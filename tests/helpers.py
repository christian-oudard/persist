"""Shared test helpers for persist."""

import json
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent

# Default session ID for tests (simulates claude_pid() via env override)
DEFAULT_PID = "test-pid"


def make_project(tmp_path):
    """Create a minimal project directory with .claude/."""
    dot_claude = tmp_path / ".claude"
    dot_claude.mkdir()
    return tmp_path, dot_claude


def read_db(dot_claude):
    """Read the raw persist.json database."""
    path = dot_claude / "persist.json"
    if path.exists():
        return json.loads(path.read_text())
    return None


def read_session(dot_claude, key):
    """Read a specific session's state."""
    db = read_db(dot_claude)
    if db:
        return db.get('sessions', {}).get(key)
    return None


def read_pid(dot_claude, pid):
    """Read the session key associated with a PID."""
    db = read_db(dot_claude)
    if db:
        return db.get('pids', {}).get(pid)
    return None


def session_for_pid(dot_claude, pid):
    """Get the session state for a given PID."""
    key = read_pid(dot_claude, pid)
    if key:
        return read_session(dot_claude, key)
    return None


def write_session(dot_claude, pid, key, iteration, prompt, total=None,
                  deadline=None):
    """Write a session entry to persist.json with PID association."""
    path = dot_claude / "persist.json"
    db = {'pids': {}, 'sessions': {}}
    if path.exists():
        db = json.loads(path.read_text())
        db.setdefault('pids', {})
        db.setdefault('sessions', {})
    db['pids'][pid] = key
    db['sessions'][key] = {
        "iteration": iteration,
        "prompt": prompt,
        "total": total,
        "deadline": deadline,
    }
    path.write_text(json.dumps(db))


def _make_env(pid=None):
    """Build subprocess env with PERSIST_SESSION_ID set."""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT)
    env["PERSIST_SESSION_ID"] = pid or DEFAULT_PID
    return env


def run_persist(cwd, func, stdin_text, pid=None):
    """Run a persist function as a subprocess with piped stdin."""
    return subprocess.run(
        [sys.executable, "-c", f"import persist; persist.{func}()"],
        input=stdin_text,
        capture_output=True,
        text=True,
        cwd=str(cwd),
        env=_make_env(pid),
    )


def run_main(cwd, args, stdin_text="", pid=None):
    """Run persist.main() as a subprocess with given argv and stdin."""
    argv = ["persist"] + args
    code = f"import sys; sys.argv = {argv!r}; import persist; persist.main()"
    return subprocess.run(
        [sys.executable, "-c", code],
        input=stdin_text, capture_output=True, text=True,
        cwd=str(cwd), env=_make_env(pid),
    )


def run_start(cwd, stdin_text, pid=None):
    return run_persist(cwd, "start", stdin_text, pid)


def run_status(cwd, pid=None):
    return run_persist(cwd, "status", "", pid)


def run_hook(cwd, event, pid=None):
    """Run persist hook and return parsed JSON output (or None)."""
    result = run_persist(cwd, "hook", json.dumps(event), pid)
    if result.returncode != 0 and result.stderr:
        raise RuntimeError(f"Hook failed: {result.stderr}")
    stdout = result.stdout.strip()
    if stdout:
        return json.loads(stdout)
    return None


def make_stop_event(last_msg="", session_id=None):
    event = {
        "hook_event_name": "Stop",
        "transcript_path": "/dev/null",
        "last_assistant_message": last_msg,
    }
    if session_id:
        event["session_id"] = session_id
    return event


def make_pretooluse_event(session_id, tool_name="Bash"):
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": tool_name,
        "session_id": session_id,
    }
