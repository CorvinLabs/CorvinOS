---
description: Show the last N messages from the WhatsApp bridge
argument-hint: "[n]"
---

Show the last N received messages (default 10), reconstructed from the adapter's `processed/` archive.

Run:

```bash
bash ${CLAUDE_PLUGIN_ROOT}/scripts/whatsapp_cli.sh history $ARGUMENTS
```

Return the result to the user as a compact list with timestamp and sender.
