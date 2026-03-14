"""Unit and integration tests for persist core logic."""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))
import persist

from helpers import (
    make_project, read_state_file, read_session, write_session,
    write_unclaimed, make_transcript,
    run_main, run_start, run_status, run_hook, run_persist,
    make_stop_event, make_pre_tool_use_event,
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

    def test_priority_review_okay_first(self):
        """REVIEW_OKAY beats TASK_COMPLETE when both appear in text."""
        assert persist.find_keyword("TASK_COMPLETE REVIEW_OKAY") == "REVIEW_OKAY"

    def test_priority_review_incomplete_over_task_complete(self):
        assert persist.find_keyword("TASK_COMPLETE REVIEW_INCOMPLETE") == "REVIEW_INCOMPLETE"


# --- Integration tests: start() ---

class TestStart:
    def test_basic_start(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        result = run_start(proj, "5 Fix the bug")
        assert result.returncode == 0
        state = read_session(dot_claude, "unclaimed_1")
        assert state["iteration"] == 0
        assert state["prompt"] == "Fix the bug"
        assert state["total"] == 5

    def test_start_outputs_work_prompt(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        result = run_start(proj, "3 Do stuff")
        assert "Iteration 1" in result.stdout
        assert "Do stuff" in result.stdout

    def test_start_creates_unclaimed_key(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        run_start(proj, "5 task")
        assert read_session(dot_claude, "unclaimed_1") is not None

    def test_start_replaces_existing_unclaimed(self, tmp_path):
        """Starting a new task should clear any previous unclaimed entries."""
        proj, dot_claude = make_project(tmp_path)
        run_start(proj, "5 task A")
        run_start(proj, "3 task B")
        # task B should be the only entry
        assert read_session(dot_claude, "unclaimed_1")["prompt"] == "task B"
        assert read_session(dot_claude, "unclaimed_2") is None

    def test_start_replaces_claimed_session(self, tmp_path):
        """Starting a new task should clear previously claimed sessions too."""
        proj, dot_claude = make_project(tmp_path)
        write_session(dot_claude, "csid-1", 3, "old task", 5)
        run_start(proj, "3 new task")
        assert read_session(dot_claude, "csid-1") is None
        assert read_session(dot_claude, "unclaimed_1")["prompt"] == "new task"

    def test_multiline_task(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        run_start(proj, "3 Fix the bug.\nAlso update tests.")
        assert read_session(dot_claude, "unclaimed_1")["prompt"] == "Fix the bug.\nAlso update tests."

    def test_curly_braces(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        run_start(proj, "2 Fix the {name} field")
        assert read_session(dot_claude, "unclaimed_1")["prompt"] == "Fix the {name} field"

    def test_shell_metacharacters(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        run_start(proj, "1 echo $HOME && rm -rf /; don't")
        assert read_session(dot_claude, "unclaimed_1")["prompt"] == "echo $HOME && rm -rf /; don't"

    def test_quotes(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        run_start(proj, """2 Fix the "parser" and it's 'edge cases'""")
        assert read_session(dot_claude, "unclaimed_1")["prompt"] == """Fix the "parser" and it's 'edge cases'"""

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

    def test_time_limit_start(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        result = run_start(proj, "2h Fix the bug")
        assert result.returncode == 0
        data = read_session(dot_claude, "unclaimed_1")
        assert data["prompt"] == "Fix the bug"
        assert data["total"] is None
        assert data["deadline"] is not None
        assert data["deadline"] > time.time()

    def test_status_active(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_session(dot_claude, "csid-1", 3, "Fix the bug", 5)
        result = run_status(proj)
        assert result.returncode == 0
        assert "Fix the bug" in result.stdout

    def test_status_inactive(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        result = run_status(proj)
        assert result.returncode == 0
        assert "No active session" in result.stdout


# --- Integration tests: transcript claiming ---

class TestTranscriptClaiming:
    def test_claims_unclaimed_by_transcript(self, tmp_path):
        """Stop hook claims unclaimed entry when prompt found in transcript."""
        proj, dot_claude = make_project(tmp_path)
        key = write_unclaimed(dot_claude, "Fix the parser", total=5)

        transcript = tmp_path / "transcript.jsonl"
        make_transcript(transcript, ["/persist 5 Fix the parser"])

        event = make_stop_event("Making progress.", session_id="csid-1",
                                transcript_path=str(transcript))
        decision = run_hook(proj, event)

        assert decision["decision"] == "block"
        assert read_session(dot_claude, "csid-1")["iteration"] == 1
        assert read_session(dot_claude, key) is None

    def test_ignores_unclaimed_without_matching_prompt(self, tmp_path):
        """Stop hook ignores unclaimed entry when prompt NOT in transcript."""
        proj, dot_claude = make_project(tmp_path)
        write_unclaimed(dot_claude, "Fix the parser", total=5)

        transcript = tmp_path / "transcript.jsonl"
        make_transcript(transcript, ["Something completely different"])

        event = make_stop_event("Making progress.", session_id="csid-1",
                                transcript_path=str(transcript))
        decision = run_hook(proj, event)
        assert decision is None
        assert read_session(dot_claude, "unclaimed_1") is not None

    def test_distinguishes_two_unclaimed_by_prompt(self, tmp_path):
        """Two unclaimed entries: only the matching one gets claimed."""
        proj, dot_claude = make_project(tmp_path)
        write_unclaimed(dot_claude, "Fix the parser", total=5)
        write_unclaimed(dot_claude, "Add tests", total=3)

        transcript = tmp_path / "transcript.jsonl"
        make_transcript(transcript, ["/persist 3 Add tests"])

        event = make_stop_event("Started testing.", session_id="csid-1",
                                transcript_path=str(transcript))
        decision = run_hook(proj, event)

        assert decision["decision"] == "block"
        assert read_session(dot_claude, "csid-1")["prompt"] == "Add tests"
        assert read_session(dot_claude, "unclaimed_1")["prompt"] == "Fix the parser"

    def test_fast_path_already_claimed(self, tmp_path):
        """Already-claimed session matched by session_id directly."""
        proj, dot_claude = make_project(tmp_path)
        write_session(dot_claude, "csid-1", 2, "Fix the parser", 5)

        event = make_stop_event("Making progress.", session_id="csid-1")
        decision = run_hook(proj, event)

        assert decision["decision"] == "block"
        assert read_session(dot_claude, "csid-1")["iteration"] == 3

    def test_no_transcript_path_no_claim(self, tmp_path):
        """Without transcript_path, unclaimed entries can't be claimed."""
        proj, dot_claude = make_project(tmp_path)
        write_unclaimed(dot_claude, "Fix the parser", total=5)

        # /dev/null won't contain the prompt
        event = make_stop_event("Progress.", session_id="csid-1")
        decision = run_hook(proj, event)
        assert decision is None


# --- Integration tests: hook state machine ---

class TestHookStateMachine:
    def test_normal_continuation(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_session(dot_claude, "csid-1", 1, "Do the thing", 5)

        decision = run_hook(proj, make_stop_event("Made some progress.",
                                                   session_id="csid-1"))

        assert decision["decision"] == "block"
        assert read_session(dot_claude, "csid-1")["iteration"] == 2

    def test_task_complete_triggers_verification(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_session(dot_claude, "csid-1", 2, "Build feature X", 5)

        decision = run_hook(proj, make_stop_event("Done! TASK_COMPLETE",
                                                   session_id="csid-1"))

        assert decision["decision"] == "block"
        assert "Verification" in decision["reason"]

    def test_review_okay_ends_session(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_session(dot_claude, "csid-1", 3, "Build feature X", 5)

        decision = run_hook(proj, make_stop_event("Everything looks good. REVIEW_OKAY",
                                                   session_id="csid-1"))

        assert decision["decision"] == "block"
        assert "verified" in decision["reason"].lower()
        assert read_session(dot_claude, "csid-1") is None

    def test_review_okay_beats_iterations_exhausted(self, tmp_path):
        """REVIEW_OKAY takes priority even when iterations are also exhausted."""
        proj, dot_claude = make_project(tmp_path)
        # iteration=1, total=1 → next iteration (2) exceeds total
        write_session(dot_claude, "csid-1", 1, "debug", 1)

        decision = run_hook(proj, make_stop_event(
            "Looks good. REVIEW_OKAY", session_id="csid-1"))

        assert decision["decision"] == "block"
        assert "verified" in decision["reason"].lower()
        assert "exhausted" not in decision["reason"].lower()
        assert read_session(dot_claude, "csid-1") is None

    def test_review_incomplete_continues(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_session(dot_claude, "csid-1", 3, "Build feature X", 5)

        decision = run_hook(proj, make_stop_event("Found a bug. REVIEW_INCOMPLETE",
                                                   session_id="csid-1"))

        assert decision["decision"] == "block"
        assert read_session(dot_claude, "csid-1")["iteration"] == 4

    def test_iterations_exhausted(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_session(dot_claude, "csid-1", 3, "Do stuff", 3)

        decision = run_hook(proj, make_stop_event("Still working...",
                                                   session_id="csid-1"))

        assert "exhausted" in decision["reason"].lower()
        assert read_session(dot_claude, "csid-1") is None

    def test_no_state_silent(self, tmp_path):
        proj, _ = make_project(tmp_path)
        decision = run_hook(proj, make_stop_event("Hello"))
        assert decision is None

    def test_non_stop_event_ignored(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_session(dot_claude, "csid-1", 3, "task", 5)

        decision = run_hook(proj, {"hook_event_name": "NotStop",
                                    "last_assistant_message": ""})
        assert decision is None
        assert read_session(dot_claude, "csid-1")["iteration"] == 3

    def test_curly_braces_in_prompt(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_session(dot_claude, "csid-1", 2, "Fix the {name} field", 5)
        decision = run_hook(proj, make_stop_event("Working on it.",
                                                   session_id="csid-1"))
        assert "Fix the {name} field" in decision["reason"]

    def test_curly_braces_in_verification(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_session(dot_claude, "csid-1", 2, "Update {foo} and {bar}", 5)
        decision = run_hook(proj, make_stop_event("Done! TASK_COMPLETE",
                                                   session_id="csid-1"))
        assert "Update {foo} and {bar}" in decision["reason"]

    def test_multiline_prompt_in_hook(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        task = "Step 1: fix parsing.\nStep 2: add tests.\nStep 3: deploy."
        write_session(dot_claude, "csid-1", 2, task, 5)
        decision = run_hook(proj, make_stop_event("Progress made.",
                                                   session_id="csid-1"))
        assert task in decision["reason"]

    def test_full_lifecycle(self, tmp_path):
        """Full start -> claim via transcript -> hook -> ... -> done."""
        proj, dot_claude = make_project(tmp_path)

        # 1. Start: writes session under unclaimed key
        result = run_start(proj, "5 Create hello.txt")
        assert result.returncode == 0
        assert read_session(dot_claude, "unclaimed_1")["iteration"] == 0

        # 2. First Stop: claims via transcript, iteration 1
        transcript = tmp_path / "transcript.jsonl"
        make_transcript(transcript, ["/persist 5 Create hello.txt"])

        d = run_hook(proj, make_stop_event("Starting work.", session_id="csid-1",
                                            transcript_path=str(transcript)))
        assert d["decision"] == "block"
        assert read_session(dot_claude, "csid-1")["iteration"] == 1
        assert read_session(dot_claude, "unclaimed_1") is None

        # 3. Second Stop: fast path by session_id, iteration 2
        d = run_hook(proj, make_stop_event("Created the file.",
                                            session_id="csid-1"))
        assert read_session(dot_claude, "csid-1")["iteration"] == 2

        # 4. TASK_COMPLETE -> verification
        d = run_hook(proj, make_stop_event("All done. TASK_COMPLETE",
                                            session_id="csid-1"))
        assert "Verification" in d["reason"]

        # 5. REVIEW_OKAY -> session ends
        d = run_hook(proj, make_stop_event("Verified. REVIEW_OKAY",
                                            session_id="csid-1"))
        assert "verified" in d["reason"].lower()
        assert read_session(dot_claude, "csid-1") is None

    def test_deadline_expired_ends_session(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_session(dot_claude, "csid-1", 2, "Do stuff",
                      deadline=time.time() - 1)
        decision = run_hook(proj, make_stop_event("Still working...",
                                                   session_id="csid-1"))
        assert "time limit" in decision["reason"].lower()
        assert read_session(dot_claude, "csid-1") is None

    def test_deadline_not_expired_continues(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_session(dot_claude, "csid-1", 2, "Do stuff",
                      deadline=time.time() + 3600)
        decision = run_hook(proj, make_stop_event("Making progress.",
                                                   session_id="csid-1"))
        assert "Iteration" in decision["reason"]
        assert read_session(dot_claude, "csid-1")["iteration"] == 3


# --- Session isolation tests ---

class TestSessionIsolation:
    def test_different_sessions_coexist(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_session(dot_claude, "csid-A", 2, "task A", 5)
        write_session(dot_claude, "csid-B", 1, "task B", 3)

        run_hook(proj, make_stop_event("progress", session_id="csid-A"))
        assert read_session(dot_claude, "csid-A")["iteration"] == 3
        assert read_session(dot_claude, "csid-B")["iteration"] == 1

    def test_other_session_untouched(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_session(dot_claude, "csid-A", 2, "task", 5)
        # csid-B has no session — hook is silent
        decision = run_hook(proj, make_stop_event("progress",
                                                   session_id="csid-B"))
        assert decision is None
        assert read_session(dot_claude, "csid-A")["iteration"] == 2

    def test_session_end_preserves_other(self, tmp_path):
        proj, dot_claude = make_project(tmp_path)
        write_session(dot_claude, "csid-A", 3, "task A", 3)
        write_session(dot_claude, "csid-B", 1, "task B", 5)

        decision = run_hook(proj, make_stop_event("done", session_id="csid-A"))
        assert "exhausted" in decision["reason"].lower()
        assert read_session(dot_claude, "csid-A") is None
        assert read_session(dot_claude, "csid-B")["iteration"] == 1

# --- PreToolUse: block self-stop ---

class TestBlockSelfStop:
    def test_blocks_bash_persist_stop(self, tmp_path):
        """Agent in a persist session cannot run 'persist stop' via Bash."""
        proj, dot_claude = make_project(tmp_path)
        write_session(dot_claude, "csid-1", 2, "Fix bugs", 5)

        event = make_pre_tool_use_event(
            "Bash", {"command": "persist stop"}, session_id="csid-1")
        decision = run_hook(proj, event)

        assert decision["decision"] == "block"
        assert "Cannot stop" in decision["reason"]
        # Session still exists
        assert read_session(dot_claude, "csid-1") is not None

    def test_blocks_skill_persist_stop(self, tmp_path):
        """Agent in a persist session cannot invoke the persist-stop skill."""
        proj, dot_claude = make_project(tmp_path)
        write_session(dot_claude, "csid-1", 2, "Fix bugs", 5)

        event = make_pre_tool_use_event(
            "Skill", {"skill": "persist-stop"}, session_id="csid-1")
        decision = run_hook(proj, event)

        assert decision["decision"] == "block"
        assert "Cannot stop" in decision["reason"]

    def test_blocks_qualified_skill_name(self, tmp_path):
        """Fully qualified skill name like 'abc123-persist-stop' is also blocked."""
        proj, dot_claude = make_project(tmp_path)
        write_session(dot_claude, "csid-1", 2, "Fix bugs", 5)

        event = make_pre_tool_use_event(
            "Skill", {"skill": "xw6rl2i143903mspnjv4p52ahidqbphk-persist-stop"},
            session_id="csid-1")
        decision = run_hook(proj, event)

        assert decision["decision"] == "block"

    def test_allows_non_persist_session(self, tmp_path):
        """Bash 'persist stop' is allowed when session is NOT persisted."""
        proj, dot_claude = make_project(tmp_path)

        event = make_pre_tool_use_event(
            "Bash", {"command": "persist stop"}, session_id="csid-1")
        decision = run_hook(proj, event)

        assert decision is None

    def test_allows_other_bash_commands(self, tmp_path):
        """Non-persist-stop Bash commands are not blocked."""
        proj, dot_claude = make_project(tmp_path)
        write_session(dot_claude, "csid-1", 2, "Fix bugs", 5)

        event = make_pre_tool_use_event(
            "Bash", {"command": "persist status"}, session_id="csid-1")
        decision = run_hook(proj, event)

        assert decision is None

    def test_allows_other_skills(self, tmp_path):
        """Non-persist-stop skills are not blocked."""
        proj, dot_claude = make_project(tmp_path)
        write_session(dot_claude, "csid-1", 2, "Fix bugs", 5)

        event = make_pre_tool_use_event(
            "Skill", {"skill": "persist-status"}, session_id="csid-1")
        decision = run_hook(proj, event)

        assert decision is None

    def test_does_not_block_different_session(self, tmp_path):
        """A session that is NOT persisted can still run persist stop."""
        proj, dot_claude = make_project(tmp_path)
        write_session(dot_claude, "csid-1", 2, "Fix bugs", 5)

        # csid-2 is NOT a persist session
        event = make_pre_tool_use_event(
            "Bash", {"command": "persist stop"}, session_id="csid-2")
        decision = run_hook(proj, event)

        assert decision is None


# --- Session isolation tests (continued) ---

class TestSessionContinuation:
    def test_continue_after_restart(self, tmp_path):
        """--continue spawns new process but same session_id survives."""
        proj, dot_claude = make_project(tmp_path)
        write_session(dot_claude, "csid-1", 2, "Build feature", 5)

        # After restart, Stop hook with same session_id
        decision = run_hook(proj, make_stop_event("More progress.",
                                                   session_id="csid-1"))
        assert decision["decision"] == "block"
        assert read_session(dot_claude, "csid-1")["iteration"] == 3
