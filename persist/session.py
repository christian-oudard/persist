"""Persist session — re-injects the same purpose prompt each iteration."""

import json
import os
import subprocess
import sys
import time

from .common import dot_claude_dir, parse_limit, is_expired


def _bell():
    """Run PERSIST_BELL_CMD if set. Called on every stop except re-injection."""
    cmd = os.environ.get('PERSIST_BELL_CMD')
    if cmd:
        subprocess.run(cmd, shell=True)


LOOP_INTRO = """\
You are in a persistent coding loop. Each time you stop, you will receive \
this same prompt again. Your work persists in files and git history."""

LOCK_NOTICE = """\
This is a locked session. There is no completion keyword, and there is \
more work to do. Each iteration, think about what the next most valuable \
thing to work on is."""

CONTINUATION = """\
Check git log and recent files to see what previous iterations accomplished. \
Then ask: what is the most valuable thing I could work on next?"""

ORIENTATION = """\
Work incrementally: do one thing, verify it, then stop. Difficulty is \
expected; each setback narrows the problem. If everything obvious is \
done, look deeper."""

EXIT_INSTRUCTIONS = """\
If the purpose is genuinely and fully achieved, output exactly TASK_COMPLETE \
as a standalone message. Treat this as a factual assertion, not an escape \
hatch. Only say it when it is unambiguously true. If you are stuck or \
frustrated, do not use it to bail out. The next iteration is a fresh \
chance to try something different."""

VERIFICATION = """\
You claimed the purpose is achieved. Before confirming, do a thorough review:

1. Re-read the original purpose below.
2. Read through all code you wrote or modified.
3. Run the tests or otherwise verify the implementation works end-to-end.
4. Check for edge cases, missing requirements, or loose ends.

After your review, output exactly one of these keywords as a standalone \
message:

- REVIEW_OKAY — the purpose is fully and genuinely achieved.
- REVIEW_INCOMPLETE — you found something incomplete or broken. Briefly \
describe what remains before the keyword."""


def work_prompt(*, prompt, iteration_label, lock=False, first=False):
    parts = [f"# Iteration {iteration_label}"]
    if first:
        parts.append(LOOP_INTRO)
    else:
        parts.append(CONTINUATION)
    parts.append(ORIENTATION)
    if lock:
        parts.append(LOCK_NOTICE)
    else:
        parts.append(EXIT_INSTRUCTIONS)
    parts.append(f"## Purpose\n\n{prompt}")
    return "\n\n".join(parts) + "\n"


def verification_prompt(*, prompt):
    return "\n\n".join(["# Verification", VERIFICATION, f"## Purpose\n\n{prompt}"]) + "\n"


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
        print("Usage: /persist [--lock|-l] LIMIT PURPOSE", file=sys.stderr)
        sys.exit(1)

    tokens = raw.split()
    lock = '--lock' in tokens or '-l' in tokens
    if lock:
        tokens.remove('--lock' if '--lock' in tokens else '-l')
        raw = ' '.join(tokens)

    parts = raw.split(None, 1)
    if len(parts) < 2:
        print("Usage: /persist [--lock|-l] LIMIT PURPOSE", file=sys.stderr)
        sys.exit(1)

    total, deadline = parse_limit(parts[0])
    prompt = parts[1]
    # Clear unclaimed entries (previous /persist in this conversation)
    # but preserve claimed entries (other agents' sessions).
    sessions = read_all_sessions()
    sessions = {k: v for k, v in sessions.items() if not k.startswith("unclaimed_")}
    _write_all_sessions(sessions)
    key = next_unclaimed_key()
    state = {
        'iteration': 0, 'prompt': prompt,
        'total': total, 'deadline': deadline,
        'started': time.time(),
    }
    if lock:
        state['lock'] = True
    write_session(key, state)
    print(work_prompt(prompt=prompt, iteration_label="1", lock=lock, first=True))


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
        print(f"  Purpose: {data['prompt']}")


def _next_state(state, iteration):
    """Build the next iteration's state dict, preserving all fields."""
    return {
        'iteration': iteration, 'prompt': state['prompt'],
        'total': state.get('total'), 'deadline': state.get('deadline'),
        'started': state.get('started'),
        **({'lock': True} if state.get('lock') else {}),
    }


def stop_hook(session_id, state, event):
    """Handle a stop hook for a persist session."""
    prompt = state['prompt']
    iteration = state['iteration'] + 1
    lock = state.get('lock')

    last_msg = event.get('last_assistant_message', '')
    keyword = find_keyword(last_msg)

    expired = is_expired({**state, 'iteration': iteration})

    if not lock and keyword == 'REVIEW_OKAY':
        delete_session(session_id)
        _bell()
        print(json.dumps({
            "decision": "block",
            "reason": "Session complete (verified). Summarize what you accomplished.",
        }))
    elif expired:
        delete_session(session_id)
        _bell()
        reason = 'time limit reached' if expired == 'deadline' else 'iterations exhausted'
        print(json.dumps({
            "decision": "block",
            "reason": f"Session complete ({reason}). Summarize what you accomplished.",
        }))
    elif not lock and keyword == 'TASK_COMPLETE':
        write_session(session_id, _next_state(state, iteration))
        print(json.dumps({
            "decision": "block",
            "reason": verification_prompt(prompt=prompt),
        }))
    else:
        write_session(session_id, _next_state(state, iteration))
        wp = work_prompt(prompt=prompt, iteration_label=_iteration_label(iteration), lock=lock)
        if lock and keyword:
            wp = ("You indicated you are done, however this is a locked "
                  "session with no completion keyword. There is more work "
                  "to do. Think about what the next most valuable thing to "
                  "work on is.\n\n" + wp)
        print(json.dumps({
            "decision": "block",
            "reason": wp,
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
