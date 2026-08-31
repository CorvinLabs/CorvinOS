---
description: Remove a chat's persona binding (chat_profile stays otherwise)
argument-hint: "<channel> <chat_key>"
---

Entfernt nur das `persona`-Feld aus `chat_profiles[<chat>]`. Wenn das Profil
dadurch leer wird, wird der ganze Chat-Eintrag entfernt — Default-Verhalten
greift wieder.

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/cowork" unbind "$1" "$2"
```

Gib das Ergebnis 1:1 wieder.
