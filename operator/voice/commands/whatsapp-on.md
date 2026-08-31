---
description: Start the WhatsApp bridge (daemon + adapter)
argument-hint: ""
---

Start the WhatsApp bridge daemon and the Python adapter in the background.

Voraussetzung: node modules must be installed (`cd operator/voice/whatsapp && npm install`) and the WhatsApp account must be paired (`/whatsapp-pair`).

Run:

```bash
bash ${CLAUDE_PLUGIN_ROOT}/scripts/whatsapp_cli.sh on
```

Confirm to the user that the bridge is running, or point out missing prerequisites.
