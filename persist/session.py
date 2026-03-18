"""Persist session — re-injects the same task each iteration."""

import json
import sys
import time

from .common import dot_claude_dir, parse_limit, is_expired

ORIENTATION = """\
You are in a persistent coding session. Orient yourself by reading files and \
checking git status/log. Work incrementally: implement one piece, verify it \
works, then stop. You will be re-prompted after each iteration."""

PERSISTENCE = """\
Stay persistent and creative. If your current approach is blocked, try a \
different angle. There is always more to explore, build, improve, or learn. \
Do not declare the task "done" or "at steady state", find the next thing \
worth doing."""

EXIT_INSTRUCTIONS = """\
If the task is genuinely and fully complete, output exactly TASK_COMPLETE \
as a standalone message. Do not use it to escape the session because you are \
stuck, use the next iteration to try a different approach."""

VERIFICATION = """\
You indicated the task is complete. Before confirming, do a thorough review:

1. Re-read the original task requirements below.
2. Read through all code you wrote or modified.
3. Run the tests or otherwise verify the implementation works end-to-end.
4. Check for edge cases, missing requirements, or loose ends.

After your review, output exactly one of these keywords as a standalone \
message:

- REVIEW_OKAY — the task is fully and genuinely complete.
- REVIEW_INCOMPLETE — you found something incomplete or broken. Briefly \
describe what remains before the keyword."""


def work_prompt(*, prompt, iteration_label, no_exit=False):
    parts = [f"# Iteration {iteration_label}", ORIENTATION, PERSISTENCE]
    if not no_exit:
        parts.append(EXIT_INSTRUCTIONS)
    parts.append(f"## Task\n\n{prompt}")
    return "\n\n".join(parts) + "\n"


def verification_prompt(*, prompt):
    return "\n\n".join(["# Verification", VERIFICATION, f"## Task\n\n{prompt}"]) + "\n"


def _iteration_label(iteration):
    """Format iteration label like '3'."""
    return str(iteration)


# --- State file ---

def _state_path():
    d = dot_claude_dir()
    return d / 'persist.json' if d else None


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


# --- Unclaimed entries ---

def next_unclaimed_key():
    """Return the next available unclaimed_N key."""
    sessions = read_all_sessions()
    n = 1
    while f"unclaimed_{n}" in sessions:
        n += 1
    return f"unclaimed_{n}"


def find_unclaimed():
    """Return list of (key, state) for unclaimed entries."""
    sessions = read_all_sessions()
    return [(k, v) for k, v in sessions.items() if k.startswith("unclaimed_")]


def claim_session(old_key, new_session_id):
    """Re-key an entry from placeholder to real session_id."""
    sessions = read_all_sessions()
    state = sessions.pop(old_key, None)
    if state is not None:
        sessions[new_session_id] = state
        _write_all_sessions(sessions)
    return state


# --- Transcript ---

def transcript_contains_prompt(transcript_path, prompt):
    """Check if prompt text appears in transcript JSONL messages."""
    try:
        with open(transcript_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                content = entry.get('message', {}).get('content', '')
                if isinstance(content, str) and prompt in content:
                    return True
    except (FileNotFoundError, OSError):
        pass
    return False


# --- Commands ---

def start():
    if dot_claude_dir() is None:
        print("Not in a project, or there is no .claude directory.", file=sys.stderr)
        sys.exit(1)

    raw = sys.stdin.read().strip() if not sys.stdin.isatty() else ''

    if not raw:
        print("Usage: /persist LIMIT TASK", file=sys.stderr)
        sys.exit(1)

    no_exit = '--no-exit' in raw
    if no_exit:
        raw = raw.replace('--no-exit', '', 1).strip()

    parts = raw.split(None, 1)
    if len(parts) < 2:
        print("Usage: /persist LIMIT TASK", file=sys.stderr)
        sys.exit(1)

    total, deadline = parse_limit(parts[0])
    prompt = parts[1]
    _write_all_sessions({})
    key = next_unclaimed_key()
    state = {
        'iteration': 0, 'prompt': prompt,
        'total': total, 'deadline': deadline,
        'started': time.time(),
    }
    if no_exit:
        state['no_exit'] = True
    write_session(key, state)
    print(work_prompt(prompt=prompt, iteration_label="1", no_exit=no_exit))


def stop():
    path = _state_path()
    if path and path.exists():
        path.unlink()
    print('Session stopped (all sessions cleared).')


def status():
    from .common import format_remaining
    sessions = read_all_sessions()
    if not sessions:
        print("No active session.")
        return
    for key, data in sessions.items():
        print(f"Session {key}: iteration {format_remaining(data)}")
        print(f"  Task: {data['prompt']}")


def _next_state(state, iteration):
    """Build the next iteration's state dict, preserving all fields."""
    return {
        'iteration': iteration, 'prompt': state['prompt'],
        'total': state.get('total'), 'deadline': state.get('deadline'),
        'started': state.get('started'),
        **({'no_exit': True} if state.get('no_exit') else {}),
    }


def stop_hook(session_id, state, event):
    """Handle a stop hook for a persist session."""
    prompt = state['prompt']
    iteration = state['iteration'] + 1
    no_exit = state.get('no_exit')

    last_msg = event.get('last_assistant_message', '')
    keyword = None if no_exit else find_keyword(last_msg)

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
        write_session(session_id, _next_state(state, iteration))
        print(json.dumps({
            "decision": "block",
            "reason": verification_prompt(prompt=prompt),
        }))
    else:
        write_session(session_id, _next_state(state, iteration))
        print(json.dumps({
            "decision": "block",
            "reason": work_prompt(prompt=prompt, iteration_label=_iteration_label(iteration), no_exit=no_exit),
        }))


def find_keyword(text):
    """Check text for a session keyword.

    Returns 'REVIEW_OKAY', 'REVIEW_INCOMPLETE', 'TASK_COMPLETE', or None.
    REVIEW_* checked first: the model may reference TASK_COMPLETE in prose
    while giving a review answer.
    """
    for kw in ('REVIEW_OKAY', 'REVIEW_INCOMPLETE', 'TASK_COMPLETE'):
        if kw in text:
            return kw
    return None
