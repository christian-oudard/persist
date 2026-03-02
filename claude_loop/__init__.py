"""Coding loop for Claude Code.

Two modes:
    Fixed loop:  claude-loop            Start from /loop slash command
    Agent loop:  claude-loop loop-agent  Start from /loop-agent slash command

Common:
    claude-loop hook      Stop hook handler (called by Claude Code)
    claude-loop stop      Cancel a running loop
    claude-loop status    Show loop status
"""

import json
import sys

from .common import parse_limit, is_expired, format_remaining  # noqa: F401
from .fixed import (  # noqa: F401
    start, loop_hook, find_keyword,
    read_loop_file, write_loop_file, delete_loop_file,
)
from .agent import (  # noqa: F401
    agent_start, agent_hook, test_manager,
    read_agent_file, write_agent_file, delete_agent_file,
    call_manager, build_manager_prompt, format_history,
    parse_manager_response, manager_fallback,
)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else None
    if cmd == 'hook':
        hook()
    elif cmd == 'stop':
        delete_loop_file()
        delete_agent_file()
        print('Loop stopped.')
    elif cmd == 'status':
        status()
    elif cmd == 'loop-agent':
        agent_start()
    elif cmd == 'test-manager':
        test_manager()
    else:
        start()


def hook():
    agent_data = read_agent_file()
    loop_data = read_loop_file()

    if agent_data is None and loop_data is None:
        return

    event = json.loads(sys.stdin.read())
    if event['hook_event_name'] != 'Stop':
        return

    if agent_data:
        agent_hook(agent_data, event)
    else:
        loop_hook(loop_data, event)


def status():
    agent_data = read_agent_file()
    if agent_data:
        print(f"Agent loop active: iteration {format_remaining(agent_data)}")
        print(f"Goals: {agent_data['goals']}")
        if agent_data.get('plan'):
            print(f"Plan: {agent_data['plan']}")
        return

    data = read_loop_file()
    if data:
        print(f"Loop active: iteration {format_remaining(data)}")
        print(f"Task: {data['prompt']}")
    else:
        print("No active loop.")
