"""Persistent coding sessions for Claude Code.

    persist              Start from /persist slash command
    persist hook         Stop hook handler (called by Claude Code)
    persist prompt-hook  UserPromptSubmit hook (captures session_id)
    persist stop         Cancel a running session
    persist status       Show session status
"""

import json
import sys

from .common import parse_limit, is_expired, format_remaining  # noqa: F401
from .session import (  # noqa: F401
    start, stop_hook, find_keyword,
    read_state_file, write_state_file, delete_state_file,
    state_file_path,
)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else None
    if cmd == 'hook':
        hook()
    elif cmd == 'prompt-hook':
        prompt_hook()
    elif cmd == 'stop':
        delete_state_file()
        print('Session stopped.')
    elif cmd == 'status':
        status()
    else:
        start()


def prompt_hook():
    """UserPromptSubmit hook: write session_id when user invokes /persist."""
    event = json.loads(sys.stdin.read())
    prompt = event.get('prompt', '')
    if not prompt.startswith('/persist '):
        return
    session_id = event.get('session_id')
    if not session_id:
        return
    path = state_file_path()
    if path is None:
        return
    path.parent.mkdir(exist_ok=True)
    (path.parent / 'persist-session').write_text(session_id)


def hook():
    state = read_state_file()
    if state is None:
        return

    event = json.loads(sys.stdin.read())
    if event['hook_event_name'] != 'Stop':
        return

    event_session_id = event.get('session_id')
    state_session_id = state.get('session_id')

    if state_session_id is not None and state_session_id != event_session_id:
        return

    stop_hook(state, event)


def status():
    data = read_state_file()
    if data:
        print(f"Session active: iteration {format_remaining(data)}")
        print(f"Task: {data['prompt']}")
    else:
        print("No active session.")
