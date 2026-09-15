---
description: Start the Slack bridge (daemon + adapter)
argument-hint: ""
---

Start the Slack bridge daemon and the shared Python adapter in the background (systemd user services).

Prerequisite: Bot token (`xoxb-...`) and App-Level token (`xapp-...`) from https://api.slack.com/apps set in `operator/bridges/slack/settings.json` under `slack_bot_token` + `slack_app_token`. The bot must be invited to a channel. If CorvinOS is not installed yet, run `corvin-install`.

Run:

```bash
systemctl --user start corvin-voice-bridge-adapter.service corvin-voice-bridge-slack.service \
  && systemctl --user is-active corvin-voice-bridge-slack.service
```

Confirm to the user that the bridge is running, or point out missing prerequisites.
