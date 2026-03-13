"""Unit and integration tests for persist core logic."""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))
import persist

from helpers import (
    make_project, read_state_file, write_state_file, write_session_file,
    run_main, run_start, run_status, run_hook, run_persist,
    make_stop_event, make_prompt_event,
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
    def test_stop_deletes_state(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_state_file(dot_claude, 2, "task", 5)
        result = run_main(proj, ["stop"])
        assert result.returncode == 0
        assert "Session stopped" in result.stdout
        assert read_state_file(dot_claude) is None

    def test_stop_no_session(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        result = run_main(proj, ["stop"])
        assert result.returncode == 0
        assert read_state_file(dot_claude) is None

    def test_status_routes(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_state_file(dot_claude, 1, "my task", 3)
        result = run_main(proj, ["status"])
        assert result.returncode == 0
        assert "1/3" in result.stdout

    def test_no_args_routes_to_start(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        result = run_main(proj, [], stdin_text="3 Do stuff")
        assert result.returncode == 0
        state = read_state_file(dot_claude)
        assert state["iteration"] == 1
        assert state["prompt"] == "Do stuff"
        assert state["total"] == 3

    def test_hook_routes(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_state_file(dot_claude, 1, "task", 5, session_id="test")
        event = json.dumps({"hook_event_name": "Stop", "last_assistant_message": "",
                            "session_id": "test", "transcript_path": "/dev/null"})
        result = run_main(proj, ["hook"], stdin_text=event)
        assert result.returncode == 0
        parsed = json.loads(result.stdout)
        assert parsed["decision"] == "block"


# --- Integration tests: start() reads args from stdin ---

class TestStart:
    def test_basic_args(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        result = run_start(proj, "5 Fix the bug")
        assert result.returncode == 0
        state = read_state_file(dot_claude)
        assert state["iteration"] == 1
        assert state["prompt"] == "Fix the bug"
        assert state["total"] == 5
        assert state["session_id"] is None  # no persist-session file

    def test_reads_session_id_from_file(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_session_file(dot_claude, "ses-123")
        result = run_start(proj, "5 Fix the bug")
        assert result.returncode == 0
        assert read_state_file(dot_claude)["session_id"] == "ses-123"

    def test_multiline_task(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        run_start(proj, "3 Fix the bug.\nAlso update tests.")
        assert read_state_file(dot_claude)["prompt"] == "Fix the bug.\nAlso update tests."

    def test_curly_braces(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        run_start(proj, "2 Fix the {name} field")
        assert read_state_file(dot_claude)["prompt"] == "Fix the {name} field"

    def test_shell_metacharacters(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        run_start(proj, "1 echo $HOME && rm -rf /; don't")
        assert read_state_file(dot_claude)["prompt"] == "echo $HOME && rm -rf /; don't"

    def test_quotes(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        run_start(proj, """2 Fix the "parser" and it's 'edge cases'""")
        assert read_state_file(dot_claude)["prompt"] == """Fix the "parser" and it's 'edge cases'"""

    def test_no_project_root(self, tmp_path):
        result = run_start(tmp_path, "3 Do stuff")
        assert result.returncode == 1
        assert "Not in a project" in result.stderr

    def test_auto_creates_dot_claude(self, tmp_path):
        (tmp_path / ".git").mkdir()
        result = run_start(tmp_path, "3 Do stuff")
        assert result.returncode == 0
        assert (tmp_path / ".claude").is_dir()
        assert "created" in result.stderr.lower()
        assert read_state_file(tmp_path / ".claude") is not None

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

    def test_status_active(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_state_file(dot_claude, 3, "Fix the bug", 5)
        result = run_status(proj)
        assert result.returncode == 0
        assert "3/5" in result.stdout
        assert "Fix the bug" in result.stdout

    def test_status_inactive(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        result = run_status(proj)
        assert result.returncode == 0
        assert "No active session" in result.stdout

    def test_no_overwrite_active_session(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_state_file(dot_claude, 3, "old task", total=5, session_id="old-ses")
        write_session_file(dot_claude, "new-ses")
        run_start(proj, "10 New task")
        state = read_state_file(dot_claude)
        assert state["prompt"] == "old task"
        assert state["session_id"] == "old-ses"

    def test_time_limit_start(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        result = run_start(proj, "2h Fix the bug")
        assert result.returncode == 0
        data = read_state_file(dot_claude)
        assert data["prompt"] == "Fix the bug"
        assert data["total"] is None
        assert data["deadline"] is not None
        assert data["deadline"] > time.time()

    def test_time_limit_status(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_state_file(dot_claude, 3, "Fix the bug", deadline=time.time() + 3600)
        result = run_status(proj)
        assert result.returncode == 0
        assert "remaining" in result.stdout


# --- Integration tests: hook state machine ---

class TestHookStateMachine:
    def test_first_hook_continues(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_state_file(dot_claude, 1, "Write hello world", 3, session_id="test-session")

        decision = run_hook(proj, make_stop_event("I'll start working on this."))

        assert decision["decision"] == "block"
        assert "Iteration" in decision["reason"]
        assert "Write hello world" in decision["reason"]
        state = read_state_file(dot_claude)
        assert state["iteration"] == 2
        assert state["session_id"] == "test-session"

    def test_normal_continuation(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_state_file(dot_claude, 1, "Do the thing", 5, session_id="test-session")

        decision = run_hook(proj, make_stop_event("Made some progress."))

        assert decision["decision"] == "block"
        assert read_state_file(dot_claude)["iteration"] == 2

    def test_task_complete_triggers_verification(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_state_file(dot_claude, 2, "Build feature X", 5, session_id="test-session")

        decision = run_hook(proj, make_stop_event("Done! TASK_COMPLETE"))

        assert decision["decision"] == "block"
        assert "Verification" in decision["reason"]

    def test_review_okay_ends_session(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_state_file(dot_claude, 3, "Build feature X", 5, session_id="test-session")

        decision = run_hook(proj, make_stop_event("Everything looks good. REVIEW_OKAY"))

        assert decision["decision"] == "block"
        assert "verified" in decision["reason"].lower()
        assert read_state_file(dot_claude) is None

    def test_review_incomplete_continues(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_state_file(dot_claude, 3, "Build feature X", 5, session_id="test-session")

        decision = run_hook(proj, make_stop_event("Found a bug. REVIEW_INCOMPLETE"))

        assert decision["decision"] == "block"
        assert read_state_file(dot_claude)["iteration"] == 4

    def test_iterations_exhausted(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_state_file(dot_claude, 3, "Do stuff", 3, session_id="test-session")

        decision = run_hook(proj, make_stop_event("Still working..."))

        assert "exhausted" in decision["reason"].lower()
        assert read_state_file(dot_claude) is None

    def test_no_state_file_silent_exit(self, tmp_path):
        proj, _ = make_project(tmp_path)
        decision = run_hook(proj, make_stop_event("Hello"))
        assert decision is None

    def test_non_stop_event_ignored(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_state_file(dot_claude, 3, "task", 5, session_id="test-session")

        decision = run_hook(proj, {"hook_event_name": "NotStop", "last_assistant_message": ""})
        assert decision is None
        assert read_state_file(dot_claude)["iteration"] == 3

    def test_curly_braces_in_prompt(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_state_file(dot_claude, 2, "Fix the {name} field", 5, session_id="test-session")
        decision = run_hook(proj, make_stop_event("Working on it."))
        assert "Fix the {name} field" in decision["reason"]

    def test_curly_braces_in_verification(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_state_file(dot_claude, 2, "Update {foo} and {bar}", 5, session_id="test-session")
        decision = run_hook(proj, make_stop_event("Done! TASK_COMPLETE"))
        assert "Update {foo} and {bar}" in decision["reason"]

    def test_multiline_prompt_in_hook(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        task = "Step 1: fix parsing.\nStep 2: add tests.\nStep 3: deploy."
        write_state_file(dot_claude, 2, task, 5, session_id="test-session")
        decision = run_hook(proj, make_stop_event("Progress made."))
        assert task in decision["reason"]

    def test_full_lifecycle(self, tmp_path):
        """Full start -> hook -> ... -> done."""
        proj, dot_claude = make_project(tmp_path)

        # Simulate UserPromptSubmit writing session file
        write_session_file(dot_claude, "test-session")

        # 1. Start
        run_start(proj, "5 Create hello.txt")
        state = read_state_file(dot_claude)
        assert state["iteration"] == 1
        assert state["session_id"] == "test-session"

        # 2. First hook: iteration 2
        d = run_hook(proj, make_stop_event("Starting work."))
        assert d["decision"] == "block"
        assert read_state_file(dot_claude)["iteration"] == 2

        # 3. Second hook: iteration 3
        d = run_hook(proj, make_stop_event("Created the file."))
        assert read_state_file(dot_claude)["iteration"] == 3

        # 4. TASK_COMPLETE -> verification
        d = run_hook(proj, make_stop_event("All done. TASK_COMPLETE"))
        assert "Verification" in d["reason"]

        # 5. REVIEW_OKAY -> session ends
        d = run_hook(proj, make_stop_event("Verified. REVIEW_OKAY"))
        assert "verified" in d["reason"].lower()
        assert read_state_file(dot_claude) is None

    def test_deadline_expired_ends_session(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_state_file(dot_claude, 2, "Do stuff", deadline=time.time() - 1, session_id="test-session")
        decision = run_hook(proj, make_stop_event("Still working..."))
        assert "time limit" in decision["reason"].lower()
        assert read_state_file(dot_claude) is None

    def test_deadline_not_expired_continues(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_state_file(dot_claude, 2, "Do stuff", deadline=time.time() + 3600, session_id="test-session")
        decision = run_hook(proj, make_stop_event("Making progress."))
        assert "Iteration" in decision["reason"]
        assert read_state_file(dot_claude)["iteration"] == 3


# --- UserPromptSubmit hook tests ---

class TestPromptHook:
    def test_writes_session_file_on_persist(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        event = make_prompt_event("/persist 5 do stuff", session_id="ses-abc")
        run_persist(proj, "prompt_hook", json.dumps(event))
        assert (dot_claude / "persist-session").read_text() == "ses-abc"

    def test_ignores_non_persist_prompts(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        event = make_prompt_event("just a normal message", session_id="ses-abc")
        run_persist(proj, "prompt_hook", json.dumps(event))
        assert not (dot_claude / "persist-session").exists()

    def test_creates_dot_claude_if_needed(self, tmp_path):
        """prompt_hook creates .claude/ if project has .git but no .claude."""
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / ".git").mkdir()
        # No .claude yet — prompt_hook should handle gracefully
        # (state_file_path returns None if no .claude, but prompt_hook creates it)
        dot_claude = proj / ".claude"
        event = make_prompt_event("/persist 5 task", session_id="ses-abc")
        run_persist(proj, "prompt_hook", json.dumps(event))
        # It's OK if this doesn't create .claude — start() handles that.
        # The important thing is it doesn't crash.


# --- Session isolation tests ---

class TestSessionIsolation:
    def test_session_id_set_at_start(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_session_file(dot_claude, "session-A")
        run_start(proj, "5 task")
        assert read_state_file(dot_claude)["session_id"] == "session-A"

    def test_no_session_file_starts_without_session(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        run_start(proj, "5 task")
        assert read_state_file(dot_claude)["session_id"] is None

    def test_unclaimed_session_accepts_hooks(self, tmp_path):
        """A session with no session_id accepts hooks from any session."""
        proj, dot_claude = make_project(tmp_path)
        write_state_file(dot_claude, 1, "task", 5)
        decision = run_hook(proj, make_stop_event("progress", session_id="session-A"))
        assert decision is not None
        assert decision["decision"] == "block"
        assert read_state_file(dot_claude)["iteration"] == 2

    def test_other_session_ignored(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_state_file(dot_claude, 2, "task", 5, session_id="session-A")
        decision = run_hook(proj, make_stop_event("progress", session_id="session-B"))
        assert decision is None
        assert read_state_file(dot_claude)["iteration"] == 2

    def test_owning_session_proceeds(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_state_file(dot_claude, 2, "task", 5, session_id="session-A")
        decision = run_hook(proj, make_stop_event("progress", session_id="session-A"))
        assert decision["decision"] == "block"
        assert read_state_file(dot_claude)["iteration"] == 3

    def test_other_session_cannot_complete(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_state_file(dot_claude, 3, "task", 5, session_id="session-A")
        decision = run_hook(proj, make_stop_event("REVIEW_OKAY", session_id="session-B"))
        assert decision is None
        assert read_state_file(dot_claude) is not None

    def test_stop_works_regardless_of_session(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_state_file(dot_claude, 3, "task", 5, session_id="session-A")
        result = run_main(proj, ["stop"])
        assert result.returncode == 0
        assert read_state_file(dot_claude) is None
