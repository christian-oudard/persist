---
description: "Start a managed agent loop."
argument-hint: "NUM_ITERATIONS GOALS"
allowed-tools: ["Bash(claude-loop:*)"]
hide-from-slash-command-tool: "true"
---
/agent $ARGUMENTS

```!
claude-loop agent <<'_LOOP_ARGS_'
$ARGUMENTS
_LOOP_ARGS_
```
