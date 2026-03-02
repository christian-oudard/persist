# claude-loop

An improved coding loop for Claude Code.

## Two Modes

**Fixed loop** (`/loop LIMIT TASK`): Re-injects the same task each iteration. Simple, predictable. Good for focused tasks.

**Agent loop** (`/loop-agent LIMIT GOALS`): A managing agent (Haiku) reviews worker output after each turn and generates adaptive instructions. It maintains a plan, tracks progress, and decides when goals are met. Good for complex multi-step work.

## Fixed Loop

```
Work iteration  -> TASK_COMPLETE     -> verification prompt
Verification    -> REVIEW_OKAY      -> done
Verification    -> REVIEW_INCOMPLETE -> back to work
Any iteration   -> limit reached     -> done
```

## Agent Loop

```
Worker turn ends -> manager reviews output -> generates next instruction
Manager says done -> loop ends
Limit reached     -> done
```

The manager runs as `claude --print --model haiku` between worker turns. It receives the goals, its own plan, work history, and the worker's latest output. It returns the next specific instruction for the worker.

## Install

With uv:
```bash
uv tool install claude-loop
```

Or with pipx:
```bash
pipx install claude-loop
```

## Setup

### 1. Add the slash commands

Copy `commands/*.md` to `~/.claude/commands/`.

### 2. Add the stop hook

Add this to your `~/.claude/settings.json` under `"hooks"`:

```json
{
  "hooks": {
    "Stop": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "claude-loop hook"
          }
        ]
      }
    ]
  }
}
```

## Usage

Fixed loop:
```
/loop 10 Implement a function that solves the traveling salesman problem
/loop 2h Refactor the database layer
```

Agent loop:
```
/loop-agent 20 Build a REST API for a todo app with CRUD operations and tests
/loop-agent 2pm Ship the feature branch
```

The first argument is a limit — either an iteration count (max 999) or a time limit (`2h`, `30m`, `2pm`, `1400`, `14:00`). Numbers >= 1000 are interpreted as military time. The rest is the task/goals.

To cancel either type of loop:
```
/loop-stop
```

Or run `claude-loop stop` from a terminal in the project directory.
