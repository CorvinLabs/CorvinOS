---
description: Stop the Slack bridge
argument-hint: ""
---

Stop the Slack bridge daemon. The shared adapter keeps running if other bridges (WhatsApp, Telegram, Discord) are still active.

Run:

```bash
systemctl --user stop corvin-voice-bridge-slack.service
```

Confirm in a single sentence in the user's language.
