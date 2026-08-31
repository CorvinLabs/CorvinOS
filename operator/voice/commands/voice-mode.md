---
description: Set the voice read-aloud mode — auto (default, threshold-based), full (always read everything), or summary (always summarize)
argument-hint: "auto|full|summary"
---

Set the voice mode. Run:

```bash
bash ${CLAUDE_PLUGIN_ROOT}/scripts/voice_cli.sh mode "$ARGUMENTS"
```

Confirm in one sentence which mode is now active. Modes:

- **auto** — current threshold-based behaviour: short answers are read in full, long ones get summarized.
- **full** — every answer is read aloud completely, no summarization.
- **summary** — every answer is summarized first, even short ones.

Per-turn override: regardless of this default, phrases like "lies das vollständig vor" / "read it in full" force `full` for that one answer; "fass das zusammen" / "summarize this" force `summary`.
