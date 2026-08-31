---
description: Send a test message to a WhatsApp number
argument-hint: "<jid> (e.g. 491701234567@s.whatsapp.net)"
---

Send a short test message via the bridge to the given WhatsApp address. Verifies end-to-end delivery without invoking Claude.

Argument $ARGUMENTS must be a complete WhatsApp JID, e.g. `491701234567@s.whatsapp.net`.

Run:

```bash
bash ${CLAUDE_PLUGIN_ROOT}/scripts/whatsapp_cli.sh test $ARGUMENTS
```

Confirm in a single sentence that the test message has been queued in the outbox, or point out a malformed JID.
