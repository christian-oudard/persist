# claude-loop

A coding loop for Claude Code. Re-injects the same task each iteration using a stop hook, keeping work going across multiple turns without manual intervention.

```
Work iteration  -> TASK_COMPLETE      -> verification prompt
Verification    -> REVIEW_OKAY       -> done
Verification    -> REVIEW_INCOMPLETE  -> back to work
Any iteration   -> limit reached      -> done
```

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

### 1. Add the skills

Copy each `skills/*/` directory to `~/.claude/skills/`.

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

```
/loop 10 Implement a function that solves the traveling salesman problem
/loop 2h Refactor the database layer
/loop 2pm Ship the feature branch
```

The first argument is a limit -- either an iteration count (max 999) or a time limit (`2h`, `30m`, `2pm`, `1400`, `14:00`). Numbers >= 1000 are interpreted as military time. The rest is the task.

To check status:
```
/loop-status
```

To cancel a running loop:
```
/loop-stop
```

Or run `claude-loop stop` from a terminal in the project directory.
