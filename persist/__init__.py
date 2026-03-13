"""Persistent coding sessions for Claude Code.

    persist              Start from /persist slash command
    persist hook         Stop hook handler
    persist stop         Cancel a running session
    persist status       Show session status
"""

import json
import sys

from .common import parse_limit, is_expired, format_remaining  # noqa: F401
from .session import (  # noqa: F401
    start, stop, status, stop_hook, find_keyword,
    read_all_sessions, read_session, write_session, delete_session,
    read_pending, activate_pending,
    _state_path, _pending_path,
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
    if event.get('hook_event_name') != 'Stop':
        return

    session_id = event.get('session_id')
    if not session_id:
        return

    # Check for a pending session (written by start() before session_id was known).
    state = read_session(session_id)
    if state is None:
        state = activate_pending(session_id)
    if state is None:
        return

    stop_hook(session_id, state, event)
