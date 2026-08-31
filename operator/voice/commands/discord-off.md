---
description: Stop the Discord bridge
argument-hint: ""
---

Stop the Discord bridge daemon. The shared adapter keeps running if other bridges (WhatsApp, Telegram, Slack) are still active.

Run:

```bash
systemctl --user stop corvin-voice-bridge-discord.service
```

Confirm in a single sentence in the user's language.
