---
description: Start the Discord bridge (daemon + adapter)
argument-hint: ""
---

Start the Discord bridge daemon and the shared Python adapter in the background (systemd user services).

Prerequisite: Bot token from https://discord.com/developers/applications (Bot tab → Reset Token), set in `operator/bridges/discord/settings.json` under `discord_token`. The bot must be invited to a server. If CorvinOS is not installed yet, run `corvin-install`.

Run:

```bash
systemctl --user start corvin-voice-bridge-adapter.service corvin-voice-bridge-discord.service \
  && systemctl --user is-active corvin-voice-bridge-discord.service
```

Confirm to the user that the bridge is running, or point out missing prerequisites.
