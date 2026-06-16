# persist

Persistent coding sessions for Claude Code. Re-injects the same task each iteration using a stop hook, keeping work going across multiple turns without manual intervention.

```
Work iteration  -> TASK_COMPLETE      -> verification prompt
Verification    -> REVIEW_OKAY       -> done
Verification    -> REVIEW_INCOMPLETE  -> back to work
Any iteration   -> limit reached      -> done
```

## Install

With uv:
```bash
uv tool install persist
```

Or with pipx:
```bash
pipx install persist
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
            "command": "persist hook"
          }
        ]
      }
    ]
  }
}
```

## Usage

```
/persist:go 10 Implement a function that solves the traveling salesman problem
/persist:go 2h Refactor the database layer
/persist:go 2pm Ship the feature branch
```

The first argument is a limit -- either an iteration count (max 999) or a time limit (`2h`, `30m`, `2pm`, `1400`, `14:00`). Numbers >= 1000 are interpreted as military time. The rest is the task.

To check status:
```
/persist:status
```

To cancel a running session:
```
/persist:stop
```

Or run `persist stop` from a terminal in the project directory.

## Scripting

`persist active` is a silent predicate for hooks and scripts: it exits 0 when a live session is running in the current project, 1 otherwise (including a session that has passed its limit but not yet been cleaned up). For example, ring a terminal bell on every stop except while a session loop is running:

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "persist active || printf '\\a' > /dev/tty"
          }
        ]
      }
    ]
  }
}
```

`persist status` is for humans and always exits 0, so don't guard on it.
