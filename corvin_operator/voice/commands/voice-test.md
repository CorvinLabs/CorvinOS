---
description: Test the speech output in German and English
argument-hint: "[de|en|both]"
---

Teste die TTS-Pipeline. Run:

```bash
bash ${CLAUDE_PLUGIN_ROOT}/scripts/voice_cli.sh test "$ARGUMENTS"
```

Wenn keine Argumente angegeben sind, wird beides getestet (DE + EN).
Berichte dem User auf Deutsch das Ergebnis kurz (eine Zeile).
