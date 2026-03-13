---
description: "Start a persistent coding session."
argument-hint: "LIMIT TASK"
allowed-tools: ["Bash(persist:*)"]
hide-from-slash-command-tool: "true"
---

```!
persist <<'_PERSIST_ARGS_'
$ARGUMENTS
_PERSIST_ARGS_
```

The `persist` command above has initialized the session. Now execute the task described in the arguments. Work incrementally: implement one piece, verify it works, then stop. You will be re-prompted after each iteration.
