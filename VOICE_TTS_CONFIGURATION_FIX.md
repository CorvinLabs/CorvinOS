# Voice TTS Provider — Discord voice summary spoke via edge-tts instead of OpenAI

**Status:** RESOLVED 2026-09-18 [ADR-0883]. This file supersedes the first
analysis committed the same day at 13:37, which was wrong on three points
(see "Corrections" below). The durable description lives in
`docs/bridge-setup.md` § "Pinning the TTS provider"; this file is the incident
record.

## What was actually happening

The Discord voice summary is synthesised by the **bridge adapter**
(`corvin-voice-bridge-adapter.service`, `corvin_operator/bridges/shared/adapter.py::synthesize_voice_note`),
not by `say.py` and not by a `corvin-voice` unit (no such unit exists).

1. **2026-09-16 20:12:07** — `scripts/rotate_corvin_keys_blocker3.py` (Track 3
   credential rotation) replaced every OpenAI key in
   `~/.config/corvin-voice/service.env` with `sk-proj-PLACEHOLDER-…-20260916_201207`.
   The running adapter kept the previous, valid key in its process
   environment, so nothing changed for two days.
2. **2026-09-18 08:59** — reboot. The adapter loaded the placeholders through
   the unit's `EnvironmentFile=`. From 09:03 every turn logged
   `synth OpenAI failed: Error code: 401 … ***1207` and fell through to
   edge-tts (Microsoft). Roughly one turn in six edge-tts failed too and Piper
   spoke.
3. **13:51** — `service.env` was repaired with a valid key (verified against
   `GET /v1/models` → 200). No unit was restarted, so the adapter and the
   Discord daemon still held the placeholder: `provider_keys.resolve_key`
   checks the process environment before any file, and the file is only read
   at unit start. The last 401 with the placeholder suffix is from 15:03.

## Corrections to the 13:37 analysis

| Claim (13:37) | Reality |
|---|---|
| `openai` and `edge-tts` SDKs not installed | Both installed in the adapter's interpreter (`.venv`: openai 3.1.0, edge-tts 7.2.8). The check was run against the wrong Python. |
| Restart `corvin-voice` | The units are `corvin-voice-bridge-adapter` and `corvin-voice-bridge-discord`. |
| Keys are placeholders | True until 13:51; stale afterwards. The *running process* still had them. |

## Fix

1. Restart the daemons after any `service.env` change (this is now documented
   in `docs/bridge-setup.md`):
   ```bash
   systemctl --user restart corvin-voice-bridge-adapter corvin-voice-bridge-discord
   ```
2. ADR-0883 — the bridge adapter honours the TTS pin (`CORVIN_TTS_PROVIDER`
   env, then `profile.tts_provider`), Variant A: a pinned cloud provider falls
   back to local Piper only, never to the other cloud. `profile.json` on this
   install is pinned to `openai`.
3. `VoiceTtsPinnedProviderReset` (ACO healer) now resolves the key through
   `provider_keys.resolve_key` instead of the console process's own
   `OPENAI_API_KEY`, so it no longer clears a valid pin.

## Second finding after the restart (15:48): the OpenAI account has no credits

With the valid key loaded, `POST /v1/audio/speech` answers
`429 insufficient_quota / credit_balance_exhausted`. The adapter treats every
429 as quota, backs off for 60 minutes and — by design — logged nothing, so the
only evidence was the fallback voice. Under the `openai` pin the fallback is
now local Piper (never edge-tts). Fix on the OpenAI side: add credits at
platform.openai.com → Billing. The backoff clears after 60 minutes or on
`systemctl --user restart corvin-voice-bridge-adapter`. Since ADR-0883 the
adapter logs one content-free line per backoff window:
`synth OpenAI: quota/credits exhausted (429) — backing off 60 min, using fallback tier`.

## Still open (out of scope of ADR-0883)

- `CORVIN_STT_OPENAI_KEY` (and the vault entries `openai_api_key` /
  `stt_openai_api_key`, which hold that same value) return 401 — OpenAI
  Whisper STT is dead and falls back to local whisper.
- `summarize.py` runs degraded (Hermes + CLI timeouts), so voice summaries are
  near-verbatim (~4 000 chars) and occasionally exceed the adapter's 15 s
  OpenAI timeout.
- The rotation scripts write placeholders into the live `service.env` without
  restarting units or validating the replacement.
