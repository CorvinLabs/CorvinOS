---
description: Start the Telegram bridge (daemon + adapter)
argument-hint: ""
---

Start the Telegram bridge daemon and the shared Python adapter in the background (systemd user services).

Prerequisite: Bot token via `@BotFather` (/newbot), set in `operator/bridges/telegram/settings.json` under `telegram_token`. If CorvinOS is not installed yet, run `corvin-install`.

Run:

```bash
systemctl --user start corvin-voice-bridge-adapter.service corvin-voice-bridge-telegram.service \
  && systemctl --user is-active corvin-voice-bridge-telegram.service
```

Confirm to the user that the bridge is running, or point out missing prerequisites.
