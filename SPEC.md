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

## Session Identity

Every session is scoped to a Claude Code session_id. This prevents cross-session pollution when multiple agents share a project.

### persist-session file

`.claude/persist-session` is a transient file used to pass the session_id from the PreToolUse hook to the CLI. The hook writes it; the CLI reads and deletes it.

The PreToolUse hook (matched on Bash) fires before every Bash tool call. When the command starts with `persist`, the hook writes the event's session_id to `.claude/persist-session`. Because PreToolUse runs synchronously before the tool executes, the file is guaranteed to exist when the persist CLI reads it.

### persist.json

`.claude/persist.json` is keyed by session_id at the top level:

```json
{
  "abc-123": {"iteration": 2, "prompt": "Fix the parser", "total": 5, "deadline": null},
  "def-456": {"iteration": 1, "prompt": "Add tests", "total": null, "deadline": 1709413200}
}
```

persist.json is never written without a session_id. The stop hook looks up the session by the event's session_id directly — no file-based handoff needed since the event already carries it.

## Hooks

A single `persist hook` binary handles both hook events:

- **PreToolUse** (matcher: `Bash`) — writes persist-session before persist CLI runs
- **Stop** (matcher: all) — advances iteration, checks limits and keywords

## Flow

```
User types: /persist 5 Fix the parser

1. Skill expands, Claude calls Bash tool with `persist <<'...'`

2. PreToolUse hook fires (before Bash executes)
   --> command starts with "persist"
   --> writes session_id to .claude/persist-session

3. Bash tool runs `persist` CLI (start)
   --> reads .claude/persist-session, deletes it
   --> parse limit: "5" -> total=5, deadline=null
   --> write persist.json {session_id: {iteration: 0, ...}}
   --> print work prompt (iteration 1) to stdout for skill expansion

4. Claude responds, Stop hook fires
   --> read persist.json, look up by event session_id
   --> increment iteration
   --> check deadline or iteration limit
   --> check last_assistant_message for keywords
   --> TASK_COMPLETE? inject verification prompt
   --> REVIEW_OKAY? delete session entry, done
   --> REVIEW_INCOMPLETE or no keyword? inject work prompt
   --> limit reached? delete session entry, done
```

## Commands

- `/persist LIMIT TASK` — start session
- `/persist-status` — show status
- `/persist-stop` — stop a running session (fallback clears all if no session_id)
