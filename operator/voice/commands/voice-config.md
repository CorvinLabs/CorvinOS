---
description: Open / show the voice configuration file
argument-hint: "[show|path|edit]"
---

Verwalte die Voice-Konfiguration. Default-Aktion: anzeigen.
Run:

```bash
bash ${CLAUDE_PLUGIN_ROOT}/scripts/voice_cli.sh config "$ARGUMENTS"
```

- `show` (default): aktuelle Config ausgeben
- `path`: Pfad zur Config-Datei ausgeben
- `edit`: Pfad ausgeben + dem User sagen, dass er die Datei manuell editieren kann

Berichte dem User das Ergebnis auf Deutsch.
