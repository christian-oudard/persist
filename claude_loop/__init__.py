"""Coding loop for Claude Code.

    claude-loop            Start from /loop slash command
    claude-loop hook       Stop hook handler (called by Claude Code)
    claude-loop stop       Cancel a running loop
    claude-loop status     Show loop status
"""

import json
import sys

from .common import parse_limit, is_expired, format_remaining  # noqa: F401
from .fixed import (  # noqa: F401
    start, loop_hook, find_keyword,
    read_loop_file, write_loop_file, delete_loop_file,
)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else None
    if cmd == 'hook':
        hook()
    elif cmd == 'stop':
        delete_loop_file()
        print('Loop stopped.')
    elif cmd == 'status':
        status()
    else:
        start()


def hook():
    loop_data = read_loop_file()
    if loop_data is None:
        return

    event = json.loads(sys.stdin.read())
    if event['hook_event_name'] != 'Stop':
        return

    loop_hook(loop_data, event)


def status():
    data = read_loop_file()
    if data:
        print(f"Loop active: iteration {format_remaining(data)}")
        print(f"Task: {data['prompt']}")
    else:
        print("No active loop.")
