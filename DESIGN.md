# claude-loop Design

## Purpose

claude-loop extends Claude Code with persistent coding loops. It re-prompts Claude after each turn via a stop hook, keeping work going across multiple iterations without manual intervention.

Two modes:

1. **Fixed loop** (`/loop LIMIT TASK`): Re-injects the same task prompt every iteration. Simple, predictable. Good for focused single-track tasks.

2. **Agent loop** (`/loop-agent LIMIT GOALS`): A managing agent reviews the worker's output after each turn and generates a new, specific instruction. The manager maintains a plan, tracks progress, and decides when goals are met. Good for complex multi-step work.

## Limits

The LIMIT argument can be an iteration count or a time limit:

- `5` — 5 iterations (max 999)
- `2h` — 2 hours from now
- `30m` — 30 minutes from now
- `2pm` — today at 2 PM (tomorrow if already past)
- `1400` — military time, today at 14:00 (numbers >= 1000 are always time)
- `14:00` — same as above

Time-based loops run until the deadline, with no iteration cap. Iteration-based loops have no time limit.

The loop ends when any termination condition is met: iteration limit, deadline, task completion (fixed loop keyword), or manager declaring done (agent loop).

## Fixed Loop

State: `.claude/loop.json`

```json
{"iteration": 2, "prompt": "Fix the parser", "total": 5, "deadline": null}
```

or (time-limited):

```json
{"iteration": 2, "prompt": "Fix the parser", "total": null, "deadline": 1709413200}
```

Flow:
```
/loop 5 Fix the parser
  --> parse limit: "5" -> total=5, deadline=null
  --> write loop.json {iteration: 1}
  --> worker gets initial task from slash command text

Stop hook fires:
  --> read loop.json
  --> check deadline or iteration limit
  --> check last_assistant_message for keywords
  --> TASK_COMPLETE? inject verification prompt
  --> REVIEW_OKAY? delete loop.json, done
  --> REVIEW_INCOMPLETE or no keyword? inject work prompt, increment iteration
  --> limit reached? delete loop.json, done
```

## Agent Loop

State: `.claude/agent.json`

```json
{
  "goals": "Build a REST API with tests",
  "plan": "1. Set up project structure\n2. Implement endpoints\n3. Write tests",
  "history": [
    {"instruction": "Set up Flask project structure", "outcome": "Created app.py with Flask skeleton"}
  ],
  "iteration": 2,
  "total": 20,
  "deadline": null
}
```

Flow:
```
/loop-agent 2h Build a REST API with tests
  --> parse limit: "2h" -> total=null, deadline=now+7200
  --> write agent.json {iteration: 1, goals, plan: "", history: []}
  --> worker gets goals directly as first instruction

Stop hook fires:
  --> read agent.json
  --> check deadline or iteration limit
  --> call manager: claude --print --model haiku
      input: goals + plan + history + last_assistant_message
      output: JSON {assessment, plan, instruction, done}
  --> done? delete agent.json, done
  --> limit reached? delete agent.json, done
  --> otherwise: update agent.json with new plan/history, inject instruction
```

### Manager

The manager is a separate, cheap LLM call (`claude --print --model haiku`) that runs between worker turns. It:

- Assesses what the worker accomplished
- Updates its plan
- Generates the next specific instruction
- Decides when goals are fully met

The manager does NOT use tools. It receives the worker's last message as text and makes decisions based on that.

### Worker Prompt

```
# Managed iteration N

You are in a managed coding loop. Follow the instruction below.
Work incrementally, then stop. You will receive your next instruction
after this turn.

## Instruction
{manager's instruction}
```

The worker never declares completion. The manager decides.

### Manager Prompt

```
You are a managing agent overseeing a coding worker.

## Goals
{goals}

## Your Plan
{plan}

## History
{formatted instruction/outcome pairs}

## Worker's Latest Output
{last_assistant_message}

Assess progress. Update your plan. Give the next instruction, or
mark done if all goals are met.

Respond as JSON:
{"assessment": "...", "plan": "...", "instruction": "...", "done": false}
```

### Manager Failure

If the manager call fails (timeout, malformed response, etc.), fall back to a generic work prompt using the goals directly. Don't block the worker.

## Hook Routing

The stop hook checks which state file exists:
1. `agent.json` exists → agent loop logic
2. `loop.json` exists → fixed loop logic
3. Neither → do nothing

## Commands

- `/loop LIMIT TASK` — start fixed loop
- `/loop-agent LIMIT GOALS` — start agent loop
- `/loop-status` — show status of either loop type
- `/loop-stop` — stop either loop type
