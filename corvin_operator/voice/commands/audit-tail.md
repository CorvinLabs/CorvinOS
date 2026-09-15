---
description: Show the last N events from the voice + forge audit log
argument-hint: "[N]"
---

Streams the most recent N events from the SHA-chained audit log
(default 20). Each line shows timestamp, severity, event_type, channel,
chat_key, user, persona — useful when something just went wrong and
you want to see what the bridge / forge plugin emitted.

Run:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/voice_audit.py tail --limit "${1:-20}"
```

Format the output as a compact table the user can scan. Group by
severity if there are warnings or errors so they stand out.
