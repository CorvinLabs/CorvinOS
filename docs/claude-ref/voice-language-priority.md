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
