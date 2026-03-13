"""Unit and integration tests for persist core logic."""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))
import persist

from helpers import (
    make_project, read_state_file, read_session, read_pending,
    write_session, write_pending,
    run_main, run_start, run_status, run_hook, run_persist,
    make_stop_event,
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


# --- Integration tests: main() dispatch ---

class TestMain:
    def test_stop_clears_all(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_session(dot_claude, "ses-A", 2, "task", 5)
        write_pending(dot_claude, 0, "other task", 3)
        result = run_main(proj, ["stop"])
        assert result.returncode == 0
        assert "Session stopped" in result.stdout
        assert read_state_file(dot_claude) is None
        assert read_pending(dot_claude) is None

    def test_status_shows_pending(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_pending(dot_claude, 0, "my task", 3)
        result = run_status(proj)
        assert result.returncode == 0
        assert "pending" in result.stdout.lower()
        assert "my task" in result.stdout

    def test_no_args_routes_to_start(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        result = run_main(proj, [], stdin_text="3 Do stuff")
        assert result.returncode == 0
        pending = read_pending(dot_claude)
        assert pending["iteration"] == 0
        assert pending["prompt"] == "Do stuff"
        assert pending["total"] == 3

    def test_hook_activates_pending(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_pending(dot_claude, 0, "task", 5)
        event = json.dumps({"hook_event_name": "Stop", "last_assistant_message": "",
                            "session_id": "ses-A", "transcript_path": "/dev/null"})
        result = run_main(proj, ["hook"], stdin_text=event)
        assert result.returncode == 0
        parsed = json.loads(result.stdout)
        assert parsed["decision"] == "block"
        # Pending consumed, session created in persist.json
        assert read_pending(dot_claude) is None
        assert read_session(dot_claude, "ses-A")["iteration"] == 1


# --- Integration tests: start() ---

class TestStart:
    def test_basic_start(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        result = run_start(proj, "5 Fix the bug")
        assert result.returncode == 0
        pending = read_pending(dot_claude)
        assert pending["iteration"] == 0
        assert pending["prompt"] == "Fix the bug"
        assert pending["total"] == 5
        # No persist.json yet — only pending
        assert read_state_file(dot_claude) is None

    def test_multiline_task(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        run_start(proj, "3 Fix the bug.\nAlso update tests.")
        assert read_pending(dot_claude)["prompt"] == "Fix the bug.\nAlso update tests."

    def test_curly_braces(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        run_start(proj, "2 Fix the {name} field")
        assert read_pending(dot_claude)["prompt"] == "Fix the {name} field"

    def test_shell_metacharacters(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        run_start(proj, "1 echo $HOME && rm -rf /; don't")
        assert read_pending(dot_claude)["prompt"] == "echo $HOME && rm -rf /; don't"

    def test_quotes(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        run_start(proj, """2 Fix the "parser" and it's 'edge cases'""")
        assert read_pending(dot_claude)["prompt"] == """Fix the "parser" and it's 'edge cases'"""

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

    def test_no_overwrite_pending(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_pending(dot_claude, 0, "old task", total=5)
        run_start(proj, "10 New task")
        assert read_pending(dot_claude)["prompt"] == "old task"

    def test_time_limit_start(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        result = run_start(proj, "2h Fix the bug")
        assert result.returncode == 0
        data = read_pending(dot_claude)
        assert data["prompt"] == "Fix the bug"
        assert data["total"] is None
        assert data["deadline"] is not None
        assert data["deadline"] > time.time()

    def test_status_active(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_session(dot_claude, "ses-A", 3, "Fix the bug", 5)
        result = run_status(proj)
        assert result.returncode == 0
        assert "Fix the bug" in result.stdout

    def test_status_inactive(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        result = run_status(proj)
        assert result.returncode == 0
        assert "No active session" in result.stdout


# --- Integration tests: hook state machine ---

class TestHookStateMachine:
    def test_first_hook_activates_pending(self, tmp_path):
        """First stop hook promotes pending to persist.json."""
        proj, dot_claude = make_project(tmp_path)
        write_pending(dot_claude, 0, "Write hello world", 3)

        decision = run_hook(proj, make_stop_event("I'll start working.", session_id="ses-A"))

        assert decision["decision"] == "block"
        assert "Iteration" in decision["reason"]
        assert "Write hello world" in decision["reason"]
        state = read_session(dot_claude, "ses-A")
        assert state["iteration"] == 1
        assert read_pending(dot_claude) is None

    def test_normal_continuation(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_session(dot_claude, "ses-A", 1, "Do the thing", 5)

        decision = run_hook(proj, make_stop_event("Made some progress.", session_id="ses-A"))

        assert decision["decision"] == "block"
        assert read_session(dot_claude, "ses-A")["iteration"] == 2

    def test_task_complete_triggers_verification(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_session(dot_claude, "ses-A", 2, "Build feature X", 5)

        decision = run_hook(proj, make_stop_event("Done! TASK_COMPLETE", session_id="ses-A"))

        assert decision["decision"] == "block"
        assert "Verification" in decision["reason"]

    def test_review_okay_ends_session(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_session(dot_claude, "ses-A", 3, "Build feature X", 5)

        decision = run_hook(proj, make_stop_event("Everything looks good. REVIEW_OKAY", session_id="ses-A"))

        assert decision["decision"] == "block"
        assert "verified" in decision["reason"].lower()
        assert read_session(dot_claude, "ses-A") is None

    def test_review_incomplete_continues(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_session(dot_claude, "ses-A", 3, "Build feature X", 5)

        decision = run_hook(proj, make_stop_event("Found a bug. REVIEW_INCOMPLETE", session_id="ses-A"))

        assert decision["decision"] == "block"
        assert read_session(dot_claude, "ses-A")["iteration"] == 4

    def test_iterations_exhausted(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_session(dot_claude, "ses-A", 3, "Do stuff", 3)

        decision = run_hook(proj, make_stop_event("Still working...", session_id="ses-A"))

        assert "exhausted" in decision["reason"].lower()
        assert read_session(dot_claude, "ses-A") is None

    def test_no_state_no_pending_silent(self, tmp_path):
        proj, _ = make_project(tmp_path)
        decision = run_hook(proj, make_stop_event("Hello"))
        assert decision is None

    def test_no_matching_session_no_pending(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_session(dot_claude, "ses-A", 3, "task", 5)
        decision = run_hook(proj, make_stop_event("Hello", session_id="ses-B"))
        assert decision is None
        assert read_session(dot_claude, "ses-A")["iteration"] == 3

    def test_non_stop_event_ignored(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_session(dot_claude, "ses-A", 3, "task", 5)

        decision = run_hook(proj, {"hook_event_name": "NotStop", "last_assistant_message": "",
                                   "session_id": "ses-A"})
        assert decision is None
        assert read_session(dot_claude, "ses-A")["iteration"] == 3

    def test_no_session_id_in_event_ignored(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_pending(dot_claude, 0, "task", 5)
        decision = run_hook(proj, {"hook_event_name": "Stop", "last_assistant_message": "",
                                   "transcript_path": "/dev/null"})
        assert decision is None
        # Pending not consumed
        assert read_pending(dot_claude) is not None

    def test_curly_braces_in_prompt(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_session(dot_claude, "ses-A", 2, "Fix the {name} field", 5)
        decision = run_hook(proj, make_stop_event("Working on it.", session_id="ses-A"))
        assert "Fix the {name} field" in decision["reason"]

    def test_curly_braces_in_verification(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_session(dot_claude, "ses-A", 2, "Update {foo} and {bar}", 5)
        decision = run_hook(proj, make_stop_event("Done! TASK_COMPLETE", session_id="ses-A"))
        assert "Update {foo} and {bar}" in decision["reason"]

    def test_multiline_prompt_in_hook(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        task = "Step 1: fix parsing.\nStep 2: add tests.\nStep 3: deploy."
        write_session(dot_claude, "ses-A", 2, task, 5)
        decision = run_hook(proj, make_stop_event("Progress made.", session_id="ses-A"))
        assert task in decision["reason"]

    def test_full_lifecycle(self, tmp_path):
        """Full start -> hook (activate) -> hook -> ... -> done."""
        proj, dot_claude = make_project(tmp_path)

        # 1. Start (writes pending, no persist.json)
        run_start(proj, "5 Create hello.txt")
        assert read_pending(dot_claude) is not None
        assert read_state_file(dot_claude) is None

        # 2. First hook: activates pending -> ses-A in persist.json, iteration 1
        d = run_hook(proj, make_stop_event("Starting work.", session_id="ses-A"))
        assert d["decision"] == "block"
        assert read_pending(dot_claude) is None
        assert read_session(dot_claude, "ses-A")["iteration"] == 1

        # 3. Second hook: iteration 2
        d = run_hook(proj, make_stop_event("Created the file.", session_id="ses-A"))
        assert read_session(dot_claude, "ses-A")["iteration"] == 2

        # 4. TASK_COMPLETE -> verification
        d = run_hook(proj, make_stop_event("All done. TASK_COMPLETE", session_id="ses-A"))
        assert "Verification" in d["reason"]

        # 5. REVIEW_OKAY -> session ends
        d = run_hook(proj, make_stop_event("Verified. REVIEW_OKAY", session_id="ses-A"))
        assert "verified" in d["reason"].lower()
        assert read_session(dot_claude, "ses-A") is None

    def test_deadline_expired_ends_session(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_session(dot_claude, "ses-A", 2, "Do stuff",
                      deadline=time.time() - 1)
        decision = run_hook(proj, make_stop_event("Still working...", session_id="ses-A"))
        assert "time limit" in decision["reason"].lower()
        assert read_session(dot_claude, "ses-A") is None

    def test_deadline_not_expired_continues(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_session(dot_claude, "ses-A", 2, "Do stuff",
                      deadline=time.time() + 3600)
        decision = run_hook(proj, make_stop_event("Making progress.", session_id="ses-A"))
        assert "Iteration" in decision["reason"]
        assert read_session(dot_claude, "ses-A")["iteration"] == 3


# --- Session isolation tests ---

class TestSessionIsolation:
    def test_different_sessions_coexist(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_session(dot_claude, "ses-A", 2, "task A", 5)
        write_session(dot_claude, "ses-B", 1, "task B", 3)

        run_hook(proj, make_stop_event("progress", session_id="ses-A"))
        assert read_session(dot_claude, "ses-A")["iteration"] == 3
        assert read_session(dot_claude, "ses-B")["iteration"] == 1

    def test_other_session_ignored(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_session(dot_claude, "ses-A", 2, "task", 5)
        decision = run_hook(proj, make_stop_event("progress", session_id="ses-B"))
        assert decision is None
        assert read_session(dot_claude, "ses-A")["iteration"] == 2

    def test_session_end_preserves_other(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_session(dot_claude, "ses-A", 3, "task A", 3)
        write_session(dot_claude, "ses-B", 1, "task B", 5)

        decision = run_hook(proj, make_stop_event("done", session_id="ses-A"))
        assert "exhausted" in decision["reason"].lower()
        assert read_session(dot_claude, "ses-A") is None
        assert read_session(dot_claude, "ses-B")["iteration"] == 1

    def test_pending_only_claimed_by_first_session(self, tmp_path):
        """Only the first stop hook claims the pending session."""
        proj, dot_claude = make_project(tmp_path)
        write_pending(dot_claude, 0, "task", 5)

        # ses-A claims it
        run_hook(proj, make_stop_event("working", session_id="ses-A"))
        assert read_session(dot_claude, "ses-A")["iteration"] == 1
        assert read_pending(dot_claude) is None

        # ses-B gets nothing
        decision = run_hook(proj, make_stop_event("working", session_id="ses-B"))
        assert decision is None
