"""End-to-end tests for persist using PTY-driven claude --model haiku.

Spawns real Claude Code instances in pseudo-terminals, types commands,
sends Escape to interrupt, and verifies hook behavior via log files.

Requires: claude CLI, valid API credentials, network access.
Run with:  pytest tests/test_e2e.py -v -s
Skip with: SKIP_E2E=1 pytest
"""

import json
import os
import pty
import re
import select
import signal
import shutil
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
ESC = b'\x1b'

pytestmark = pytest.mark.skipif(
    os.environ.get("SKIP_E2E") or shutil.which("claude") is None,
    reason="E2E tests require claude CLI (set SKIP_E2E=1 to skip)",
)


# --- PTY helpers ---

def pty_read_until(fd, pattern, timeout=60):
    """Read from PTY fd until pattern found or timeout. Returns (data, found)."""
    buf = b''
    deadline = time.monotonic() + timeout
    if isinstance(pattern, str):
        pattern = pattern.encode()
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        r, _, _ = select.select([fd], [], [], min(remaining, 0.1))
        if r:
            try:
                data = os.read(fd, 4096)
                if not data:
                    break
                buf += data
                if pattern in buf:
                    return buf, True
            except OSError:
                break
    return buf, False


def pty_drain(fd, timeout=2.0):
    """Read all available data from PTY."""
    buf = b''
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r, _, _ = select.select([fd], [], [], min(deadline - time.monotonic(), 0.1))
        if r:
            try:
                data = os.read(fd, 4096)
                if not data:
                    break
                buf += data
            except OSError:
                break
    return buf


def strip_ansi(text):
    """Remove ANSI escape sequences from terminal output."""
    text = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', text)
    text = re.sub(r'\x1b\][^\x07]*\x07', '', text)
    return text


# Input prompt marker: Claude Code shows "shift+tab" hint when ready for input.
INPUT_READY = b"shift+tab"


# --- Test infrastructure ---

class ClaudePTY:
    """Drives a Claude Code instance through a pseudo-terminal."""

    def __init__(self, tmp_path):
        self.project_dir = tmp_path / "project"
        self.project_dir.mkdir()
        dot_claude = self.project_dir / ".claude"
        dot_claude.mkdir()

        skills_dst = dot_claude / "skills"
        shutil.copytree(PROJECT_ROOT / "skills", skills_dst)

        self.hook_log = tmp_path / "hook_calls.jsonl"
        self.state_json = dot_claude / "persist.json"
        self._setup_hook(tmp_path)

        self.pid = None
        self.fd = None

    def _setup_hook(self, tmp_path):
        # Wrapper bin dir: shadows the installed `persist` binary so the
        # /persist slash command invokes source-tree code, not whatever stale
        # build is in PATH. Also used by the Stop hook.
        self.bin_dir = tmp_path / "bin"
        self.bin_dir.mkdir()
        persist_wrapper = self.bin_dir / "persist"
        persist_wrapper.write_text(f"""\
#!/bin/bash
exec env PYTHONPATH={PROJECT_ROOT} python3 -c 'import sys; from persist import main; sys.exit(main() or 0)' "$@"
""")
        persist_wrapper.chmod(0o755)

        hook_wrapper = tmp_path / "hook_wrapper.sh"
        self.settings_file = tmp_path / "settings.json"

        hook_wrapper.write_text(f"""\
#!/bin/bash
EVENT=$(cat)
echo "$EVENT" >> {self.hook_log}
cd {self.project_dir}
echo "$EVENT" | PATH={self.bin_dir}:$PATH persist hook
""")
        hook_wrapper.chmod(0o755)

        self.settings_file.write_text(json.dumps({
            "hooks": {
                "Stop": [{
                    "matcher": "",
                    "hooks": [{"type": "command", "command": str(hook_wrapper)}],
                }],
            },
        }))

    def spawn(self):
        """Spawn claude in a PTY. Must call cleanup() when done."""
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
        env["TERM"] = "xterm-256color"
        env["COLUMNS"] = "120"
        env["LINES"] = "40"
        # Prepend wrapper bin so /persist invokes source-tree code.
        env["PATH"] = f"{self.bin_dir}:{env.get('PATH', '')}"

        pid, fd = pty.fork()
        if pid == 0:
            os.chdir(str(self.project_dir))
            os.execvpe("claude", [
                "claude", "--model", "haiku",
                "--dangerously-skip-permissions",
                "--setting-sources", "project,local",
                "--settings", str(self.settings_file),
            ], env)

        self.pid = pid
        self.fd = fd

        # Handle the folder trust dialog
        _, found = pty_read_until(fd, b"trust", timeout=20)
        assert found, "Claude failed to show trust dialog"
        os.write(fd, b"\r")  # Press Enter to confirm trust

        # Wait for input prompt
        _, found = pty_read_until(fd, INPUT_READY, timeout=20)
        assert found, "Claude failed to reach input prompt"
        pty_drain(fd, timeout=1)

    def submit(self, text):
        """Type text and press Enter. Waits for TUI to register the input."""
        if isinstance(text, str):
            text = text.encode()
        os.write(self.fd, text)
        # Wait for the TUI to echo back at least the first few characters,
        # confirming it registered the input before we press Enter.
        prefix = text[:8]
        pty_read_until(self.fd, prefix, timeout=5)
        os.write(self.fd, b"\r")

    def send_escape(self):
        """Send Escape key to interrupt current turn."""
        os.write(self.fd, ESC)

    def wait_for_input_ready(self, timeout=30):
        """Wait for the input prompt to appear (TUI ready for commands)."""
        buf, found = pty_read_until(self.fd, INPUT_READY, timeout=timeout)
        pty_drain(self.fd, timeout=0.5)
        return found

    def wait_for(self, pattern, timeout=90):
        """Wait for pattern to appear in terminal output."""
        buf, found = pty_read_until(self.fd, pattern, timeout=timeout)
        return strip_ansi(buf.decode("utf-8", errors="replace")), found

    def drain(self, timeout=3.0):
        """Drain remaining output."""
        return strip_ansi(pty_drain(self.fd, timeout).decode("utf-8", errors="replace"))

    def wait_for_hook_calls(self, n, timeout=120):
        """Wait until at least n hook calls have been logged."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.count_hook_calls() >= n:
                return True
            # Drain output to keep the PTY buffer from filling up
            pty_drain(self.fd, timeout=0.5)
        return False

    def wait_for_session_end(self, timeout=180):
        """Wait until persist.json is deleted (session finished)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            pty_drain(self.fd, timeout=0.5)
            if not self.state_file_exists() and self.count_hook_calls() >= 1:
                return True
        return False

    def wait_for_exit(self, timeout=30):
        """Wait for the claude process to exit."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            pty_drain(self.fd, timeout=0.5)
            result = os.waitpid(self.pid, os.WNOHANG)
            if result[0] != 0:
                self.pid = None
                return True
        return False

    def count_hook_calls(self):
        if not self.hook_log.exists():
            return 0
        text = self.hook_log.read_text().strip()
        if not text:
            return 0
        return sum(1 for line in text.split("\n") if line.strip())

    def parse_hook_log(self):
        if not self.hook_log.exists():
            return []
        events = []
        for line in self.hook_log.read_text().strip().split("\n"):
            if line.strip():
                events.append(json.loads(line))
        return events

    def state_file_exists(self):
        return self.state_json.exists()

    def cleanup(self):
        """Kill the claude process."""
        if self.pid is not None:
            try:
                os.kill(self.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                os.waitpid(self.pid, 0)
            except ChildProcessError:
                pass
            self.pid = None


E2E_TIMEOUT = 150  # seconds per test


@pytest.fixture
def claude(tmp_path):
    """Fixture that provides a started ClaudePTY instance with a timeout."""
    prev = signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(
        TimeoutError(f"E2E test exceeded {E2E_TIMEOUT}s timeout")))
    signal.alarm(E2E_TIMEOUT)

    c = ClaudePTY(tmp_path)
    c.spawn()
    yield c

    signal.alarm(0)
    signal.signal(signal.SIGALRM, prev)
    # Try graceful exit
    try:
        c.send_escape()
        c.wait_for_input_ready(timeout=10)
        c.submit("/exit")
        c.wait_for_exit(timeout=5)
    except OSError:
        pass
    c.cleanup()


# --- Smoke test ---
# A single end-to-end canary: the core test suite already exercises the
# hook state machine in isolation. This test verifies the three things
# that can only break in the real TUI:
#   1. /persist slash command reaches start() and writes persist.json
#   2. The Stop hook actually fires and re-injects the work prompt
#   3. Iteration limit ends the session and deletes persist.json

COUNTING_TASK = (
    "Say one number per iteration, counting upward. "
    "Use number words, not digits. This task is never complete."
)


def test_smoke_two_iteration_session(claude):
    claude.submit(f"/persist 2 {COUNTING_TASK}")

    got_enough = claude.wait_for_hook_calls(2, timeout=90)
    hooks = claude.parse_hook_log()
    assert got_enough, (
        f"Expected 2+ hook calls, got {len(hooks)}. "
        f"Messages: {[h.get('last_assistant_message', '')[:40] for h in hooks]}"
    )

    claude.wait_for_session_end(timeout=30)
    assert not claude.state_file_exists(), \
        "State file should be deleted when iterations exhausted"

    for h in hooks:
        assert h["hook_event_name"] == "Stop"
        assert len(h.get("last_assistant_message", "")) > 0
