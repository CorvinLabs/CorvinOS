# First Run — Install-Time Language Default + Spoken Onboarding

Two related but independent asks, covered together because both live in the "very first
experience with CorvinOS" window: (1) the CLI installer should ask the user's language once,
so voice and text default correctly from turn one; (2) the very first time the web console
opens, Corvin should introduce itself out loud, warm up the models, and run a real self-check
— turning a silent, potentially-broken cold start into an audible, verified "I'm ready" moment.

**Status: implemented.** No ADR filed — evaluated against the repo's ADR gate at
implementation time: a real design choice was made (async job/poll for the health-check,
greeting text composed from the existing `i18n` bundle), but none of the structural triggers
fire — no new protocol/wire-format shared across repos, no new security/compliance mechanism,
no irreversible fail-open/closed default (the check is designed to never block), no cross-repo
binding. Reason to skip: this connects existing pieces behind one new endpoint and one new
installer call, it doesn't introduce a new structural contract. The design below is unchanged
from the original concept pass; "Implementation notes" blocks mark where the shipped code
differs from what was originally sketched.

## 1. Concept 1 — install-time language default

### Current state (verified in code)

CorvinOS already has almost all of this — it just doesn't connect two pieces that exist
independently:

| Piece | What it does | Where |
|---|---|---|
| Install-time language picker | Detects system locale, shows a numbered menu of 12 languages (or auto-picks in `--yes` mode), downloads the matching Piper TTS voice | `corvinOS/installer/steps/piper.py::_setup_model`/`_detect_language` |
| Where that choice is saved | `piper_model_<lang>` paths + `lang_default` written to `~/.config/corvin-voice/config.json` | `piper.py::_save_model_config` |
| What actually controls text/LLM output language | A **separate** file, `profile.json`'s `display_language` key, resolved at runtime via `i18n.py::resolve()`'s fallback chain: explicit override → per-chat `language` → `profile.display_language` → bridge locale → `"en"` | `operator/bridges/shared/profile.py`, `operator/bridges/shared/i18n.py:200` |
| Runtime language change | `/lang set <code>` — already fully built, writes `profile.display_language` | `operator/voice/scripts/lang_cli.py` |
| Per-turn auto-detect (independent layer) | The console frontend already re-detects language from each typed/spoken message and feeds it straight into TTS/STT, regardless of the persistent default | `ConsoleAssistant.tsx:289-410` (`convLang` state) |

**The actual gap:** the installer's language picker only feeds the Piper **voice accent**
(`config.json`). It never touches `profile.display_language` — so a fresh install where the
user picks "Deutsch" gets German-*sounding* TTS but the LLM's actual text/reply language
still defaults to `"en"` (i18n's fallback) until the user manually runs `/lang set de`. The
one real question CorvinOS asks at install time doesn't reach the one setting that actually
controls default reply language.

### Design

1. **Close the propagation gap, don't add a new question.** In `piper.py::_save_model_config`
   (or immediately after it), also write the SAME chosen `lang` into `profile.display_language`
   via the existing `profile.set_value()` API — one extra call, reusing the exact mechanism
   `/lang set` already uses, so there is no second source of truth to keep in sync.
2. **This is a seed, never a lock.** `profile.display_language` is already designed as an
   overridable default (that's its whole purpose in the `i18n.resolve()` chain) — nothing
   about this change touches `/lang set`, per-chat overrides, or the frontend's per-turn
   auto-detect. A user who picked German at install time and later says "switch to English"
   changes it the same way they already can today.
3. **`--yes` / non-interactive installs** already auto-pick the detected system locale
   (`_detect_language()`'s OS-locale probe) without prompting — this seeding applies there
   too, so even a silent/scripted install gets a sensible non-English default when the host
   OS itself isn't set to English, instead of silently defaulting to `"en"` regardless.
4. **STT needs no equivalent change** — `pywhispercpp`/whisper.cpp is inherently multilingual
   (confirmed: the STT installer step picks a RAM tier, not a language), so there is nothing
   to seed there.

### Non-goals
- No new interactive prompt — reuses the existing one.
- No change to the runtime override chain, `/lang` command, or per-turn auto-detect.
- No attempt to seed per-chat (Discord/WhatsApp) language independently — those already
  resolve through bridge-supplied locale first, ahead of `profile.display_language`, per the
  existing `i18n.resolve()` order.

### Implementation notes
Shipped exactly as designed. `piper.py::_save_model_config` calls a new
`_seed_profile_display_language(lang)` right after writing `config.json`. One wrinkle not
anticipated in the original design: `operator/` has no `__init__.py` (the name collides with
the stdlib `operator` module, which is essentially always already cached in `sys.modules`), so
`import operator.bridges.shared.profile` is not actually importable from the installer's
process in practice. `_seed_profile_display_language` therefore tries `corvin_console.profile`
first (a new force-include wheel shim added in `pyproject.toml`, mirroring the existing
`hermes_bootstrap.py`/`engine_detection.py` shims) and falls back to putting
`operator/bridges/shared/` on `sys.path` and importing the bare `profile` module — the same
pattern `lang_cli.py` and `adapter.py` already use. Best-effort: wrapped in `try/except
Exception: pass`, voice setup never fails because of it. Tests:
`tests/test_installer_piper.py`.

## 2. Concept 2 — first-boot spoken onboarding + warm-up + self-check

### Current state (verified in code)

| Piece | What it does | Where |
|---|---|---|
| First-run signal | `GET /setup/status` returns `first_run: true` until a marker file is written; the web console shows a 3-step wizard (`welcome → engine → bridge → done`) | `routes/setup.py` (`_SETUP_COMPLETE_PATH`, `_ONBOARDING_JSON_PATH`), `SetupGate.tsx` |
| The current welcome step | A static screen: logo, "Your AI operating system is ready", a "Let's go" button. **No audio, no health check, no warm-up today.** | `SetupGate.tsx::WelcomeStep` (line 133) |
| Periodic self-healing (already runs, but not synchronously) | ACO Boot-Healer: engine+voice readiness (starts Ollama if offline, installs edge-tts if missing), chat-subsystem liveness check — first cycle 8s after boot, then every 5 min | `core/console/corvin_console/aco/boot_healer.py` |
| L44 classifier health | Probes Ollama for the house-rules model, logs actionable warnings, never blocks boot | `house_rules.py::house_rules_boot_health_check` |
| The REAL end-to-end pipeline check to reuse | `corvin-voice doctor` — genuine (non-mocked) STT round-trip on a fixture WAV, genuine TTS round-trip via `synthesize_voice_note`, a dedicated Piper-offline-tier check | `operator/voice/scripts/voice_doctor.py` |
| Model warm-up (Hermes/local only) | `ensure_hermes_ready()` — starts Ollama if needed, sends a cheap `keep_alive: 30m` warm prompt | `agents/hermes_engine.py:154`; also done once at install time in `install.ps1:216-233` |
| TTS playback + autoplay-block handling (already built, reusable) | `speak()` → `ttsBlob()` → `audioRef.play()`; on autoplay rejection, sets a `"blocked"` state and shows a tap-to-play banner | `pages/chat.tsx` (~line 1190-1240), same pattern in `browser.tsx` |

Every piece Concept 2 needs already exists somewhere in the codebase — the gap is purely that
`WelcomeStep` never calls any of it.

### Design

**Backend — one new endpoint, `POST /setup/welcome-check` (or a field added to
`GET /setup/status`):**
- Runs, synchronously, in this order: (a) `house_rules_boot_health_check`'s Ollama/classifier
  probe, (b) `ensure_hermes_ready()` if Hermes is the configured/fallback engine, (c) the STT
  and TTS round-trip checks `voice_doctor.py` already implements (call its functions
  directly, not the CLI), (d) a single cheap turn against whichever engine is actually
  configured as primary (Claude Code or Hermes) — the closest thing to a "warm-up" a cloud
  API has: proves auth, network egress, and model availability all work, not just that a
  process can start.
- Returns a structured `{component: "ok"|"degraded"|"unavailable", detail}` map — never
  raises; a broken component degrades the greeting's wording (§ below), it never blocks the
  wizard.
- Resolves the greeting text server-side via `i18n`, using the language just seeded in §1 (or
  the request's `Accept-Language`/profile override if already set) — the voice line is
  localized, not hardcoded to German or English.

**Frontend — `WelcomeStep` gains a mount-time effect:**
1. Calls the new endpoint immediately on mount (no user action needed to START the check —
   only needed to hear it, per the autoplay constraint below).
2. Attempts to `speak()` the greeting via the EXISTING `ttsBlob`/`audioRef` mechanism from
   `chat.tsx` (factor it into a small shared hook both pages import, rather than duplicating
   it a third time).
3. **Autoplay is expected to be blocked on a genuinely first-ever page load** (no prior user
   gesture exists yet) — this is not a problem to solve, it's a browser policy to respect.
   Reuse the EXISTING "blocked → tap to hear" banner pattern verbatim; the check itself still
   ran and its result is already visible in the UI text regardless of whether the audio plays.
4. The spoken/written content **reflects the actual check result**, not a canned line
   regardless of outcome — e.g. if TTS itself is what's degraded, the WRITTEN welcome text
   still explains what happened (obviously the voice can't announce its own failure), while a
   fully-healthy pipeline gets the full spoken introduction.

### A suggested greeting (localized to German here since that's the operator's own default;
### the real text lives in `i18n`-resolved strings, not hardcoded)

> „Hallo, ich bin Corvin. Deine Installation ist fertig, und ich habe mich gerade selbst
> durchgecheckt: Sprachein- und Sprachausgabe funktionieren, und die Verbindung zu meinen
> KI-Modellen steht. Ab jetzt kannst du mit mir sprechen oder schreiben — hier im Web-Chat,
> aber genauso über Discord, WhatsApp oder Telegram, wenn du das einrichtest. Sag mir einfach,
> was du brauchst."

Short, states what just happened (the check), states the one thing users most need to know
(voice-and-text control from multiple surfaces), and stops — no marketing copy, no feature
tour. A degraded variant (e.g. TTS unavailable, STT fine) would drop the "Sprachausgabe
funktioniert" clause and say plainly what to fix instead of glossing over it.

### Dialectical pass: should the check block the wizard until everything is healthy?

**Thesis:** gate "Let's go" until every component reports healthy, so a broken install never
reaches the user unannounced.

**Antithesis:** CorvinOS is explicitly designed to degrade gracefully today (Hermes falls back
when Claude is absent, edge-tts falls back to Piper, etc. — ADR-0185's whole point). A hard
gate here would make a perfectly-functional-but-partially-degraded install (e.g. no Ollama, so
no local fallback, but Claude Code works fine) look broken and block onboarding over a
component the user may not even need.

**Synthesis:** never block — surface status, don't gate on it. The wizard's "Let's go" stays
enabled regardless of check outcome (matches the Boot-Healer's own existing philosophy: warn
and repair where possible, never block boot). The check's VALUE is that problems become
visible and audible in the first 10 seconds instead of surfacing confusingly on the user's
first real request — not that it acts as a pass/fail gate.

### Non-goals
- Not a replacement for the periodic Boot-Healer cycle — this is one synchronous,
  user-facing pass at a specific moment, the Boot-Healer keeps running independently.
- Not a new TTS/STT provider or audio pipeline — reuses `synthesize_voice_note` and the
  existing browser playback mechanism unchanged.
- No autoplay workaround/hack — respects the browser's policy, reuses the existing
  tap-to-play fallback that already exists for this exact scenario elsewhere in the console.
- Does not attempt to "warm up" the Claude Code engine the way Hermes is warmed (no
  persistent local model state to preload) — the cheap test turn there is a **connectivity/
  auth check**, described as such, not oversold as a performance warm-up.

### Implementation notes
Shipped as `POST /setup/welcome-check` (start, idempotent while running) +
`GET /setup/welcome-check/status` (poll) in `core/console/corvin_console/routes/setup.py` —
the same async-job/poll shape as the existing `/setup/whatsapp/start` endpoint in the same
file, chosen because a Hermes warm-up or a cold STT model load can take tens of seconds, too
slow for a synchronous request. The background job runs `house_rules_boot_health_check`
(captured via its `log_fn` callback, since it's a logging-only API — any message it would have
logged becomes a `"degraded"` component), `ensure_hermes_ready()` only when Hermes is the
resolved default engine, `voice_doctor._check_stt`/`_check_tts` directly (a 45s STT budget,
not the CLI's patient 180s default — this runs unattended, not with a human watching a
terminal), and the existing `test_engine` route handler reused directly as a plain Python call
(FastAPI route decorators don't wrap the function, so it's callable as-is). The greeting is
assembled from new `welcome.*` keys added to `operator/voice/i18n/{de,en}.json` — short,
independent per-component clauses (`check_stt_ok`/`check_stt_bad`, etc.) joined together,
rather than one grammatically-joined "A, B and C" sentence, so no per-language list-join logic
was needed. **Per the user's explicit follow-up ask**, the greeting also always includes a
`welcome.capabilities` clause — what Corvin can do (code, web research, browser automation,
image generation, email/calendar, automation) and how to ask for it in plain language — not
just a health report. A second follow-up ask added `welcome.voice_to_action`, inserted between
`control` and `capabilities`: a short framing clause explaining the underlying idea — "you
speak, Corvin acts," with real computer/browser/internet access, so the whole digital life can
be steered through voice alone — before the concrete capability examples land.

On the frontend, the `ttsBlob`/`audioRef`/autoplay-block mechanism was extracted from
`chat.tsx` into a shared `useVoicePlayback` hook (`web-next/src/lib/useVoicePlayback.ts`), and
`chat.tsx` itself was refactored to use it (no behavior change — verified via `tsc -b` and a
full `npm run build`) — closing the "factor it into a shared hook" gap this design flagged
instead of leaving chat.tsx's copy as a second, diverging implementation. `WelcomeStep` in
`SetupGate.tsx` calls the check on mount (guarded against React StrictMode's double-invoke),
shows the loading/greeting/fallback text depending on check state, and attempts playback via
the hook; on autoplay block it shows a "Tap to hear Corvin" pill, matching the existing
tap-to-play pattern. "Let's go" is never disabled by check state. Tests:
`core/console/tests/test_setup_welcome_check.py`.

## 3. Delivery

1. Concept 1 (language propagation) — `corvinOS/installer/steps/piper.py`, `pyproject.toml`.
2. Concept 2 backend (`/setup/welcome-check` + status poll, localized greeting with
   capabilities clause) — `core/console/corvin_console/routes/setup.py`,
   `operator/voice/i18n/{de,en}.json`.
3. Concept 2 frontend (`WelcomeStep` mount effect, shared `useVoicePlayback` hook used by both
   `SetupGate.tsx` and `chat.tsx`) — `web-next/src/lib/useVoicePlayback.ts`,
   `web-next/src/components/setup/SetupGate.tsx`, `web-next/src/pages/chat.tsx`,
   `web-next/src/lib/api.ts`.
