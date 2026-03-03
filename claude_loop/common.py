"""Shared utilities for claude-loop."""

import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path


def dot_claude_dir():
    p = Path.cwd()
    project_root = None
    for p in [p, *p.parents]:
        if p == Path.home():
            break
        dot_claude = p / '.claude'
        if dot_claude.is_dir():
            return dot_claude
        if project_root is None and (p / '.git').exists():
            project_root = p
    if project_root:
        dot_claude = project_root / '.claude'
        dot_claude.mkdir()
        print(f"Found .git in {project_root}, created {dot_claude}/", file=sys.stderr)
        return dot_claude
    return None


def parse_limit(s):
    """Parse a limit string into (total_iterations, deadline_timestamp).

    Returns a tuple where exactly one value is non-None.
    Raises ValueError for unparseable input.
    """
    s = s.strip()

    # Duration: "2h", "30m"
    m = re.match(r'^(\d+)h$', s, re.IGNORECASE)
    if m:
        return None, time.time() + int(m.group(1)) * 3600

    m = re.match(r'^(\d+)m$', s, re.IGNORECASE)
    if m:
        return None, time.time() + int(m.group(1)) * 60

    # Clock time: "2pm", "11am"
    m = re.match(r'^(\d{1,2})(am|pm)$', s, re.IGNORECASE)
    if m:
        hour = int(m.group(1))
        if m.group(2).lower() == 'pm' and hour != 12:
            hour += 12
        elif m.group(2).lower() == 'am' and hour == 12:
            hour = 0
        return None, _next_occurrence(hour, 0)

    # HH:MM format
    m = re.match(r'^(\d{1,2}):(\d{2})$', s)
    if m:
        return None, _next_occurrence(int(m.group(1)), int(m.group(2)))

    # Pure number
    n = int(s)
    if n >= 1000:
        # Military time: 1400 -> 14:00
        hour, minute = divmod(n, 100)
        if hour > 23 or minute > 59:
            raise ValueError(f"Invalid military time: {s}")
        return None, _next_occurrence(hour, minute)
    if n < 1:
        raise ValueError(f"Iteration count must be >= 1: {s}")
    return n, None


def _next_occurrence(hour, minute):
    """Return Unix timestamp for the next occurrence of HH:MM."""
    now = datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target.timestamp()


def is_expired(data):
    """Check if a loop's limit has been reached."""
    deadline = data.get('deadline')
    if deadline and time.time() >= deadline:
        return 'deadline'
    total = data.get('total')
    if total and data.get('iteration', 0) > total:
        return 'iterations'
    return None


def format_remaining(data):
    """Format the remaining limit for status display."""
    iteration = data.get('iteration', '?')
    deadline = data.get('deadline')
    if deadline:
        remaining = deadline - time.time()
        dt = datetime.fromtimestamp(deadline)
        clock = dt.strftime('%H:%M')
        if remaining <= 0:
            return f"{iteration}, until {clock} (expired)"
        hours, rem = divmod(int(remaining), 3600)
        minutes = rem // 60
        if hours > 0:
            return f"{iteration}, until {clock} ({hours}h {minutes}m remaining)"
        return f"{iteration}, until {clock} ({minutes}m remaining)"
    total = data.get('total')
    if total:
        return f"{iteration}/{total}"
    return "unknown"
