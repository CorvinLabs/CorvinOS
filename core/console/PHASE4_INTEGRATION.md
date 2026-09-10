# Phase 4 — Language Resolution in `chat_runtime.py` (ADR-0650)

> **2026-09-07 — this file used to be a *proposal*.** It described the edits someone
> would have to make to `stream_turn()` and pointed at `core/console/language_resolution.py`,
> a path that does not exist (the package is `corvin_console`). Nothing was actually wired,
> and `core/language` could not even be imported. Both are fixed; this file now documents
> what the code **does**.

## Status: WIRED

| Piece | Where |
|---|---|
| Resolver | `core/language/language_resolver.py` |
| Console wrapper | `core/console/corvin_console/language_resolution.py` |
| Call site | `core/console/corvin_console/chat_runtime.py::stream_turn` |
| Persistence | `chat_runtime.py::_append_turn` → `turns.jsonl` `language_context` |
| Wire event | chat WebSocket, `{"type": "language", ...}` |

## What happens on a turn

`stream_turn()` resolves the language once, immediately after the empty-prompt guard and
before any engine work:

```python
sess.language_context = None
try:
    _lang_ctx = resolve_turn_language(
        user_text=prompt,
        response_text="",                       # not produced yet
        profile_lang=load_profile_language(sess.tenant_id),
    )
    _lang_meta: dict[str, Any] = {}
    store_language_context_in_metadata(_lang_meta, _lang_ctx)
    sess.language_context = _lang_meta["language_context"]
    yield {"type": "language", **_lang_meta["language_context"]}
except Exception:
    _log.debug("language resolution failed for this turn", exc_info=True)
```

* `WebChatSession.language_context` is **transient per-turn state**. It is not written to the
  session meta file, so it cannot go stale across turns.
* `_append_turn()` attaches it to every persisted turn — including early-exit paths (gate
  refusals, engine errors, quota notices), which is why the attach lives there instead of
  being threaded through ~20 call sites.
* The `language` event is forwarded verbatim by `routes/chat.py`. The frontend's
  `applyEvent()` switch has a `default: return`, so it is ignored until a consumer is built.
* Failure is non-fatal. Language is advisory; it never costs the user their turn.

## Priority

Profile (canonical) → detected input → detected response → system default. See
[`docs/claude-ref/voice-language-priority.md`](../../docs/claude-ref/voice-language-priority.md)
for the full table, the detection heuristics, and the must-nots.

## Tests

```bash
.venv/bin/python -m pytest -q -o addopts="" \
  core/language/tests/ \
  core/console/tests/test_language_resolution.py \
  core/console/tests/test_language_resolution_ws_e2e.py
```

`test_language_resolution_ws_e2e.py` is the wiring proof: it drives the **real** chat
WebSocket with a German prompt and asserts the decision arrives on the wire. Nothing between
the socket and the resolver is stubbed.

Real-LLM check (Rule 6):

```bash
CLAUDE_LIVE_E2E=1 .venv/bin/python -m pytest -q -o addopts="" \
  core/language/tests/test_language_live_e2e.py
```

## Still open (Phase 5+)

| Phase | What | Where |
|---|---|---|
| 5 | Summary Generator consumes `language_context` | `core/notification/summary_generator.py` |
| 6 | `OutputTranslator` wiring — **today it has no production caller and no LLM function**; `LanguageOutputBoundary.__init__` hard-codes `llm_translate_fn=None` | `chat_runtime.py` |
| — | Frontend consumer for the `language` stream event | `web-next/src/lib/chat-registry.ts` |
