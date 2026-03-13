"""Persist session — re-injects the same task each iteration."""

import json
import sys
import uuid

from .common import dot_claude_dir, parse_limit, is_expired, claude_pid

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


# --- Database: persist.json ---
#
# Structure:
#   {
#     "pids": { "<claude_pid>": "<session_key>" },
#     "sessions": { "<session_key>": { iteration, prompt, total, deadline } }
#   }
#
# session_key is a nonce (from start()) until a hook associates it with
# the real Claude session_id.

def _db_path():
    d = dot_claude_dir()
    return d / 'persist.json' if d else None


def _read_db():
    path = _db_path()
    if path and path.exists():
        data = json.load(path.open())
        # Ensure both sections exist.
        data.setdefault('pids', {})
        data.setdefault('sessions', {})
        return data
    return {'pids': {}, 'sessions': {}}


def _write_db(db):
    path = _db_path()
    if db['sessions'] or db['pids']:
        json.dump(db, path.open('w'))
    elif path and path.exists():
        path.unlink()


def resolve_session(pid):
    """Look up a session by PID. Returns (session_key, state) or (None, None)."""
    db = _read_db()
    key = db['pids'].get(pid)
    if key:
        state = db['sessions'].get(key)
        if state:
            return key, state
    return None, None


def resolve_by_session_id(session_id):
    """Find a session by Claude session_id. Returns (session_key, state) or (None, None)."""
    db = _read_db()
    # Direct key match.
    state = db['sessions'].get(session_id)
    if state:
        return session_id, state
    return None, None


def associate(pid, session_id):
    """Associate a PID with a Claude session_id.

    If the PID currently points to a nonce, migrates the session from
    nonce to session_id.
    """
    db = _read_db()
    old_key = db['pids'].get(pid)

    if old_key == session_id:
        return  # Already associated.

    if old_key and old_key in db['sessions']:
        # Migrate: rename nonce → session_id.
        db['sessions'][session_id] = db['sessions'].pop(old_key)
        # Update any other PIDs pointing to the old nonce.
        for p, k in db['pids'].items():
            if k == old_key:
                db['pids'][p] = session_id

    db['pids'][pid] = session_id
    _write_db(db)


def write_session(key, state):
    db = _read_db()
    db['sessions'][key] = state
    _write_db(db)


def delete_session(key):
    db = _read_db()
    db['sessions'].pop(key, None)
    # Clean up PID references to this key.
    db['pids'] = {p: k for p, k in db['pids'].items() if k != key}
    _write_db(db)


def read_all_sessions():
    return _read_db()['sessions']


def read_session(key):
    return _read_db()['sessions'].get(key)


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

    pid = claude_pid()
    if not pid:
        print("Could not find Claude Code process in process tree.",
              file=sys.stderr)
        sys.exit(1)

    # Don't overwrite an active session.
    if resolve_session(pid)[1]:
        return

    total, deadline = parse_limit(parts[0])
    prompt = parts[1]
    nonce = uuid.uuid4().hex[:12]

    db = _read_db()
    db['pids'][pid] = nonce
    db['sessions'][nonce] = {
        'iteration': 0, 'prompt': prompt,
        'total': total, 'deadline': deadline,
    }
    _write_db(db)
    print(WORK_PROMPT.format(prompt=prompt, iteration=1))


def stop():
    pid = claude_pid()
    if pid:
        key, _ = resolve_session(pid)
        if key:
            delete_session(key)
            print('Session stopped.')
            return
    # Fallback: clear everything.
    path = _db_path()
    if path and path.exists():
        path.unlink()
    print('Session stopped (all sessions cleared).')


def status():
    pid = claude_pid()
    if pid:
        from .common import format_remaining
        key, data = resolve_session(pid)
        if data:
            print(f"Session active: iteration {format_remaining(data)}")
            print(f"Task: {data['prompt']}")
            return
    # Fallback: show all.
    sessions = read_all_sessions()
    if not sessions:
        print("No active session.")
        return
    from .common import format_remaining
    for sid, data in sessions.items():
        print(f"Session {sid[:8]}...: iteration {format_remaining(data)}")
        print(f"  Task: {data['prompt']}")


def stop_hook(key, state, event):
    """Handle a stop hook for a persist session."""
    prompt = state['prompt']
    iteration = state['iteration'] + 1

    last_msg = event.get('last_assistant_message', '')
    keyword = find_keyword(last_msg)

    expired = is_expired({**state, 'iteration': iteration})

    def _updated():
        return {
            'iteration': iteration, 'prompt': prompt,
            'total': state.get('total'), 'deadline': state.get('deadline'),
        }

    if keyword == 'REVIEW_OKAY':
        delete_session(key)
        print(json.dumps({
            "decision": "block",
            "reason": "Session complete (verified). Summarize what you accomplished.",
        }))
    elif expired:
        delete_session(key)
        reason = 'time limit reached' if expired == 'deadline' else 'iterations exhausted'
        print(json.dumps({
            "decision": "block",
            "reason": f"Session complete ({reason}). Summarize what you accomplished.",
        }))
    elif keyword == 'TASK_COMPLETE':
        write_session(key, _updated())
        print(json.dumps({
            "decision": "block",
            "reason": VERIFICATION_PROMPT.format(prompt=prompt),
        }))
    else:
        write_session(key, _updated())
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
