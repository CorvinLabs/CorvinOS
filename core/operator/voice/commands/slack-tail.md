---
description: Live log of the Slack bridge
argument-hint: "[n]"
---

Tail the journald logs of the Slack daemon + adapter live. End with Ctrl-C.

Run:

```bash
journalctl --user -f -n ${ARGUMENTS:-30} \
  -u corvin-voice-bridge-slack.service \
  -u corvin-voice-bridge-adapter.service
```

Note to the user: this command blocks the terminal until they end it with Ctrl-C.
