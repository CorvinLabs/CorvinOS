---
description: Live tail the voice log with bridge activity
argument-hint: "[n]"
---

Tail the voice log live ($VOICE_LOG_FILE), showing inbound and outbound messages of the WhatsApp bridge plus all TTS events. By default the last 30 lines plus live stream. End with Ctrl-C.

Run:

```bash
bash ${CLAUDE_PLUGIN_ROOT}/scripts/whatsapp_cli.sh tail $ARGUMENTS
```

Note to the user: this command blocks the terminal until they end it with Ctrl-C.
