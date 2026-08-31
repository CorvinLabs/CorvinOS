---
description: Start every configured bridge (WhatsApp + Telegram + Discord + Slack)
argument-hint: ""
---

Start every configured bridge via `bridge.sh up`. Installs systemd user units, enables linger (services survive reboot/logout) and starts the adapter plus every configured channel.

Run:

```bash
bash ${CLAUDE_PLUGIN_ROOT}/bridges/bridge.sh up
```

Summarize which channels have started for the user.
