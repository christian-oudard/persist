"""Persistent coding sessions for Claude Code.

    persist              Start from /persist slash command
    persist hook         Hook handler (Stop only)
    persist stop         Cancel a running session
    persist status       Show session status
"""

import json
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
