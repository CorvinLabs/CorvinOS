# Language Priority — one decision per turn (ADR-0650)

**Module:** `core/language/` · **Console wiring:** `core/console/corvin_console/language_resolution.py`
· **Call site:** `core/console/corvin_console/chat_runtime.py::stream_turn`

## The rule

A turn's language is resolved ONCE, at the top of the turn, in this order:

| # | Source | `LanguageSource` | Confidence | When it wins |
|---|---|---|---|---|
| 1 | `display_language` from the user's profile | `profile` | 1.00 | Whenever it is set — **canonical** |
| 2 | Detected from the user's own text | `input` | 0.95 | No profile set |
| 3 | Detected from the model's reply | `response` | 0.85 | No profile, user text too ambiguous |
| 4 | `system_default` (`"en"`) | `system` | 0.50 | Nothing else decided |

An empty or whitespace-only profile value is treated as **unset** and falls through to
detection (`LanguageResolver.__init__`), so a blank Settings field never pins the user to
English. `"zh"` normalises to `"zh-Hans"`; codes are lower-cased.

The profile is canonical *by design*: a user who configured Deutsch keeps Deutsch even on an
English prompt. `resolve(..., force_input_detection=True)` is the only bypass and has no
production caller — it exists for tests.

### Installer seed vs operator pin (2026-10-21)

`display_language` has two writers with two meanings, and the profile records which one
wrote it (`profile.py`: `_meta.display_language_source`):

| Writer | Provenance | `display_language_pin()` | `display_language_default()` | Reply-language rule |
|---|---|---|---|---|
| installer (`corvinOS/installer/steps/piper.py::_seed_profile_display_language` → `profile.seed_display_language`) | `installer` | `""` | the seed | auto-detect per message; the seed only decides language-less input |
| operator (`/lang set`, `PUT /v1/console/profile`, `profile.set_value`) | `operator` | the pin | the pin | `ALWAYS reply in <pin>` — overrides the message language |
| a profile written before the marker existed | `operator` | the value | the value | as a pin (unchanged behaviour) |

Row 1 of the table above therefore means the **operator's pin**: the console
(`chat_runtime._language_rule`, `_language_closing_block`, `language_resolution.load_profile_language`),
the bridge adapter (`_pinned_display_language`, `_resolve_voice_output_language`) and the voice
routes (`_resolve_voice_output_language`) all read `display_language_pin()`; the seed enters only
as `system_default` (`language_resolution.load_default_language`) and as the TTS fallback. The
seed never overwrites a value and an operator write always un-seeds (`set_value`,
`mark_display_language_operator`). Measured before the split, on a fresh `en_US` container: a
German question got *"Die Operatorvorgabe lautet, dass ich immer auf Englisch antworte"* —
`language_context.source` was `profile`, `profile_set: true`, for a value nobody had chosen.
Guard: `corvin_operator/bridges/shared/test_profile_language_pin.py`,
`tests/e2e/fresh_system/test_fresh_chat_live.py::test_the_bot_answers_in_the_users_language`.

### Every reader of the provenance (round 2, 2026-10-21)

The split above is only complete when every surface that *speaks* or *greets* reads the same
provenance. Three readers still took the value for a pin and were fixed in round 2:

| Reader | Before | Now |
|---|---|---|
| console chat page (`web-next/src/pages/chat.tsx`, `lib/tts-language.ts`) | any `identity.display_language` pinned the SPOKEN language — a German answer on a seeded `en` install was spoken with the English voice, and the on-demand session recap was made in English | `GET /v1/console/profile` → `profile.display_language_source` (`operator` / `installer` / `""`); only `operator` pins, the seed is the fallback for a language-less reply. A backend without the field keeps pin behaviour |
| Settings → Voice preview (`pages/voice.tsx`) | the client-side mirror of `for_system_prompt()` printed *"(default; still match the user's actual writing language)"* for every value — the opposite of a pin | mirrors the real rule (pin → `ALWAYS answer in …`, seed → auto-detect + install default) and shows *"Detected from the system locale at install"* under the selector while the seed is untouched |
| `/lang` on a bridge (`lang_cli.py show`) | `set: true` for the seed → *"Language set to English"* | `set` is the operator's pin only; a seed answers *"No language preference set. Defaulting to English (en)."* with `source: installer` |
| bot-disclosure card (`in_chat_commands.js::disclosureCardText`, Discord/WhatsApp daemons) | `settings.lang \|\| 'en'` — a settings key nothing writes, so every card was English | a daemon without a language of its own leaves `lang` unset and the card takes the profile's language (seed or pin — both are the right default for a greeting), else English |
| `POST /v1/console/voice/summarize` | `lang` defaulted to `de`: an English text summarised in German | omitted → the text's own language through `_resolve_voice_output_language` (pin → detection → seed → locale), `de`/`en` explicit wins |

Verified on a fresh container (round 2): seed `en` → *"42?"* answered in English (`source: system`),
German in → German out, English in → English out; `PUT /profile {display_language: de}` →
*"Snow is white."* asked in English answered *in German*; clearing it restores auto-detect; the installer
seed run again (update / reinstall) re-seeds an empty value and never demotes a pin; a hand-written
profile without `_meta`, or with a non-dict `_meta`, is a pin (old-install behaviour, documented above).
Guards: `core/console/tests/test_profile_routes.py::ProfileLanguageProvenanceTests`,
`web-next/tests/unit/lib/tts-language.test.ts`, `corvin_operator/voice/scripts/test_lang_cli.py`,
`corvin_operator/bridges/shared/js/test_disclosure_dispatcher.js` (card language from the profile),
`core/console/tests/test_voice_summary_language.py::SummarizeRouteLanguageTests`.

## Detection (`detect_language`)

1. **Script-level** (`_detect_script_language`) — CJK, Hangul, Arabic, Hebrew, Cyrillic, Greek,
   Thai, Devanagari. One character is definitive.
2. **German diacritics** — a single `ä/ö/ü/ß` decides `de`, **provided German function words
   are not outnumbered by English ones**. Evaluated *before* the minimum-length gate, because
   `"äöüß"` is decisive in four characters. The guard is what keeps *"The Zürich office is
   open"* English.
3. **Function-word scoring** — needs ≥ 10 characters and ≥ `_MIN_WORD_HITS` (2) hits for the
   winning language, which must strictly outscore the other. Two is the floor because two is
   the smallest count that can express dominance (*"Das ist …"* / *"This is …"*); a floor of
   three sent every short German sentence to the English fallback.
4. **fr / es / it** single-word probes, only once German-vs-English was inconclusive.
5. Otherwise the caller's `fallback`.

## Where the decision goes

`stream_turn` resolves the language immediately after the empty-prompt guard, before any
engine work, and then:

* stashes it on `WebChatSession.language_context` — transient per-turn state, deliberately
  **not** persisted in the session meta (`_session_from_meta` rebuilds every field
  explicitly), so a stale decision can never leak into the next turn;
* emits it on the chat WebSocket as `{"type": "language", "resolved_lang", "source",
  "confidence", "profile_set"}`, the first event of the turn — the client and the Voice
  Summary read the same value the backend chose;
* `_append_turn` attaches it to **every** persisted turn record as `language_context`,
  including the early-exit paths (gate refusals, engine errors, quota notices) — a refusal
  spoken in the wrong language is exactly what the resolver exists to prevent.

Resolution is best-effort: a resolver failure is logged at debug and the turn continues
without a `language` event. Language is advisory, never turn-fatal.

## What is NOT wired

`core/language/output_translator.py` (`OutputTranslator`, `LanguageOutputBoundary`) has no
production call site, and `LanguageOutputBoundary.__init__` hard-codes
`llm_translate_fn=None`. There is no translation happening today — only the input-side
decision above. Treat the "skills always emit English, the boundary translates" invariant as
a design intent, not a live guarantee.

## Tests

| File | Boundary |
|---|---|
| `core/language/tests/test_language_resolver.py` | unit — detection + priority |
| `core/console/tests/test_language_resolution.py` | unit — console wrapper |
| `core/console/tests/test_language_resolution_ws_e2e.py` | **real chat WebSocket route** → real `stream_turn` → real resolver |
| `core/language/tests/test_language_live_e2e.py` | real `claude -p` turn (`@pytest.mark.live`, `CLAUDE_LIVE_E2E=1`) |

## Must NOT do

* Let detection override a set profile (that is the bug ADR-0650 exists to fix).
* Treat an empty/whitespace profile value as a real setting.
* Persist `language_context` into the session meta file — it is per-turn.
* Make a resolver failure fatal to the turn.
