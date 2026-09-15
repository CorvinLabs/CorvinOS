---
description: Stop the Telegram bridge
argument-hint: ""
---

Stop the Telegram bridge daemon. The shared adapter keeps running if other bridges (WhatsApp, Discord, Slack) are still active.

Run:

```bash
systemctl --user stop corvin-voice-bridge-telegram.service
```

Confirm in a single sentence in the user's language.
