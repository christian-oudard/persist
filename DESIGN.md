# persist Design

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

## Session

State: `.claude/persist.json`

```json
{"iteration": 2, "prompt": "Fix the parser", "total": 5, "deadline": null}
```

or (time-limited):

```json
{"iteration": 2, "prompt": "Fix the parser", "total": null, "deadline": 1709413200}
```

Flow:
```
/persist 5 Fix the parser
  --> parse limit: "5" -> total=5, deadline=null
  --> write persist.json {iteration: 1}
  --> worker gets initial task from slash command text

Stop hook fires:
  --> read persist.json
  --> check deadline or iteration limit
  --> check last_assistant_message for keywords
  --> TASK_COMPLETE? inject verification prompt
  --> REVIEW_OKAY? delete persist.json, done
  --> REVIEW_INCOMPLETE or no keyword? inject work prompt, increment iteration
  --> limit reached? delete persist.json, done
```

## Hook Routing

The stop hook checks if `persist.json` exists. If not, it does nothing.

## Commands

- `/persist LIMIT TASK` — start session
- `/persist-status` — show status
- `/persist-stop` — stop a running session
