---
description: Bind a persona to a chat (writes settings.json, hot-reload kicks in immediately)
argument-hint: "<channel> <chat_key> <persona>"
---

Writes `chat_profiles[<chat>].persona = <persona>` into
`operator/bridges/<channel>/settings.json`. The adapter reads the
file fresh per inbox message — no restart needed.

Arguments:
1. `channel` — `whatsapp`, `telegram`, `discord`, or `slack`
2. `chat_key` — `chat_id` (Telegram/Discord/Slack) or sender JID (WhatsApp)
3. `persona` — name from `/cowork-list`

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/cowork" bind "$1" "$2" "$3"
```

Reproduce the result verbatim.
