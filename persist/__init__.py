"""Persistent coding sessions for Claude Code.

    persist              Start from /persist slash command
    persist hook         Hook handler (PreToolUse + Stop)
    persist stop         Cancel a running session
    persist status       Show session status
"""

import json
import sys

from .common import parse_limit, is_expired, format_remaining, claude_pid  # noqa: F401
from .session import (  # noqa: F401
    start, stop, status, stop_hook, find_keyword,
    read_all_sessions, read_session, write_session, delete_session,
    resolve_session, resolve_by_session_id, associate,
    _db_path,
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

    if event_name == 'PreToolUse':
        _pre_tool_use(event)
    elif event_name == 'Stop':
        _stop(event)


def _pre_tool_use(event):
    """Associate the current PID with the Claude session_id."""
    pid = claude_pid()
    session_id = event.get('session_id')
    if not pid or not session_id:
        return
    key, state = resolve_session(pid)
    if not state:
        return
    # Already associated — nothing to do.
    if key == session_id:
        return
    associate(pid, session_id)


def _stop(event):
    pid = claude_pid()
    if not pid:
        return

    key, state = resolve_session(pid)

    if state is None:
        # After --continue: new PID, but session_id is preserved.
        session_id = event.get('session_id')
        if session_id:
            key, state = resolve_by_session_id(session_id)
        if state is None:
            return
        # Re-associate new PID with this session.
        associate(pid, session_id)

    stop_hook(key, state, event)
