---
description: Start WhatsApp pairing (QR code in the terminal)
argument-hint: ""
---

Start the pairing flow. A QR code appears in the terminal — the user has to scan it in the WhatsApp app (Settings → Linked Devices → Link a device).

Important: this command blocks the terminal until pairing is complete. Explain to the user that they need to scan the QR code directly in the terminal, and that the script terminates automatically once pairing succeeds.

Run:

```bash
bash ${CLAUDE_PLUGIN_ROOT}/scripts/whatsapp_cli.sh pair
```
