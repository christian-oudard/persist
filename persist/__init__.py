"""Persistent coding sessions for Claude Code.

    persist            Start from /persist slash command
    persist hook       Stop hook handler (called by Claude Code)
    persist stop       Cancel a running session
    persist status     Show session status
"""

import json
import sys

from .common import parse_limit, is_expired, format_remaining  # noqa: F401
from .fixed import (  # noqa: F401
    start, stop_hook, find_keyword,
    read_state_file, write_state_file, delete_state_file,
)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else None
    if cmd == 'hook':
        hook()
    elif cmd == 'stop':
        delete_state_file()
        print('Session stopped.')
    elif cmd == 'status':
        status()
    else:
        start()


def hook():
    state = read_state_file()
    if state is None:
        return

    event = json.loads(sys.stdin.read())
    if event['hook_event_name'] != 'Stop':
        return

    stop_hook(state, event)


def status():
    data = read_state_file()
    if data:
        print(f"Session active: iteration {format_remaining(data)}")
        print(f"Task: {data['prompt']}")
    else:
        print("No active session.")
