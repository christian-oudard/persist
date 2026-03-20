# persist Spec

## Purpose

persist extends Claude Code with persistent coding sessions. It re-prompts Claude after each turn via a stop hook, keeping work going across multiple iterations without manual intervention.

## Commands

- `/persist LIMIT PURPOSE` , start a session that re-injects PURPOSE every iteration
- `/persist (-l/--lock) LIMIT PURPOSE` , start a session that ignores task completion (runs until limit)
- `/persist-status` , show session status
- `/persist-stop` , stop a running session

## Limits

The LIMIT argument can be an iteration count or a time limit:

- `5` , 5 iterations (max 999)
- `2h` , 2 hours from now
- `30m` , 30 minutes from now
- `2pm` , today at 2 PM (tomorrow if already past)
- `1400` , military time, today at 14:00 (numbers >= 1000 are always time)
- `14:00` , same as above
- `forever` , no limit, runs until stopped or task complete

Time-based sessions run until the deadline, with no iteration cap. Iteration-based sessions have no time limit. Forever sessions have neither, ending only via task completion or `/persist-stop`.

## Termination

A session ends when any of these conditions is met:

- **Iteration limit** reached
- **Deadline** reached
- **Task completion**: Claude outputs TASK_COMPLETE or REVIEW_OKAY

With `--lock`, task completion keywords are ignored. The session runs until its iteration or deadline limit.

## Session Isolation

Every session is scoped to a Claude Code session_id. Multiple agents can share a project without interfering with each other's sessions.

## Work Prompt

Each iteration, persist injects a work prompt that includes:

- **First iteration**: an explanation of the loop mechanism
- **Continuation iterations**: orientation via git log, asking "what is the most valuable thing I could work on next?"
- **Always**: guidance toward incremental work and persistence through difficulty
- **Unless --lock**: instructions for signaling task completion

Design principles:
- **Autonomy**: the agent decides what to work on, not the prompt
- **Purpose over compliance**: heading says "Purpose" not "Task"
- **Anti-helplessness**: continuation prompt reviews prior work, establishing that previous actions had effects
- **Growth framing**: difficulty is expected, adjust approach rather than give up
- **Factual exit**: TASK_COMPLETE is a truth claim, not a way to escape frustration
