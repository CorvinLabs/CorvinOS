---
description: Stop the Email bridge
argument-hint: ""
---

Stop the Email bridge daemon. The shared adapter keeps running if other bridges (WhatsApp, Telegram, Discord, Slack) are still active.

Run:

```bash
systemctl --user stop corvin-voice-bridge-email.service
```

Confirm in a single sentence in the user's language.
