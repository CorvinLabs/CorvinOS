# Voice Audio Handling — Data Retention Reference

**Layer:** 23 (Speech-to-Text)
**Regulatory basis:** GDPR Art. 5 (storage limitation), Art. 32 (security measures)
**ADR:** ADR-0073 G-007

---

## Lifecycle of an Audio File

When a user sends a voice note via Discord, Telegram, or WhatsApp, the bridge daemon
downloads the audio file and places it in the inbox directory. From that moment:

```
[Bridge daemon] → inbox/<msg-id>.ogg
      ↓ process_one() picks up envelope
[adapter.py]    → transcribe_audio(audio_path)   ← STT call (OpenAI Whisper or local)
      ↓ transcription complete (success or failure)
[adapter.py]    → _delete_audio_post_stt()       ← IMMEDIATE deletion (ADR-0073 G-007)
      ↓
  audio_path.unlink()                             ← file gone from disk
  _audit_event("voice.audio_deleted", ...)        ← deletion record in audit chain
      ↓
[_move_inbox_with_attachments()]                  ← audio no longer exists; src.exists()=False → skipped
```

**The audio file is deleted from disk immediately after STT returns** — regardless of
whether transcription succeeded or failed. It is never moved to the PROCESSED directory.

---

## Audit Events

Two audit events are emitted per voice note:

| Event | When | Details |
|---|---|---|
| `voice.transcribed` | STT succeeds | `provider`, `lang`, `audio_s`, `wall_clock_s`, `chars` — NEVER transcript text |
| `voice.transcribe_failed` | STT fails | `provider`, `reason`, `wall_clock_s` |
| `voice.audio_deleted` | After deletion succeeds | `file_size_bytes`, `sha256_prefix` (8 hex chars), `deleted: true` |
| `voice.audio_delete_failed` | Deletion fails | `file_size_bytes`, `sha256_prefix`, `deleted: false` |

**Must NOT do:** Put transcript text, audio content, or full file paths in any audit event detail.

---

## STT Provider and Egress

When `CORVIN_STT_PROVIDER=openai_whisper` (default), the audio bytes are transmitted
to OpenAI's Whisper API. This is an international data transfer under GDPR Art. 44.

Required operator actions:
- Ensure a Data Processing Agreement with OpenAI covers Whisper API usage.
- For EU_PRODUCTION deployments (`spec.egress.enabled: true`), ensure `api.openai.com`
  is in the tenant's `allowed_hosts` list if whisper is used.
- For zero-egress deployments: set `CORVIN_STT_PROVIDER=local` and ensure
  the `local` provider is configured (`pywhispercpp` — ADR-0185 M1; the legacy
  `faster-whisper` package remains available as an opt-in
  `CORVIN_STT_LOCAL_ENGINE=faster-whisper` alternative).

---

## Retention Summary

| Data | Retention | Location | Security |
|---|---|---|---|
| Audio file (inbox) | Until STT call returns, then deleted immediately | `INBOX/` tempdir | mode 0600 (set by bridge daemon) |
| Audio file (PROCESSED) | **Never stored there** (deletion happens first) | — | — |
| Transcribed text | Session TTL (default 7 days via L28 recall) | `recall.db` | mode 0600 |
| Audit metadata | 7 years (L37 default) | `audit.jsonl` (sealed) | AES-256-GCM |

---

## What Operators Must Configure

1. **DPA with STT provider** — required before enabling voice notes in production.
2. **`allowed_hosts`** — for EU_PRODUCTION preset, add the STT provider's API domain.
3. **`local_whisper`** — use for CONFIDENTIAL-classified tenants where audio must not egress.
4. **Verify audit chain** — `voice-audit verify` should show `voice.audio_deleted` events
   matching the count of `voice.transcribed` events. A mismatch indicates files not being
   cleaned up.

---

## Self-Test

The adapter boot self-test (L11) checks for orphaned audio files:
- Scans the configured inbox temp directory for audio files (`*.ogg`, `*.mp3`, `*.m4a`, `*.opus`)
  older than 60 seconds.
- Emits `WARNING` if any found (signals a cleanup failure in a previous run).
- Does NOT delete them automatically (operator may need to investigate).

---

## Round-Trip Self-Test — `corvin-voice doctor` (ADR-0185 M5)

Distinct from the L11 orphan-file scan above: `corvin-voice doctor` is a real,
non-mocked functional check that the voice subsystem actually works end to end —
not just that files are being cleaned up. It exists to close the class of bug
that shipped silently for a long time before ADR-0185 (a missing `import asyncio`
in `adapter.py` broke every `edge-tts` call on every platform, swallowed into a
log line nobody read).

What it does, in order:
1. **STT provider table** — reports `local` (`pywhispercpp`) and `openai`
   readiness with a human reason (missing package, missing API key, etc.).
2. **STT round-trip** — actually transcribes `operator/voice/scripts/fixtures/stt_sample.wav`
   through the real resolver chain and fails loudly if the returned text is empty.
3. **TTS provider table** — reports `openai`, `edge-tts`, and `piper` readiness.
4. **TTS round-trip** — actually calls `adapter.synthesize_voice_note(...)` and fails
   loudly if no audio file (or a zero-byte file) comes back.

```bash
corvin-voice doctor
```

Exit code `0` only when both round-trips pass; `1` otherwise (per-check `PASS`/`FAIL`
lines and an `OVERALL` summary are printed either way — no stack trace only). CI
runs the same command, unmodified, on `ubuntu-latest` / `macos-latest` /
`windows-latest` (`.github/workflows/voice-e2e.yml`) so a regression here is
caught before release, not discovered by a user months later.
