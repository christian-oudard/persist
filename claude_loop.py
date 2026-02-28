#!/usr/bin/env python3
"""Coding loop for Claude Code.

Provides a stop hook that re-prompts Claude with the task after each iteration,
with a verification step before completion.

Two modes:
    Fixed loop:  claude-loop           Start from /loop slash command
    Agent loop:  claude-loop agent     Start from /agent slash command

Common:
    claude-loop hook      Stop hook handler (called by Claude Code)
    claude-loop stop      Cancel a running loop
    claude-loop status    Show loop status
"""

from pathlib import Path
import json
import os
import re
import subprocess
import sys

# --- Fixed loop prompts ---

WORK_PROMPT = """\
# Loop iteration {iteration}

You are in a coding loop. Orient yourself by reading files and checking \
git status/log. Work incrementally: implement one piece, verify it works, \
then stop. You will be re-prompted after each iteration.

If the task is genuinely and fully complete, output exactly TASK_COMPLETE \
as a standalone message. Do not use it to escape the loop because you are \
stuck — use the next iteration to try a different approach.

## Task

{prompt}
"""

VERIFICATION_PROMPT = """\
# Verification

You indicated the task is complete. Before confirming, do a thorough review:

1. Re-read the original task requirements below.
2. Read through all code you wrote or modified.
3. Run the tests or otherwise verify the implementation works end-to-end.
4. Check for edge cases, missing requirements, or loose ends.

After your review, output exactly one of these keywords as a standalone \
message:

- REVIEW_OKAY — the task is fully and genuinely complete.
- REVIEW_INCOMPLETE — you found something incomplete or broken. Briefly \
describe what remains before the keyword.

## Task

{prompt}
"""

# --- Agent loop prompts ---

MANAGER_PROMPT = """\
You are a managing agent overseeing a coding worker. The worker is a \
Claude Code instance that can read/write files, run commands, and run \
tests. You direct the worker by giving it one focused instruction per turn.

## User's Goals

{goals}

## Your Current Plan

{plan}

## Work History

{history}

## Worker's Latest Output

{last_message}

## Your Task

1. Assess what the worker accomplished this turn.
2. Update your plan — note what's done and what remains.
3. Give a specific, actionable instruction for the worker's next turn.
4. Set done=true ONLY if ALL goals are fully and genuinely met.

Respond with ONLY a JSON object (no markdown, no explanation):
{{"assessment": "...", "plan": "...", "instruction": "...", "done": false}}
"""

AGENT_WORK_PROMPT = """\
# Managed iteration {iteration}

You are in a managed coding loop. A managing agent is directing your \
work toward the goals below. Follow the instruction. Work incrementally: \
do what's asked, verify it works, then stop.

## Goals

{goals}

## Instruction

{instruction}
"""


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
    elif cmd == 'agent':
        agent_start()
    elif cmd == 'test-manager':
        test_manager()
    else:
        start()


# --- Fixed loop ---

def start():
    if dot_claude_dir() is None:
        print("Not in a project, or there is no .claude directory.", file=sys.stderr)
        sys.exit(1)

    raw = sys.stdin.read().strip() if not sys.stdin.isatty() else ''

    # Don't overwrite an active loop.
    if read_loop_file():
        return

    if not raw:
        print("Usage: /loop NUM_ITERATIONS TASK", file=sys.stderr)
        sys.exit(1)

    parts = raw.split(None, 1)
    if len(parts) < 2:
        print("Usage: /loop NUM_ITERATIONS TASK", file=sys.stderr)
        sys.exit(1)

    total = int(parts[0])
    prompt = parts[1]
    # The initial Claude response (from the slash command text) is iteration 1.
    write_loop_file(1, prompt, total)


def loop_hook(loop_data, event):
    """Handle a stop hook for a fixed loop."""
    prompt = loop_data['prompt']
    iteration = loop_data['iteration'] + 1
    total = loop_data['total']

    last_msg = event.get('last_assistant_message', '')
    keyword = find_keyword(last_msg)

    if iteration > total:
        delete_loop_file()
        print(json.dumps({
            "decision": "block",
            "reason": "Loop complete (iterations exhausted). Summarize what you accomplished.",
        }))
    elif keyword == 'REVIEW_OKAY':
        delete_loop_file()
        print(json.dumps({
            "decision": "block",
            "reason": "Loop complete (verified). Summarize what you accomplished.",
        }))
    elif keyword == 'TASK_COMPLETE':
        write_loop_file(iteration, prompt, total)
        print(json.dumps({
            "decision": "block",
            "reason": VERIFICATION_PROMPT.format(prompt=prompt),
        }))
    else:
        write_loop_file(iteration, prompt, total)
        print(json.dumps({
            "decision": "block",
            "reason": WORK_PROMPT.format(prompt=prompt, iteration=iteration),
        }))


# --- Agent loop ---

def agent_start():
    if dot_claude_dir() is None:
        print("Not in a project, or there is no .claude directory.", file=sys.stderr)
        sys.exit(1)

    raw = sys.stdin.read().strip() if not sys.stdin.isatty() else ''

    if read_agent_file():
        return

    if not raw:
        print("Usage: /agent NUM_ITERATIONS GOALS", file=sys.stderr)
        sys.exit(1)

    parts = raw.split(None, 1)
    if len(parts) < 2:
        print("Usage: /agent NUM_ITERATIONS GOALS", file=sys.stderr)
        sys.exit(1)

    total = int(parts[0])
    goals = parts[1]
    write_agent_file({
        'goals': goals,
        'plan': '',
        'history': [],
        'current_instruction': goals,
        'iteration': 1,
        'total': total,
    })


def agent_hook(agent_data, event):
    """Handle a stop hook for an agent loop."""
    iteration = agent_data['iteration'] + 1
    total = agent_data['total']

    if iteration > total:
        delete_agent_file()
        print(json.dumps({
            "decision": "block",
            "reason": "Agent loop complete (iterations exhausted). Summarize what you accomplished.",
        }))
        return

    last_msg = event.get('last_assistant_message', '')

    response = call_manager(
        agent_data['goals'],
        agent_data.get('plan', ''),
        agent_data.get('history', []),
        last_msg,
    )

    if response.get('done'):
        delete_agent_file()
        print(json.dumps({
            "decision": "block",
            "reason": "Agent loop complete (goals met). Summarize what you accomplished.",
        }))
        return

    # Record what happened this turn.
    prev_instruction = agent_data.get('current_instruction', agent_data['goals'])
    new_history = agent_data.get('history', []) + [{
        'instruction': prev_instruction,
        'outcome': response['assessment'],
    }]

    write_agent_file({
        'goals': agent_data['goals'],
        'plan': response['plan'],
        'history': new_history,
        'current_instruction': response['instruction'],
        'iteration': iteration,
        'total': total,
    })

    reason = AGENT_WORK_PROMPT.format(
        iteration=iteration,
        goals=agent_data['goals'],
        instruction=response['instruction'],
    )
    print(json.dumps({
        "decision": "block",
        "reason": reason,
    }))


def test_manager():
    """Diagnostic: test whether the manager call works."""
    print("Testing manager call (claude --print --model haiku)...")
    response = call_manager(
        goals="Say hello",
        plan="",
        history=[],
        last_message="Ready to work.",
    )
    print(f"Response: {json.dumps(response, indent=2)}")
    if response == manager_fallback():
        print("WARNING: Got fallback response. Manager call may have failed.")
        sys.exit(1)
    else:
        print("OK: Manager call succeeded.")


def call_manager(goals, plan, history, last_message):
    """Call the managing agent to decide the next instruction."""
    # Test seam: allow tests to inject a canned response.
    test_response = os.environ.get('CLAUDE_LOOP_MANAGER_RESPONSE')
    if test_response is not None:
        return json.loads(test_response)

    prompt = build_manager_prompt(goals, plan, history, last_message)

    env = {k: v for k, v in os.environ.items() if k != 'CLAUDECODE'}
    try:
        result = subprocess.run(
            ['claude', '--print', '--model', 'haiku',
             '--no-session-persistence', '--tools', ''],
            input=prompt,
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )
        return parse_manager_response(result.stdout)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return manager_fallback()


def build_manager_prompt(goals, plan, history, last_message):
    """Build the prompt sent to the managing agent."""
    return MANAGER_PROMPT.format(
        goals=goals,
        plan=plan or "(No plan yet — this is the first iteration.)",
        history=format_history(history),
        last_message=last_message[:4000],
    )


def format_history(history):
    if not history:
        return "(No history yet — this is the first iteration.)"
    lines = []
    for i, entry in enumerate(history, 1):
        lines.append(f"Turn {i}: {entry['instruction']}")
        lines.append(f"  Outcome: {entry['outcome']}")
    return "\n".join(lines)


def parse_manager_response(text):
    """Parse the manager's JSON response, handling various formats."""
    text = text.strip()
    if not text:
        return manager_fallback()

    # Try direct JSON parse.
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass

    # Try extracting from markdown code block.
    match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except (json.JSONDecodeError, ValueError):
            pass

    # Try finding a JSON object.
    start = text.find('{')
    end = text.rfind('}')
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except (json.JSONDecodeError, ValueError):
            pass

    return manager_fallback()


def manager_fallback():
    """Default manager response when the real call fails."""
    return {
        "assessment": "(Manager unavailable.)",
        "plan": "",
        "instruction": "Continue working toward the goals. Orient yourself and make progress.",
        "done": False,
    }


# --- Hook dispatch ---

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


# --- Status ---

def status():
    agent_data = read_agent_file()
    if agent_data:
        print(f"Agent loop active: iteration {agent_data['iteration']}/{agent_data['total']}")
        print(f"Goals: {agent_data['goals']}")
        if agent_data.get('plan'):
            print(f"Plan: {agent_data['plan']}")
        return

    data = read_loop_file()
    if data:
        print(f"Loop active: iteration {data['iteration']}/{data['total']}")
        print(f"Task: {data['prompt']}")
    else:
        print("No active loop.")


# --- File management ---

def read_loop_file():
    path = loop_file_path()
    if path and path.exists():
        return json.load(path.open())


def write_loop_file(iteration, prompt, total):
    json.dump({'iteration': iteration, 'prompt': prompt, 'total': total}, loop_file_path().open('w'))


def delete_loop_file():
    path = loop_file_path()
    if path:
        path.unlink(missing_ok=True)


def loop_file_path():
    d = dot_claude_dir()
    return d / 'loop.json' if d else None


def read_agent_file():
    path = agent_file_path()
    if path and path.exists():
        return json.load(path.open())


def write_agent_file(data):
    json.dump(data, agent_file_path().open('w'))


def delete_agent_file():
    path = agent_file_path()
    if path:
        path.unlink(missing_ok=True)


def agent_file_path():
    d = dot_claude_dir()
    return d / 'agent.json' if d else None


def dot_claude_dir():
    p = Path.cwd()
    for p in [p, *p.parents]:
        if p == Path.home():
            break
        dot_claude = p / '.claude'
        if dot_claude.exists():
            return dot_claude
    return None


def find_keyword(text):
    """Check text for a loop keyword.

    Returns 'TASK_COMPLETE', 'REVIEW_OKAY', 'REVIEW_INCOMPLETE', or None.
    """
    for kw in ('TASK_COMPLETE', 'REVIEW_OKAY', 'REVIEW_INCOMPLETE'):
        if kw in text:
            return kw
    return None


if __name__ == '__main__':
    main()
