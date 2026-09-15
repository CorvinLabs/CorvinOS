---
description: Show the Slack bridge status
argument-hint: ""
---

Show the Slack bridge status: is the daemon running, is the adapter running, are there pending outbox items.

Run:

```bash
systemctl --user is-active corvin-voice-bridge-slack.service corvin-voice-bridge-adapter.service \
  ; curl -s --max-time 2 http://127.0.0.1:7894/status 2>/dev/null \
    | python3 -c 'import sys,json; d=json.load(sys.stdin); print("paired:", d.get("paired"), "bot:", d.get("bot_user_id"), "whitelist:", d.get("whitelist_size"), "pending:", d.get("pending_outbox"))' 2>/dev/null \
    || echo "(HTTP API not reachable)"
```

Summarize for the user in a single sentence.
