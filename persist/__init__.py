"""Persistent coding sessions for Claude Code.

    persist              Start from /persist slash command
    persist hook         Hook handler (Stop + PreToolUse)
    persist stop         Cancel a running session
    persist status       Show session status
"""

import json
import re
import sys

from .common import parse_limit, is_expired, format_remaining  # noqa: F401
from .session import (  # noqa: F401
    start, stop, status, stop_hook, find_keyword,
    read_all_sessions, read_session, write_session, delete_session,
    _state_path, find_unclaimed, claim_session, transcript_contains_prompt,
)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else None
    if cmd == 'hook':
        hook()
    elif cmd == 'stop':
        stop()
    elif cmd == 'status':
        status()
    else:
        start()


def hook():
    event = json.loads(sys.stdin.read())
    event_name = event.get('hook_event_name', '')

    if event_name == 'Stop':
        _stop(event)
    elif event_name == 'PreToolUse':
        _pre_tool_use(event)


def _is_persist_stop_attempt(event):
    """Check if a PreToolUse event is trying to run persist stop."""
    tool_name = event.get('tool_name', '')
    tool_input = event.get('tool_input', {})

    if tool_name == 'Bash':
        cmd = tool_input.get('command', '')
        return bool(re.search(r'\bpersist\s+stop\b', cmd))

    if tool_name == 'Skill':
        skill = tool_input.get('skill', '')
        return skill.endswith('persist-stop')

    return False


def _pre_tool_use(event):
    if not _is_persist_stop_attempt(event):
        return

    session_id = event.get('session_id')
    if not session_id:
        return

    if read_session(session_id) is not None:
        print(json.dumps({
            "decision": "block",
            "reason": "Cannot stop your own persist session. "
                      "Ask the user to run /persist-stop manually.",
        }))


def _stop(event):
    session_id = event.get('session_id')
    if not session_id:
        return

    # Fast path: already-claimed session
    state = read_session(session_id)
    if state is not None:
        stop_hook(session_id, state, event)
        return

    # Slow path: look for unclaimed entries matching transcript
    transcript_path = event.get('transcript_path')
    if not transcript_path:
        return

    for key, state in find_unclaimed():
        if transcript_contains_prompt(transcript_path, state['prompt']):
            claim_session(key, session_id)
            stop_hook(session_id, state, event)
            return
