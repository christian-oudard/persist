"""Fixed persist session — re-injects the same task each iteration."""

import json
import sys

from .common import dot_claude_dir, parse_limit, is_expired

WORK_PROMPT = """\
# Iteration {iteration}

You are in a persistent coding session. Orient yourself by reading files and \
checking git status/log. Work incrementally: implement one piece, verify it \
works, then stop. You will be re-prompted after each iteration.

If the task is genuinely and fully complete, output exactly TASK_COMPLETE \
as a standalone message. Do not use it to escape the session because you are \
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


# --- State file ---

def state_file_path():
    d = dot_claude_dir()
    return d / 'persist.json' if d else None


def read_state_file():
    path = state_file_path()
    if path and path.exists():
        return json.load(path.open())


def write_state_file(iteration, prompt, total=None, deadline=None):
    json.dump({'iteration': iteration, 'prompt': prompt, 'total': total, 'deadline': deadline},
              state_file_path().open('w'))


def delete_state_file():
    path = state_file_path()
    if path:
        path.unlink(missing_ok=True)


# --- Commands ---

def start():
    if dot_claude_dir() is None:
        print("Not in a project, or there is no .claude directory.", file=sys.stderr)
        sys.exit(1)

    raw = sys.stdin.read().strip() if not sys.stdin.isatty() else ''

    # Don't overwrite an active session.
    if read_state_file():
        return

    if not raw:
        print("Usage: /persist LIMIT TASK", file=sys.stderr)
        sys.exit(1)

    parts = raw.split(None, 1)
    if len(parts) < 2:
        print("Usage: /persist LIMIT TASK", file=sys.stderr)
        sys.exit(1)

    total, deadline = parse_limit(parts[0])
    prompt = parts[1]
    # The initial Claude response (from the slash command text) is iteration 1.
    write_state_file(1, prompt, total=total, deadline=deadline)


def stop_hook(state, event):
    """Handle a stop hook for a persist session."""
    prompt = state['prompt']
    iteration = state['iteration'] + 1

    last_msg = event.get('last_assistant_message', '')
    keyword = find_keyword(last_msg)

    expired = is_expired({**state, 'iteration': iteration})

    if keyword == 'REVIEW_OKAY':
        delete_state_file()
        print(json.dumps({
            "decision": "block",
            "reason": "Session complete (verified). Summarize what you accomplished.",
        }))
    elif expired:
        delete_state_file()
        reason = 'time limit reached' if expired == 'deadline' else 'iterations exhausted'
        print(json.dumps({
            "decision": "block",
            "reason": f"Session complete ({reason}). Summarize what you accomplished.",
        }))
    elif keyword == 'TASK_COMPLETE':
        write_state_file(iteration, prompt, total=state.get('total'), deadline=state.get('deadline'))
        print(json.dumps({
            "decision": "block",
            "reason": VERIFICATION_PROMPT.format(prompt=prompt),
        }))
    else:
        write_state_file(iteration, prompt, total=state.get('total'), deadline=state.get('deadline'))
        print(json.dumps({
            "decision": "block",
            "reason": WORK_PROMPT.format(prompt=prompt, iteration=iteration),
        }))


def find_keyword(text):
    """Check text for a session keyword.

    Returns 'TASK_COMPLETE', 'REVIEW_OKAY', 'REVIEW_INCOMPLETE', or None.
    """
    for kw in ('TASK_COMPLETE', 'REVIEW_OKAY', 'REVIEW_INCOMPLETE'):
        if kw in text:
            return kw
    return None
