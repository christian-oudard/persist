---
description: "Start a persistent coding session."
argument-hint: "[--lock|-l] LIMIT PURPOSE"
allowed-tools: ["Bash(persist:*)"]
hide-from-slash-command-tool: "true"
---

```!
persist <<'_PERSIST_ARGS_'
$ARGUMENTS
_PERSIST_ARGS_
```
