---
description: Start the Email bridge (daemon + adapter)
argument-hint: ""
---

Start the Email bridge daemon and the shared Python adapter in the background (systemd user services).

Prerequisite: IMAP + SMTP credentials (use an app-specific password) set in `operator/bridges/email/settings.json`. Whitelist must contain the sender addresses allowed to talk to the bot. If CorvinOS is not installed yet, run `corvin-install`.

Run:

```bash
systemctl --user start corvin-voice-bridge-adapter.service corvin-voice-bridge-email.service \
  && systemctl --user is-active corvin-voice-bridge-email.service
```

Confirm to the user that the bridge is running, or point out missing prerequisites.
