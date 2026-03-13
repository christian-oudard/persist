"""Unit and integration tests for persist core logic."""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))
import persist

from helpers import (
    make_project, read_db, read_session, read_pid, session_for_pid,
    write_session,
    run_main, run_start, run_status, run_hook, run_persist,
    make_stop_event, make_pretooluse_event, DEFAULT_PID,
)


# --- Unit tests: parse_limit ---

class TestParseLimit:
    def test_iterations(self):
        total, deadline = persist.parse_limit("5")
        assert total == 5
        assert deadline is None

    def test_max_iterations(self):
        total, deadline = persist.parse_limit("999")
        assert total == 999
        assert deadline is None

    def test_hours(self):
        before = time.time()
        total, deadline = persist.parse_limit("2h")
        assert total is None
        assert before + 7200 <= deadline <= time.time() + 7200

    def test_minutes(self):
        before = time.time()
        total, deadline = persist.parse_limit("30m")
        assert total is None
        assert before + 1800 <= deadline <= time.time() + 1800

    def test_military_time(self):
        total, deadline = persist.parse_limit("1400")
        assert total is None
        assert deadline is not None

    def test_colon_time(self):
        total, deadline = persist.parse_limit("14:00")
        assert total is None
        assert deadline is not None

    def test_pm(self):
        total, deadline = persist.parse_limit("2pm")
        assert total is None
        assert deadline is not None

    def test_am(self):
        total, deadline = persist.parse_limit("11am")
        assert total is None
        assert deadline is not None

    def test_invalid_military_time(self):
        import pytest
        with pytest.raises(ValueError):
            persist.parse_limit("2500")

    def test_zero_iterations(self):
        import pytest
        with pytest.raises(ValueError):
            persist.parse_limit("0")


# --- Unit tests: is_expired ---

class TestIsExpired:
    def test_iteration_not_expired(self):
        assert persist.is_expired({"iteration": 3, "total": 5}) is None

    def test_iteration_expired(self):
        assert persist.is_expired({"iteration": 6, "total": 5}) == 'iterations'

    def test_deadline_not_expired(self):
        assert persist.is_expired({"deadline": time.time() + 3600}) is None

    def test_deadline_expired(self):
        assert persist.is_expired({"deadline": time.time() - 1}) == 'deadline'

    def test_no_limits(self):
        assert persist.is_expired({"iteration": 1}) is None


# --- Unit tests: find_keyword ---

class TestFindKeyword:
    def test_task_complete(self):
        assert persist.find_keyword("blah TASK_COMPLETE blah") == "TASK_COMPLETE"

    def test_review_okay(self):
        assert persist.find_keyword("REVIEW_OKAY") == "REVIEW_OKAY"

    def test_review_incomplete(self):
        assert persist.find_keyword("some text REVIEW_INCOMPLETE more") == "REVIEW_INCOMPLETE"

    def test_no_keyword(self):
        assert persist.find_keyword("just normal text") is None

    def test_empty(self):
        assert persist.find_keyword("") is None

    def test_priority_task_complete_first(self):
        assert persist.find_keyword("TASK_COMPLETE REVIEW_OKAY") == "TASK_COMPLETE"


# --- Integration tests: start() ---

class TestStart:
    def test_basic_start(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        result = run_start(proj, "5 Fix the bug")
        assert result.returncode == 0
        state = session_for_pid(dot_claude, DEFAULT_PID)
        assert state["iteration"] == 0
        assert state["prompt"] == "Fix the bug"
        assert state["total"] == 5

    def test_start_outputs_work_prompt(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        result = run_start(proj, "3 Do stuff")
        assert "Iteration 1" in result.stdout
        assert "Do stuff" in result.stdout

    def test_start_creates_nonce_key(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        run_start(proj, "5 task")
        key = read_pid(dot_claude, DEFAULT_PID)
        assert key is not None
        assert len(key) == 12  # hex nonce

    def test_multiline_task(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        run_start(proj, "3 Fix the bug.\nAlso update tests.")
        assert session_for_pid(dot_claude, DEFAULT_PID)["prompt"] == "Fix the bug.\nAlso update tests."

    def test_curly_braces(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        run_start(proj, "2 Fix the {name} field")
        assert session_for_pid(dot_claude, DEFAULT_PID)["prompt"] == "Fix the {name} field"

    def test_shell_metacharacters(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        run_start(proj, "1 echo $HOME && rm -rf /; don't")
        assert session_for_pid(dot_claude, DEFAULT_PID)["prompt"] == "echo $HOME && rm -rf /; don't"

    def test_quotes(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        run_start(proj, """2 Fix the "parser" and it's 'edge cases'""")
        assert session_for_pid(dot_claude, DEFAULT_PID)["prompt"] == """Fix the "parser" and it's 'edge cases'"""

    def test_no_project_root(self, tmp_path):
        result = run_start(tmp_path, "3 Do stuff")
        assert result.returncode == 1
        assert "Not in a project" in result.stderr

    def test_auto_creates_dot_claude(self, tmp_path):
        (tmp_path / ".git").mkdir()
        result = run_start(tmp_path, "3 Do stuff")
        assert result.returncode == 0
        assert (tmp_path / ".claude").is_dir()

    def test_empty_stdin(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        result = run_start(proj, "")
        assert result.returncode == 1
        assert "Usage" in result.stderr

    def test_missing_task(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        result = run_start(proj, "5")
        assert result.returncode == 1
        assert "Usage" in result.stderr

    def test_no_overwrite_active(self, tmp_path):
        """start() is a no-op if a session is already active."""
        proj, dot_claude = make_project(tmp_path)
        write_session(dot_claude, DEFAULT_PID, "old-key", 2, "old task", total=5)
        run_start(proj, "10 New task")
        assert session_for_pid(dot_claude, DEFAULT_PID)["prompt"] == "old task"

    def test_time_limit_start(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        result = run_start(proj, "2h Fix the bug")
        assert result.returncode == 0
        data = session_for_pid(dot_claude, DEFAULT_PID)
        assert data["prompt"] == "Fix the bug"
        assert data["total"] is None
        assert data["deadline"] is not None
        assert data["deadline"] > time.time()

    def test_status_active(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_session(dot_claude, DEFAULT_PID, "key-1", 3, "Fix the bug", 5)
        result = run_status(proj)
        assert result.returncode == 0
        assert "Fix the bug" in result.stdout

    def test_status_inactive(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        result = run_status(proj)
        assert result.returncode == 0
        assert "No active session" in result.stdout


# --- Integration tests: PreToolUse association ---

class TestPreToolUseAssociation:
    def test_associates_pid_with_session_id(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        run_start(proj, "5 task")
        nonce = read_pid(dot_claude, DEFAULT_PID)
        assert nonce is not None

        # PreToolUse fires → associates nonce with real session_id
        run_hook(proj, make_pretooluse_event("csid-abc"))
        assert read_pid(dot_claude, DEFAULT_PID) == "csid-abc"
        # Session migrated from nonce to session_id
        assert read_session(dot_claude, nonce) is None
        assert read_session(dot_claude, "csid-abc")["prompt"] == "task"

    def test_no_session_no_association(self, tmp_path):
        """PreToolUse with no active session is a no-op."""
        proj, dot_claude = make_project(tmp_path)
        run_hook(proj, make_pretooluse_event("csid-abc"))
        assert read_db(dot_claude) is None

    def test_already_associated(self, tmp_path):
        """PreToolUse is idempotent after association."""
        proj, dot_claude = make_project(tmp_path)
        write_session(dot_claude, DEFAULT_PID, "csid-abc", 2, "task", 5)
        run_hook(proj, make_pretooluse_event("csid-abc"))
        assert read_session(dot_claude, "csid-abc")["iteration"] == 2


# --- Integration tests: hook state machine ---

class TestHookStateMachine:
    def test_normal_continuation(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_session(dot_claude, DEFAULT_PID, "key-1", 1, "Do the thing", 5)

        decision = run_hook(proj, make_stop_event("Made some progress."))

        assert decision["decision"] == "block"
        assert read_session(dot_claude, "key-1")["iteration"] == 2

    def test_task_complete_triggers_verification(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_session(dot_claude, DEFAULT_PID, "key-1", 2, "Build feature X", 5)

        decision = run_hook(proj, make_stop_event("Done! TASK_COMPLETE"))

        assert decision["decision"] == "block"
        assert "Verification" in decision["reason"]

    def test_review_okay_ends_session(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_session(dot_claude, DEFAULT_PID, "key-1", 3, "Build feature X", 5)

        decision = run_hook(proj, make_stop_event("Everything looks good. REVIEW_OKAY"))

        assert decision["decision"] == "block"
        assert "verified" in decision["reason"].lower()
        assert read_session(dot_claude, "key-1") is None

    def test_review_incomplete_continues(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_session(dot_claude, DEFAULT_PID, "key-1", 3, "Build feature X", 5)

        decision = run_hook(proj, make_stop_event("Found a bug. REVIEW_INCOMPLETE"))

        assert decision["decision"] == "block"
        assert read_session(dot_claude, "key-1")["iteration"] == 4

    def test_iterations_exhausted(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_session(dot_claude, DEFAULT_PID, "key-1", 3, "Do stuff", 3)

        decision = run_hook(proj, make_stop_event("Still working..."))

        assert "exhausted" in decision["reason"].lower()
        assert read_session(dot_claude, "key-1") is None

    def test_no_state_silent(self, tmp_path):
        proj, _ = make_project(tmp_path)
        decision = run_hook(proj, make_stop_event("Hello"))
        assert decision is None

    def test_non_stop_event_ignored(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_session(dot_claude, DEFAULT_PID, "key-1", 3, "task", 5)

        decision = run_hook(proj, {"hook_event_name": "NotStop", "last_assistant_message": ""})
        assert decision is None
        assert read_session(dot_claude, "key-1")["iteration"] == 3

    def test_curly_braces_in_prompt(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_session(dot_claude, DEFAULT_PID, "key-1", 2, "Fix the {name} field", 5)
        decision = run_hook(proj, make_stop_event("Working on it."))
        assert "Fix the {name} field" in decision["reason"]

    def test_curly_braces_in_verification(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_session(dot_claude, DEFAULT_PID, "key-1", 2, "Update {foo} and {bar}", 5)
        decision = run_hook(proj, make_stop_event("Done! TASK_COMPLETE"))
        assert "Update {foo} and {bar}" in decision["reason"]

    def test_multiline_prompt_in_hook(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        task = "Step 1: fix parsing.\nStep 2: add tests.\nStep 3: deploy."
        write_session(dot_claude, DEFAULT_PID, "key-1", 2, task, 5)
        decision = run_hook(proj, make_stop_event("Progress made."))
        assert task in decision["reason"]

    def test_full_lifecycle(self, tmp_path):
        """Full start -> associate -> hook -> ... -> done."""
        proj, dot_claude = make_project(tmp_path)

        # 1. Start: writes session under nonce
        result = run_start(proj, "5 Create hello.txt")
        assert result.returncode == 0
        nonce = read_pid(dot_claude, DEFAULT_PID)
        assert session_for_pid(dot_claude, DEFAULT_PID)["iteration"] == 0

        # 2. PreToolUse: associates nonce → csid-1
        run_hook(proj, make_pretooluse_event("csid-1"))
        assert read_pid(dot_claude, DEFAULT_PID) == "csid-1"

        # 3. First Stop: iteration 1
        d = run_hook(proj, make_stop_event("Starting work."))
        assert d["decision"] == "block"
        assert read_session(dot_claude, "csid-1")["iteration"] == 1

        # 4. Second Stop: iteration 2
        d = run_hook(proj, make_stop_event("Created the file."))
        assert read_session(dot_claude, "csid-1")["iteration"] == 2

        # 5. TASK_COMPLETE -> verification
        d = run_hook(proj, make_stop_event("All done. TASK_COMPLETE"))
        assert "Verification" in d["reason"]

        # 6. REVIEW_OKAY -> session ends
        d = run_hook(proj, make_stop_event("Verified. REVIEW_OKAY"))
        assert "verified" in d["reason"].lower()
        assert read_session(dot_claude, "csid-1") is None

    def test_deadline_expired_ends_session(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_session(dot_claude, DEFAULT_PID, "key-1", 2, "Do stuff",
                      deadline=time.time() - 1)
        decision = run_hook(proj, make_stop_event("Still working..."))
        assert "time limit" in decision["reason"].lower()
        assert read_session(dot_claude, "key-1") is None

    def test_deadline_not_expired_continues(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_session(dot_claude, DEFAULT_PID, "key-1", 2, "Do stuff",
                      deadline=time.time() + 3600)
        decision = run_hook(proj, make_stop_event("Making progress."))
        assert "Iteration" in decision["reason"]
        assert read_session(dot_claude, "key-1")["iteration"] == 3


# --- Session isolation tests ---

class TestSessionIsolation:
    def test_different_sessions_coexist(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_session(dot_claude, "pid-A", "csid-A", 2, "task A", 5)
        write_session(dot_claude, "pid-B", "csid-B", 1, "task B", 3)

        run_hook(proj, make_stop_event("progress"), pid="pid-A")
        assert read_session(dot_claude, "csid-A")["iteration"] == 3
        assert read_session(dot_claude, "csid-B")["iteration"] == 1

    def test_other_session_untouched(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_session(dot_claude, "pid-A", "csid-A", 2, "task", 5)
        # pid-B has no session — hook is silent
        decision = run_hook(proj, make_stop_event("progress"), pid="pid-B")
        assert decision is None
        assert read_session(dot_claude, "csid-A")["iteration"] == 2

    def test_session_end_preserves_other(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_session(dot_claude, "pid-A", "csid-A", 3, "task A", 3)
        write_session(dot_claude, "pid-B", "csid-B", 1, "task B", 5)

        decision = run_hook(proj, make_stop_event("done"), pid="pid-A")
        assert "exhausted" in decision["reason"].lower()
        assert read_session(dot_claude, "csid-A") is None
        assert read_session(dot_claude, "csid-B")["iteration"] == 1

    def test_start_different_sessions(self, tmp_path):
        """Two sessions can start independently."""
        proj, dot_claude = make_project(tmp_path)
        run_start(proj, "5 task A", pid="pid-A")
        run_start(proj, "3 task B", pid="pid-B")
        assert session_for_pid(dot_claude, "pid-A")["prompt"] == "task A"
        assert session_for_pid(dot_claude, "pid-B")["prompt"] == "task B"

    def test_continue_after_restart(self, tmp_path):
        """--continue spawns new PID but same session_id survives."""
        proj, dot_claude = make_project(tmp_path)

        # Original: pid-1, associated with csid-1
        write_session(dot_claude, "pid-1", "csid-1", 2, "Build feature", 5)

        # After restart: new pid-2, Stop hook with same session_id
        decision = run_hook(proj, make_stop_event("More progress.", session_id="csid-1"),
                            pid="pid-2")
        assert decision["decision"] == "block"
        # Session now accessible via pid-2
        assert read_session(dot_claude, "csid-1")["iteration"] == 3

    def test_continue_before_any_stop(self, tmp_path):
        """Kill and --continue before any Stop hook fires.

        start() creates a nonce. PreToolUse associates it with session_id.
        After restart, Stop hook finds it by session_id.
        """
        proj, dot_claude = make_project(tmp_path)

        # pid-1: start creates nonce
        run_start(proj, "5 Build feature", pid="pid-1")
        nonce = read_pid(dot_claude, "pid-1")

        # PreToolUse fires on pid-1: associates nonce → csid-1
        run_hook(proj, make_pretooluse_event("csid-1"), pid="pid-1")
        assert read_session(dot_claude, "csid-1")["iteration"] == 0

        # Kill, --continue with pid-2. Stop hook finds csid-1.
        decision = run_hook(proj, make_stop_event("Started work.", session_id="csid-1"),
                            pid="pid-2")
        assert decision["decision"] == "block"
        assert read_session(dot_claude, "csid-1")["iteration"] == 1
