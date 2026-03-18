# persist Spec

## Purpose

persist extends Claude Code with persistent coding sessions. It re-prompts Claude after each turn via a stop hook, keeping work going across multiple iterations without manual intervention.

`/persist LIMIT TASK` re-injects the same task prompt every iteration. Simple, predictable. Good for focused single-track tasks.

## Limits

The LIMIT argument can be an iteration count or a time limit:

- `5` — 5 iterations (max 999)
- `2h` — 2 hours from now
- `30m` — 30 minutes from now
- `2pm` — today at 2 PM (tomorrow if already past)
- `1400` — military time, today at 14:00 (numbers >= 1000 are always time)
- `14:00` — same as above

Time-based sessions run until the deadline, with no iteration cap. Iteration-based sessions have no time limit.

The session ends when any termination condition is met: iteration limit, deadline, or task completion keyword.

### --no-exit

`/persist --no-exit LIMIT TASK` disables the early-exit TASK_COMPLETE/REVIEW_OKAY mechanism. The session runs until the iteration or deadline limit is reached, ignoring completion keywords entirely. The work prompt omits TASK_COMPLETE instructions. The flag can appear anywhere before the TASK text.

## Session Identity

Every session is scoped to a Claude Code session_id. This prevents cross-session pollution when multiple agents share a project.

`start()` writes the session under a placeholder key (`unclaimed_1`, `unclaimed_2`, etc.) since no session_id is available at start time. The first Stop hook claims the entry by reading the transcript JSONL and matching the task prompt. Subsequent Stop hooks match by session_id directly (fast path).

### persist.json

`.claude/persist.json` is a flat dict keyed by session_id (or placeholder for unclaimed entries):

```json
{
  "unclaimed_1": {"iteration": 0, "prompt": "Fix the parser", "total": 5, "deadline": null, "started": 1710400000.0},
  "abc-123": {"iteration": 3, "prompt": "Add tests", "total": 10, "deadline": null, "started": 1710399000.0}
}
```

Fields: `iteration` (int), `prompt` (str), `total` (int or null), `deadline` (float or null), `started` (float — Unix timestamp when session began). Exactly one of `total` or `deadline` is non-null. `started` is used to compute elapsed/total duration for statusline display.

## Hooks

A single `persist hook` binary handles the Stop hook event. There is no PreToolUse hook.

## Flow

```
User types: /persist 5 Fix the parser

1. Skill expands, Claude calls Bash tool with `persist <<'...'`

2. Bash tool runs `persist` CLI (start)
   --> writes session under unclaimed_1 key
   --> print work prompt (iteration 1) to stdout for skill expansion

3. Claude responds, Stop hook fires
   --> session_id not found in persist.json (not yet claimed)
   --> reads transcript JSONL at transcript_path
   --> finds unclaimed entry whose prompt appears in transcript
   --> claims entry: re-keys unclaimed_1 → session_id
   --> proceeds with stop_hook():
       increment iteration, check limits/keywords, inject next prompt

4. Subsequent Stop hooks
   --> session_id found directly in persist.json (fast path)
   --> stop_hook() as above

5. Session ends when:
   --> REVIEW_OKAY keyword: session complete (verified)
   --> iteration limit or deadline reached
```

## Commands

- `/persist LIMIT TASK` — start session
- `/persist --no-exit LIMIT TASK` — start session without early exit
- `/persist-status` — show status
- `/persist-stop` — stop a running session (clears all sessions)
