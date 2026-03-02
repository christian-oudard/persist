"""Agent coding loop — a managing agent directs the worker each turn."""

import json
import os
import re
import subprocess
import sys

from .common import dot_claude_dir, parse_limit, is_expired

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


# --- State file ---

def agent_file_path():
    d = dot_claude_dir()
    return d / 'agent.json' if d else None


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


# --- Commands ---

def agent_start():
    if dot_claude_dir() is None:
        print("Not in a project, or there is no .claude directory.", file=sys.stderr)
        sys.exit(1)

    raw = sys.stdin.read().strip() if not sys.stdin.isatty() else ''

    if read_agent_file():
        return

    if not raw:
        print("Usage: /loop-agent LIMIT GOALS", file=sys.stderr)
        sys.exit(1)

    parts = raw.split(None, 1)
    if len(parts) < 2:
        print("Usage: /loop-agent LIMIT GOALS", file=sys.stderr)
        sys.exit(1)

    total, deadline = parse_limit(parts[0])
    goals = parts[1]
    write_agent_file({
        'goals': goals,
        'plan': '',
        'history': [],
        'current_instruction': goals,
        'iteration': 1,
        'total': total,
        'deadline': deadline,
    })


def agent_hook(agent_data, event):
    """Handle a stop hook for an agent loop."""
    iteration = agent_data['iteration'] + 1

    expired = is_expired({**agent_data, 'iteration': iteration})
    if expired:
        delete_agent_file()
        reason = 'time limit reached' if expired == 'deadline' else 'iterations exhausted'
        print(json.dumps({
            "decision": "block",
            "reason": f"Agent loop complete ({reason}). Summarize what you accomplished.",
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
        'total': agent_data.get('total'),
        'deadline': agent_data.get('deadline'),
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


# --- Manager ---

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
