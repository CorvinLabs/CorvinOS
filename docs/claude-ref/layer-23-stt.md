# Layer 23 — Speech-to-Text (metadata-only audit)

> Load when working on voice-note transcription, STT providers, or the
> `voice.transcribed` / `voice.transcribe_failed` audit events. Compliance
> summary in CLAUDE.md § Compliance Baseline ("Voice-transcribe audit").
> The full engineering reference (provider contract, model tiers, env knobs,
> incident history) is
> [layer-voice-ldd.md § Layer 23](layer-voice-ldd.md#layer-23--speech-to-text-engine-agnostic-pluggable-providers).

## What this layer does

STT is the boundary between bridge voice-notes and any WorkerEngine (Layer 22).
It runs at the bridge/adapter layer **before** an engine subprocess is spawned,
so engines never see audio — only the resulting text. The implementation is a
small provider package:

```
operator/voice/scripts/stt/
├── base.py            # STTProvider Protocol, TranscriptResult, STTError tree
├── openai_whisper.py  # OpenAI audio transcription (default gpt-4o-mini-transcribe)
├── local_whisper.py   # pywhispercpp / whisper.cpp — offline, air-gap, EU-residency
└── resolver.py        # provider chain + fallback (default: openai → local)
```

Audit emission and audio deletion live in the bridge adapter:
`operator/bridges/shared/adapter.py` (`_emit_transcribe_ok`,
`_emit_transcribe_failed`, `_delete_audio_post_stt`). Event severities are
registered in `operator/forge/forge/security_events.py::EVENT_SEVERITY`; the
Prometheus counters in `core/gateway/corvin_gateway/audit_metrics.py`.

## Provider resolution

1. `CORVIN_STT_PROVIDER=<openai|local>` pins one provider — no fallback,
   fail-loud if unavailable (policy path: "this tenant must never use OpenAI").
2. Else `CORVIN_STT_CHAIN=<n1>,<n2>` — operator-supplied order
   (e.g. `local,openai` for local-first).
3. Else `DEFAULT_CHAIN = ("openai", "local")`; `openai` is a no-op without an
   API key, so a key-less box degrades to local-only automatically.

The chain falls through on `STTProviderUnavailable` / `STTTranscriptionFailed`,
and on `STTTimeout` only while a later provider remains. A caller-supplied
`timeout_s` is a ceiling for the whole chain, not a per-provider allowance.

## The load-bearing invariant: metadata only, never text (GDPR Art. 5)

`TranscriptResult.text` is the only PII-bearing field. The audit events record
**only** metadata — provider, language, durations and the character count
(`chars`, derived from `len(text)` in `base.py`) — never the transcript:

| Event | Severity | `details` |
|---|---|---|
| `voice.transcribed` | INFO | `msg_id`, `provider`, `lang`, `audio_s`, `wall_clock_s`, `chars` |
| `voice.transcribe_failed` | WARNING | `msg_id`, `reason` (`timeout` / `provider-error` / `package-unreachable`), `error` (capped at 200 chars), `wall_clock_s` |
| `voice.audio_deleted` / `voice.audio_delete_failed` | INFO / WARNING | file size and an 8-hex-char SHA-256 prefix of the audio, emitted when the audio file is deleted right after transcription (G-007, ADR-0073 — storage limitation); deletion is best-effort and a failure is audited, never silent |

All three ride the unified hash chain through `_audit_event`, so
`voice-audit verify` covers them; `msg_id` cross-references
`bridge.message_received` for end-to-end tracing of one voice note.
`operator/voice/scripts/test_stt.py` fails if a `voice.transcribed` event ever
carries a `text` field or the transcript content.

Metrics: `corvin_voice_transcribed_total{stt_provider}` and
`corvin_voice_transcribe_failed_total{reason}` (label values are allow-listed).

## Operator knobs

| Env var | Effect |
|---|---|
| `CORVIN_STT_PROVIDER` | Pin one provider (no fallback) |
| `CORVIN_STT_CHAIN` | Override the default chain order |
| `CORVIN_STT_OPENAI_MODEL` | OpenAI model (default `gpt-4o-mini-transcribe`) |
| `CORVIN_STT_OPENAI_KEY` / `OPENAI_API_KEY` | API key; falls back to `~/.config/corvin-voice/.env` |
| `CORVIN_STT_LOCAL_MODEL` | GGML model name; unset = RAM-adaptive tier (`base-q5_1` / `small-q5_1` / `medium-q5_0`) |
| `CORVIN_STT_LOCAL_ENGINE` | `faster-whisper` opts into the CTranslate2 engine; never the default |
| `BRIDGE_TRANSCRIBE_TIMEOUT` | Per-provider budget in seconds |

## Must NOT do

- Don't add the transcript (or any substring of it) to `voice.transcribed` /
  `voice.transcribe_failed` details — `chars` is the only text-derived field.
- Don't keep the audio file after transcription; `_delete_audio_post_stt` is
  part of the turn, not an optional cleanup.
- Don't route `local` through a network provider: it exists for air-gapped and
  data-residency deployments.
- Don't surface raw provider exception text in the console voice-status panel
  (`provider_status()` returns curated, non-leaky `detail` strings only).
