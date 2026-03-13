"""Persist session — re-injects the same task each iteration."""

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


# --- State files ---

def _state_path():
    d = dot_claude_dir()
    return d / 'persist.json' if d else None


def _pending_path():
    d = dot_claude_dir()
    return d / 'persist-pending.json' if d else None


def read_all_sessions():
    path = _state_path()
    if path and path.exists():
        return json.load(path.open())
    return {}


def _write_all_sessions(sessions):
    path = _state_path()
    if sessions:
        json.dump(sessions, path.open('w'))
    elif path and path.exists():
        path.unlink()


def read_session(session_id):
    return read_all_sessions().get(session_id)


def write_session(session_id, state):
    sessions = read_all_sessions()
    sessions[session_id] = state
    _write_all_sessions(sessions)


def delete_session(session_id):
    sessions = read_all_sessions()
    sessions.pop(session_id, None)
    _write_all_sessions(sessions)


def read_pending():
    path = _pending_path()
    if path and path.exists():
        return json.load(path.open())
    return None


def write_pending(state):
    path = _pending_path()
    json.dump(state, path.open('w'))


def delete_pending():
    path = _pending_path()
    if path and path.exists():
        path.unlink()


def activate_pending(session_id):
    """Promote pending session to persist.json under the given session_id.

    Returns the activated state, or None if no pending session.
    """
    pending = read_pending()
    if pending is None:
        return None
    delete_pending()
    write_session(session_id, pending)
    return pending


# --- Commands ---

def start():
    if dot_claude_dir() is None:
        print("Not in a project, or there is no .claude directory.", file=sys.stderr)
        sys.exit(1)

    raw = sys.stdin.read().strip() if not sys.stdin.isatty() else ''

    if not raw:
        print("Usage: /persist LIMIT TASK", file=sys.stderr)
        sys.exit(1)

    parts = raw.split(None, 1)
    if len(parts) < 2:
        print("Usage: /persist LIMIT TASK", file=sys.stderr)
        sys.exit(1)

    # Don't overwrite an active pending session.
    if read_pending():
        return

    total, deadline = parse_limit(parts[0])
    prompt = parts[1]
    write_pending({
        'iteration': 0, 'prompt': prompt,
        'total': total, 'deadline': deadline,
    })
    print(WORK_PROMPT.format(prompt=prompt, iteration=1))


def stop():
    # Clear any pending session.
    delete_pending()
    # Without session_id, clear all sessions.
    path = _state_path()
    if path and path.exists():
        path.unlink()
    print('Session stopped.')


def status():
    pending = read_pending()
    if pending:
        from .common import format_remaining
        print(f"Session pending (awaiting first stop hook)")
        print(f"Task: {pending['prompt']}")
        return
    # Without session_id we can't look up a specific session; show all.
    sessions = read_all_sessions()
    if not sessions:
        print("No active session.")
        return
    from .common import format_remaining
    for sid, data in sessions.items():
        print(f"Session {sid[:8]}...: iteration {format_remaining(data)}")
        print(f"  Task: {data['prompt']}")


def stop_hook(session_id, state, event):
    """Handle a stop hook for a persist session."""
    prompt = state['prompt']
    iteration = state['iteration'] + 1

    last_msg = event.get('last_assistant_message', '')
    keyword = find_keyword(last_msg)

    expired = is_expired({**state, 'iteration': iteration})

    if keyword == 'REVIEW_OKAY':
        delete_session(session_id)
        print(json.dumps({
            "decision": "block",
            "reason": "Session complete (verified). Summarize what you accomplished.",
        }))
    elif expired:
        delete_session(session_id)
        reason = 'time limit reached' if expired == 'deadline' else 'iterations exhausted'
        print(json.dumps({
            "decision": "block",
            "reason": f"Session complete ({reason}). Summarize what you accomplished.",
        }))
    elif keyword == 'TASK_COMPLETE':
        write_session(session_id, {
            'iteration': iteration, 'prompt': prompt,
            'total': state.get('total'), 'deadline': state.get('deadline'),
        })
        print(json.dumps({
            "decision": "block",
            "reason": VERIFICATION_PROMPT.format(prompt=prompt),
        }))
    else:
        write_session(session_id, {
            'iteration': iteration, 'prompt': prompt,
            'total': state.get('total'), 'deadline': state.get('deadline'),
        })
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
