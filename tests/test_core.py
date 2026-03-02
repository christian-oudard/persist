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
    read_agent_file, write_agent_file,
    run_main, run_start, run_status, run_hook,
    make_stop_event, make_agent_data, make_manager_response,
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


# --- Unit tests: parse_manager_response ---

class TestParseManagerResponse:
    def test_valid_json(self):
        text = '{"assessment": "ok", "plan": "p", "instruction": "i", "done": false}'
        result = claude_loop.parse_manager_response(text)
        assert result["assessment"] == "ok"
        assert result["done"] is False

    def test_json_in_code_block(self):
        text = '```json\n{"assessment": "ok", "plan": "p", "instruction": "i", "done": false}\n```'
        result = claude_loop.parse_manager_response(text)
        assert result["assessment"] == "ok"

    def test_json_in_plain_code_block(self):
        text = '```\n{"assessment": "ok", "plan": "p", "instruction": "i", "done": true}\n```'
        result = claude_loop.parse_manager_response(text)
        assert result["done"] is True

    def test_json_with_preamble(self):
        text = 'Here is my response:\n{"assessment": "ok", "plan": "p", "instruction": "i", "done": false}'
        result = claude_loop.parse_manager_response(text)
        assert result["instruction"] == "i"

    def test_empty_string(self):
        result = claude_loop.parse_manager_response("")
        assert result["done"] is False
        assert "instruction" in result

    def test_garbage(self):
        result = claude_loop.parse_manager_response("not json at all")
        assert result == claude_loop.manager_fallback()


# --- Unit tests: format_history ---

class TestFormatHistory:
    def test_empty(self):
        result = claude_loop.format_history([])
        assert "first iteration" in result.lower()

    def test_one_entry(self):
        result = claude_loop.format_history([
            {"instruction": "Set up project", "outcome": "Created files"},
        ])
        assert "Turn 1" in result
        assert "Set up project" in result
        assert "Created files" in result

    def test_multiple_entries(self):
        result = claude_loop.format_history([
            {"instruction": "A", "outcome": "B"},
            {"instruction": "C", "outcome": "D"},
        ])
        assert "Turn 1" in result
        assert "Turn 2" in result


# --- Unit tests: build_manager_prompt ---

class TestBuildManagerPrompt:
    def test_includes_goals(self):
        prompt = claude_loop.build_manager_prompt("Build X", "", [], "did stuff")
        assert "Build X" in prompt

    def test_includes_last_message(self):
        prompt = claude_loop.build_manager_prompt("g", "", [], "worker output here")
        assert "worker output here" in prompt

    def test_truncates_long_message(self):
        long_msg = "x" * 10000
        prompt = claude_loop.build_manager_prompt("g", "", [], long_msg)
        assert len(prompt) < 10000 + 2000  # prompt template + truncated message

    def test_empty_plan_shows_first_iteration(self):
        prompt = claude_loop.build_manager_prompt("g", "", [], "msg")
        assert "first iteration" in prompt.lower()


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

    def test_stop_deletes_agent(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_agent_file(dot_claude, make_agent_data())
        result = run_main(proj, ["stop"])
        assert result.returncode == 0
        assert read_agent_file(dot_claude) is None

    def test_stop_deletes_both(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_loop_file(dot_claude, 2, "task", 5)
        write_agent_file(dot_claude, make_agent_data())
        result = run_main(proj, ["stop"])
        assert read_loop_file(dot_claude) is None
        assert read_agent_file(dot_claude) is None

    def test_status_routes(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_loop_file(dot_claude, 1, "my task", 3)
        result = run_main(proj, ["status"])
        assert result.returncode == 0
        assert "1/3" in result.stdout

    def test_status_agent(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_agent_file(dot_claude, make_agent_data(goals="Build API", iteration=3, total=10))
        result = run_main(proj, ["status"])
        assert result.returncode == 0
        assert "Agent loop" in result.stdout
        assert "3/10" in result.stdout
        assert "Build API" in result.stdout

    def test_no_args_routes_to_start(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        result = run_main(proj, [], stdin_text="3 Do stuff")
        assert result.returncode == 0
        assert read_loop_file(dot_claude) == {"iteration": 1, "prompt": "Do stuff", "total": 3, "deadline": None}

    def test_agent_routes_to_agent_start(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        result = run_main(proj, ["loop-agent"], stdin_text="10 Build an API")
        assert result.returncode == 0
        data = read_agent_file(dot_claude)
        assert data["goals"] == "Build an API"
        assert data["total"] == 10
        assert data["iteration"] == 1

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

    def test_no_dot_claude_dir(self, tmp_path):
        # No .claude directory — should fail.
        result = run_start(tmp_path, "3 Do stuff")
        assert result.returncode == 1
        assert "Not in a project" in result.stderr

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


# --- Integration tests: agent loop ---

class TestAgentStart:
    def test_basic(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        result = run_main(proj, ["loop-agent"], stdin_text="10 Build a REST API")
        assert result.returncode == 0
        data = read_agent_file(dot_claude)
        assert data["goals"] == "Build a REST API"
        assert data["total"] == 10
        assert data["iteration"] == 1
        assert data["plan"] == ""
        assert data["history"] == []
        assert data["current_instruction"] == "Build a REST API"

    def test_no_dot_claude(self, tmp_path):
        result = run_main(tmp_path, ["loop-agent"], stdin_text="5 Do stuff")
        assert result.returncode == 1

    def test_empty_stdin(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        result = run_main(proj, ["loop-agent"], stdin_text="")
        assert result.returncode == 1

    def test_missing_goals(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        result = run_main(proj, ["loop-agent"], stdin_text="5")
        assert result.returncode == 1

    def test_time_limit_start(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        result = run_main(proj, ["loop-agent"], stdin_text="2h Build an API")
        assert result.returncode == 0
        data = read_agent_file(dot_claude)
        assert data["goals"] == "Build an API"
        assert data["total"] is None
        assert data["deadline"] is not None
        assert data["deadline"] > time.time()

    def test_no_overwrite(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        original = make_agent_data(goals="Original")
        write_agent_file(dot_claude, original)
        run_main(proj, ["loop-agent"], stdin_text="10 New goals")
        assert read_agent_file(dot_claude)["goals"] == "Original"


class TestAgentHook:
    """Test the agent hook using the CLAUDE_LOOP_MANAGER_RESPONSE test seam."""

    def _manager_env(self, response):
        return {"CLAUDE_LOOP_MANAGER_RESPONSE": json.dumps(response)}

    def test_continues_with_instruction(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_agent_file(dot_claude, make_agent_data(iteration=1, total=10))

        mgr = make_manager_response(
            assessment="Set up project",
            plan="1. Done: setup\n2. Next: endpoints",
            instruction="Implement CRUD endpoints",
        )
        decision = run_hook(proj, make_stop_event("Created Flask app."),
                            extra_env=self._manager_env(mgr))

        assert decision["decision"] == "block"
        assert "Implement CRUD endpoints" in decision["reason"]
        assert "Managed iteration 2" in decision["reason"]

        data = read_agent_file(dot_claude)
        assert data["iteration"] == 2
        assert data["current_instruction"] == "Implement CRUD endpoints"
        assert data["plan"] == "1. Done: setup\n2. Next: endpoints"
        assert len(data["history"]) == 1
        assert data["history"][0]["outcome"] == "Set up project"

    def test_done_ends_loop(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_agent_file(dot_claude, make_agent_data(iteration=3, total=10))

        mgr = make_manager_response(done=True)
        decision = run_hook(proj, make_stop_event("All done!"),
                            extra_env=self._manager_env(mgr))

        assert "goals met" in decision["reason"].lower()
        assert read_agent_file(dot_claude) is None

    def test_iterations_exhausted(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_agent_file(dot_claude, make_agent_data(iteration=5, total=5))

        decision = run_hook(proj, make_stop_event("Still working..."),
                            extra_env=self._manager_env(make_manager_response()))

        assert "exhausted" in decision["reason"].lower()
        assert read_agent_file(dot_claude) is None

    def test_deadline_expired_ends_loop(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_agent_file(dot_claude, make_agent_data(
            iteration=2, total=None, deadline=time.time() - 1,
        ))

        decision = run_hook(proj, make_stop_event("Still working..."),
                            extra_env=self._manager_env(make_manager_response()))

        assert "time limit" in decision["reason"].lower()
        assert read_agent_file(dot_claude) is None

    def test_deadline_not_expired_continues(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_agent_file(dot_claude, make_agent_data(
            iteration=2, total=None, deadline=time.time() + 3600,
        ))

        mgr = make_manager_response(instruction="Keep going")
        decision = run_hook(proj, make_stop_event("Making progress."),
                            extra_env=self._manager_env(mgr))

        assert "Keep going" in decision["reason"]
        assert read_agent_file(dot_claude) is not None

    def test_history_accumulates(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        existing_history = [
            {"instruction": "Set up project", "outcome": "Done"},
        ]
        write_agent_file(dot_claude, make_agent_data(
            iteration=2, total=10,
            history=existing_history,
            current_instruction="Write endpoints",
        ))

        mgr = make_manager_response(
            assessment="Endpoints written",
            instruction="Write tests",
        )
        run_hook(proj, make_stop_event("Wrote all endpoints."),
                 extra_env=self._manager_env(mgr))

        data = read_agent_file(dot_claude)
        assert len(data["history"]) == 2
        assert data["history"][1]["instruction"] == "Write endpoints"
        assert data["history"][1]["outcome"] == "Endpoints written"

    def test_agent_takes_priority_over_loop(self, tmp_path):
        """When both agent.json and loop.json exist, agent wins."""
        proj, dot_claude = make_project(tmp_path)
        write_loop_file(dot_claude, 1, "loop task", 5)
        write_agent_file(dot_claude, make_agent_data(iteration=1, total=10))

        mgr = make_manager_response(instruction="Agent instruction")
        decision = run_hook(proj, make_stop_event("output"),
                            extra_env=self._manager_env(mgr))

        assert "Agent instruction" in decision["reason"]
        # Loop file should be untouched.
        assert read_loop_file(dot_claude)["iteration"] == 1

    def test_no_agent_file_falls_to_loop(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_loop_file(dot_claude, 1, "loop task", 5)

        decision = run_hook(proj, make_stop_event("working"))

        assert "Loop iteration" in decision["reason"]

    def test_goals_in_worker_prompt(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_agent_file(dot_claude, make_agent_data(
            goals="Build a REST API", iteration=1, total=10,
        ))

        mgr = make_manager_response(instruction="Set up Flask")
        decision = run_hook(proj, make_stop_event("ready"),
                            extra_env=self._manager_env(mgr))

        assert "Build a REST API" in decision["reason"]
        assert "Set up Flask" in decision["reason"]

    def test_non_stop_event_ignored(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_agent_file(dot_claude, make_agent_data(iteration=1, total=10))

        event = {"hook_event_name": "NotStop", "last_assistant_message": ""}
        decision = run_hook(proj, event,
                            extra_env=self._manager_env(make_manager_response()))
        assert decision is None

    def test_full_lifecycle(self, tmp_path):
        """Full agent lifecycle: start -> hook -> hook -> done."""
        proj, dot_claude = make_project(tmp_path)

        # Start.
        run_main(proj, ["loop-agent"], stdin_text="10 Build and test an API")
        data = read_agent_file(dot_claude)
        assert data["iteration"] == 1

        # Iteration 1 -> 2: manager assigns first real task.
        mgr1 = make_manager_response(
            assessment="Understood the goals",
            plan="1. Setup 2. Endpoints 3. Tests",
            instruction="Create project structure with Flask",
        )
        d = run_hook(proj, make_stop_event("I'll build an API."),
                     extra_env=self._manager_env(mgr1))
        assert "Create project structure" in d["reason"]
        assert read_agent_file(dot_claude)["iteration"] == 2

        # Iteration 2 -> 3: manager assigns next task.
        mgr2 = make_manager_response(
            assessment="Project structure created",
            plan="1. Done 2. Endpoints 3. Tests",
            instruction="Implement CRUD endpoints",
        )
        d = run_hook(proj, make_stop_event("Created Flask app with models."),
                     extra_env=self._manager_env(mgr2))
        assert "CRUD endpoints" in d["reason"]
        assert read_agent_file(dot_claude)["iteration"] == 3

        # Iteration 3 -> done: manager says done.
        mgr3 = make_manager_response(done=True, assessment="All complete")
        d = run_hook(proj, make_stop_event("Tests pass."),
                     extra_env=self._manager_env(mgr3))
        assert "goals met" in d["reason"].lower()
        assert read_agent_file(dot_claude) is None
