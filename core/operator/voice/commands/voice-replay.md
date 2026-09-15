---
description: Replay the last N voice outputs (no new API call)
argument-hint: "[n] | --list [n]"
---

Replay the most recent voice outputs from the cache. Argument $ARGUMENTS:

- empty or number `n`: play the last n replies (default 1).
- `--list [n]`: show the last n entries with timestamp and a short snippet, no playback.

Run:

```bash
bash ${CLAUDE_PLUGIN_ROOT}/scripts/replay.sh $ARGUMENTS
```

If history is empty, explain to the user in a single sentence that nothing has been read aloud yet. Otherwise confirm briefly what is being replayed.
