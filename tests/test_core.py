"""Unit and integration tests for claude_loop core logic."""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))
import claude_loop

from helpers import (
    make_project, read_loop_file, write_loop_file,
    run_main, run_start, run_status, run_hook,
    make_stop_event,
)


# --- Unit tests: parse_limit ---

class TestParseLimit:
    def test_iterations(self):
        total, deadline = claude_loop.parse_limit("5")
        assert total == 5
        assert deadline is None

    def test_max_iterations(self):
        total, deadline = claude_loop.parse_limit("999")
        assert total == 999
        assert deadline is None

    def test_hours(self):
        before = time.time()
        total, deadline = claude_loop.parse_limit("2h")
        assert total is None
        assert before + 7200 <= deadline <= time.time() + 7200

    def test_minutes(self):
        before = time.time()
        total, deadline = claude_loop.parse_limit("30m")
        assert total is None
        assert before + 1800 <= deadline <= time.time() + 1800

    def test_military_time(self):
        total, deadline = claude_loop.parse_limit("1400")
        assert total is None
        assert deadline is not None

    def test_colon_time(self):
        total, deadline = claude_loop.parse_limit("14:00")
        assert total is None
        assert deadline is not None

    def test_pm(self):
        total, deadline = claude_loop.parse_limit("2pm")
        assert total is None
        assert deadline is not None

    def test_am(self):
        total, deadline = claude_loop.parse_limit("11am")
        assert total is None
        assert deadline is not None

    def test_invalid_military_time(self):
        import pytest
        with pytest.raises(ValueError):
            claude_loop.parse_limit("2500")

    def test_zero_iterations(self):
        import pytest
        with pytest.raises(ValueError):
            claude_loop.parse_limit("0")


# --- Unit tests: is_expired ---

class TestIsExpired:
    def test_iteration_not_expired(self):
        assert claude_loop.is_expired({"iteration": 3, "total": 5}) is None

    def test_iteration_expired(self):
        assert claude_loop.is_expired({"iteration": 6, "total": 5}) == 'iterations'

    def test_deadline_not_expired(self):
        assert claude_loop.is_expired({"deadline": time.time() + 3600}) is None

    def test_deadline_expired(self):
        assert claude_loop.is_expired({"deadline": time.time() - 1}) == 'deadline'

    def test_no_limits(self):
        assert claude_loop.is_expired({"iteration": 1}) is None


# --- Unit tests: find_keyword ---

class TestFindKeyword:
    def test_task_complete(self):
        assert claude_loop.find_keyword("blah TASK_COMPLETE blah") == "TASK_COMPLETE"

    def test_review_okay(self):
        assert claude_loop.find_keyword("REVIEW_OKAY") == "REVIEW_OKAY"

    def test_review_incomplete(self):
        assert claude_loop.find_keyword("some text REVIEW_INCOMPLETE more") == "REVIEW_INCOMPLETE"

    def test_no_keyword(self):
        assert claude_loop.find_keyword("just normal text") is None

    def test_empty(self):
        assert claude_loop.find_keyword("") is None

    def test_priority_task_complete_first(self):
        # TASK_COMPLETE is checked first due to iteration order
        assert claude_loop.find_keyword("TASK_COMPLETE REVIEW_OKAY") == "TASK_COMPLETE"


# --- Integration tests: main() dispatch ---

class TestMain:
    """Test that main() routes to the correct subcommand based on sys.argv."""

    def test_stop_deletes_loop(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_loop_file(dot_claude, 2, "task", 5)
        result = run_main(proj, ["stop"])
        assert result.returncode == 0
        assert "Loop stopped" in result.stdout
        assert read_loop_file(dot_claude) is None

    def test_stop_no_loop(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        result = run_main(proj, ["stop"])
        assert result.returncode == 0
        assert read_loop_file(dot_claude) is None

    def test_status_routes(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_loop_file(dot_claude, 1, "my task", 3)
        result = run_main(proj, ["status"])
        assert result.returncode == 0
        assert "1/3" in result.stdout

    def test_no_args_routes_to_start(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        result = run_main(proj, [], stdin_text="3 Do stuff")
        assert result.returncode == 0
        assert read_loop_file(dot_claude) == {"iteration": 1, "prompt": "Do stuff", "total": 3, "deadline": None}

    def test_hook_routes(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_loop_file(dot_claude, 1, "task", 5)
        event = json.dumps({"hook_event_name": "Stop", "last_assistant_message": ""})
        result = run_main(proj, ["hook"], stdin_text=event)
        assert result.returncode == 0
        parsed = json.loads(result.stdout)
        assert parsed["decision"] == "block"


# --- Integration tests: start() reads args from stdin ---

class TestStart:
    """Test start() by piping args via stdin, as the heredoc slash command does."""

    def test_basic_args(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        result = run_start(proj, "5 Fix the bug")
        assert result.returncode == 0
        assert read_loop_file(dot_claude) == {"iteration": 1, "prompt": "Fix the bug", "total": 5, "deadline": None}

    def test_multiline_task(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        run_start(proj, "3 Fix the bug.\nAlso update tests.")
        assert read_loop_file(dot_claude)["prompt"] == "Fix the bug.\nAlso update tests."

    def test_curly_braces(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        run_start(proj, "2 Fix the {name} field")
        assert read_loop_file(dot_claude)["prompt"] == "Fix the {name} field"

    def test_shell_metacharacters(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        run_start(proj, "1 echo $HOME && rm -rf /; don't")
        assert read_loop_file(dot_claude)["prompt"] == "echo $HOME && rm -rf /; don't"

    def test_quotes(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        run_start(proj, """2 Fix the "parser" and it's 'edge cases'""")
        assert read_loop_file(dot_claude)["prompt"] == """Fix the "parser" and it's 'edge cases'"""

    def test_no_project_root(self, tmp_path):
        # No .claude and no .git — should fail.
        result = run_start(tmp_path, "3 Do stuff")
        assert result.returncode == 1
        assert "Not in a project" in result.stderr

    def test_auto_creates_dot_claude(self, tmp_path):
        # .git exists but no .claude — should auto-create .claude and print message.
        (tmp_path / ".git").mkdir()
        result = run_start(tmp_path, "3 Do stuff")
        assert result.returncode == 0
        assert (tmp_path / ".claude").is_dir()
        assert "created" in result.stderr.lower()
        assert read_loop_file(tmp_path / ".claude") is not None

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
        write_loop_file(dot_claude, 3, "Fix the bug", 5)
        result = run_status(proj)
        assert result.returncode == 0
        assert "3/5" in result.stdout
        assert "Fix the bug" in result.stdout

    def test_status_inactive(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        result = run_status(proj)
        assert result.returncode == 0
        assert "No active loop" in result.stdout

    def test_no_overwrite_active_loop(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        existing = {"iteration": 3, "prompt": "old task", "total": 5, "deadline": None}
        write_loop_file(dot_claude, 3, "old task", total=5)
        run_start(proj, "10 New task")
        assert read_loop_file(dot_claude) == existing

    def test_time_limit_start(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        result = run_start(proj, "2h Fix the bug")
        assert result.returncode == 0
        data = read_loop_file(dot_claude)
        assert data["prompt"] == "Fix the bug"
        assert data["total"] is None
        assert data["deadline"] is not None
        assert data["deadline"] > time.time()

    def test_time_limit_status(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_loop_file(dot_claude, 3, "Fix the bug", deadline=time.time() + 3600)
        result = run_status(proj)
        assert result.returncode == 0
        assert "remaining" in result.stdout


# --- Integration tests: hook state machine (fixed loop) ---

class TestHookStateMachine:
    """Test the hook by calling claude-loop hook as a subprocess with crafted events.

    This tests the full state machine including file I/O, without needing
    a live Claude Code instance.
    """

    def test_first_hook_continues(self, tmp_path):
        """First hook increments iteration and outputs work prompt."""
        proj, dot_claude = make_project(tmp_path)
        write_loop_file(dot_claude, 1, "Write hello world", 3)

        decision = run_hook(proj, make_stop_event("I'll start working on this."))

        assert decision["decision"] == "block"
        assert "Loop iteration" in decision["reason"]
        assert "Write hello world" in decision["reason"]
        assert read_loop_file(dot_claude) == {"iteration": 2, "prompt": "Write hello world", "total": 3, "deadline": None}

    def test_normal_continuation(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_loop_file(dot_claude, 1, "Do the thing", 5)

        decision = run_hook(proj, make_stop_event("Made some progress."))

        assert decision["decision"] == "block"
        assert "Loop iteration" in decision["reason"]
        assert read_loop_file(dot_claude)["iteration"] == 2

    def test_task_complete_triggers_verification(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_loop_file(dot_claude, 2, "Build feature X", 5)

        decision = run_hook(proj, make_stop_event("Done! TASK_COMPLETE"))

        assert decision["decision"] == "block"
        assert "Verification" in decision["reason"]
        assert "Build feature X" in decision["reason"]
        assert read_loop_file(dot_claude) is not None

    def test_review_okay_ends_loop(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_loop_file(dot_claude, 3, "Build feature X", 5)

        decision = run_hook(proj, make_stop_event("Everything looks good. REVIEW_OKAY"))

        assert decision["decision"] == "block"
        assert "verified" in decision["reason"].lower()
        assert read_loop_file(dot_claude) is None

    def test_review_incomplete_continues(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_loop_file(dot_claude, 3, "Build feature X", 5)

        decision = run_hook(proj, make_stop_event("Found a bug. REVIEW_INCOMPLETE"))

        assert decision["decision"] == "block"
        assert "Loop iteration" in decision["reason"]
        assert read_loop_file(dot_claude)["iteration"] == 4

    def test_iterations_exhausted(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_loop_file(dot_claude, 3, "Do stuff", 3)

        decision = run_hook(proj, make_stop_event("Still working..."))

        assert "exhausted" in decision["reason"].lower()
        assert read_loop_file(dot_claude) is None

    def test_no_loop_file_silent_exit(self, tmp_path):
        proj, _ = make_project(tmp_path)

        decision = run_hook(proj, make_stop_event("Hello"))
        assert decision is None

    def test_non_stop_event_ignored(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_loop_file(dot_claude, 3, "task", 5)

        decision = run_hook(proj, {"hook_event_name": "NotStop", "last_assistant_message": ""})
        assert decision is None
        assert read_loop_file(dot_claude)["iteration"] == 3

    def test_curly_braces_in_prompt(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        task = "Fix the {name} field in config"
        write_loop_file(dot_claude, 2, task, 5)

        decision = run_hook(proj, make_stop_event("Working on it."))
        assert task in decision["reason"]

    def test_curly_braces_in_verification(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        task = "Update {foo} and {bar}"
        write_loop_file(dot_claude, 2, task, 5)

        decision = run_hook(proj, make_stop_event("Done! TASK_COMPLETE"))
        assert "Verification" in decision["reason"]
        assert task in decision["reason"]

    def test_multiline_prompt_in_hook(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        task = "Step 1: fix parsing.\nStep 2: add tests.\nStep 3: deploy."
        write_loop_file(dot_claude, 2, task, 5)

        decision = run_hook(proj, make_stop_event("Progress made."))
        assert task in decision["reason"]

    def test_full_lifecycle(self, tmp_path):
        """Full start -> hook -> ... -> done, exercising the real start() path."""
        proj, dot_claude = make_project(tmp_path)

        # 1. Start: parse args from stdin, write loop file
        run_start(proj, "5 Create hello.txt")
        assert read_loop_file(dot_claude) == {"iteration": 1, "prompt": "Create hello.txt", "total": 5, "deadline": None}

        # 2. First hook: iteration 2
        d = run_hook(proj, make_stop_event("Starting work."))
        assert d["decision"] == "block"
        assert read_loop_file(dot_claude)["iteration"] == 2

        # 3. Second hook: iteration 3
        d = run_hook(proj, make_stop_event("Created the file."))
        assert read_loop_file(dot_claude)["iteration"] == 3

        # 4. TASK_COMPLETE -> verification
        d = run_hook(proj, make_stop_event("All done. TASK_COMPLETE"))
        assert "Verification" in d["reason"]
        assert read_loop_file(dot_claude)["iteration"] == 4

        # 5. REVIEW_OKAY -> loop ends
        d = run_hook(proj, make_stop_event("Verified. REVIEW_OKAY"))
        assert "verified" in d["reason"].lower()
        assert read_loop_file(dot_claude) is None

    def test_deadline_expired_ends_loop(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_loop_file(dot_claude, 2, "Do stuff", deadline=time.time() - 1)

        decision = run_hook(proj, make_stop_event("Still working..."))

        assert "time limit" in decision["reason"].lower()
        assert read_loop_file(dot_claude) is None

    def test_deadline_not_expired_continues(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_loop_file(dot_claude, 2, "Do stuff", deadline=time.time() + 3600)

        decision = run_hook(proj, make_stop_event("Making progress."))

        assert "Loop iteration" in decision["reason"]
        assert read_loop_file(dot_claude)["iteration"] == 3
