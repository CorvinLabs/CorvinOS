---
description: Record from the microphone and transcribe via Whisper
argument-hint: "[--seconds N] [--lang de|en|auto]"
---

Records a short voice input from the microphone and transcribes it with OpenAI Whisper.

Arguments $ARGUMENTS:
- `--seconds N`: recording duration in seconds (default 15).
- `--lang de|en|auto`: language hint (default auto).

Run:

```bash
bash ${CLAUDE_PLUGIN_ROOT}/scripts/listen.sh $ARGUMENTS
```

Return the transcript to the user as a block (in a code block or italics) so they can review it and reuse it as the next prompt. If the recording was empty or the API returns an error, explain it to the user.
