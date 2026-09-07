# Phase 4 Integration Guide — Language Resolution in chat_runtime.py

## Überblick

Phase 4 integriert `LanguageResolver` in `stream_turn()` (chat_runtime.py), damit jeder Turn die Nutzer-Sprache kennt.

**Resultat:**
- ✅ Voice Summary spricht die richtige Sprache
- ✅ Outputs werden übersetzt (Phase 5)
- ✅ Audit-Events loggen Language-Entscheidung

---

## Integration Points

### 1. Import in chat_runtime.py

```python
from .language_resolution import (
    resolve_turn_language,
    load_profile_language,
    store_language_context_in_metadata,
)
```

### 2. In stream_turn() — nach Task-Erstellung

**Aktuell (Zeile 4696–4702):**
```python
task_id = tm.create_task(
    chat_key=sess.chat_key,
    instruction=prompt,
    persona="assistant",
    turn_number=sess.turn_count,
    tenant_id=sess.tenant_id,
)
```

**Neu: Language auflösen (direkt danach)**
```python
# Phase 4: Resolve language for this turn (ADR-0650)
profile_lang = load_profile_language(sess.tenant_id)
lang_ctx = resolve_turn_language(
    user_text=prompt,
    response_text="",  # wird später gefüllt, nach Claude-Response
    profile_lang=profile_lang,
    system_default="en"
)
# → lang_ctx.resolved_lang = "de" | "en" | etc.
# → lang_ctx.source = "profile" | "input" | "response" | "system"
```

### 3. Nach stream_turn() — Language-Kontext speichern

**Nach der Response vom LLM (wenn turn gespeichert wird):**
```python
# Speichere resolved language in turn metadata (für Summary/Translation/Audit)
turn_metadata = {
    "prompt": prompt,
    "response": claude_output,
    # ... andere Felder ...
}
store_language_context_in_metadata(turn_metadata, lang_ctx)

# turn_metadata["language_context"] = {
#     "resolved_lang": "de",
#     "source": "profile",
#     "confidence": 1.0,
#     "profile_set": True
# }
```

### 4. Audit-Integration (Optional, Phase 4.5)

```python
# Log the language decision to audit trail
if _bridge_audit and _console_audit:
    _console_audit.write_event({
        "event_type": "language_resolved",
        "resolved_lang": lang_ctx.resolved_lang,
        "source": lang_ctx.source.value,
        "confidence": lang_ctx.confidence,
        "task_id": task_id,
        "tenant_id": sess.tenant_id,
    })
```

---

## Was die Nutzer dann sieht

1. **Input:** User schreibt `"Überprüf den Code"` (Deutsch)
2. **Resolution:** LanguageResolver: Profile="de" → CANONICAL
3. **Processing:** ACP Skill antwortet auf Deutsch/English (egal, intern)
4. **Metadata:** Turn speichert `language_context: { resolved_lang: "de", source: "profile", ... }`
5. **Output (Phase 5):** Summary Generator / Translator nutzt `language_context` → spricht Deutsch

---

## Tests

Siehe `core/console/tests/test_language_resolution.py`:
- 6 test cases
- Covers: Profile priority, Input detection, Response fallback, System default
- Metadata storage structure

Syntax-Check:
```bash
python3 -m py_compile core/console/language_resolution.py
python3 -m py_compile core/console/tests/test_language_resolution.py
```

---

## Nächste Schritte (Phase 5+)

| Phase | Was | Wo |
|-------|-----|-----|
| 5 | Summary Generator nutzt `language_context` | core/notification/summary_generator.py |
| 6 | OutputTranslator wiring | chat_runtime.py + language_output_boundary |
| 7 | Audit-Events | core/console/corvin_console/audit.py |

---

## Load-Bearing Invariants

✅ **Language ist immutable pro Turn** — wird 1x resolved, dann gefrostelt  
✅ **Profile ist CANONICAL** — overwritet Input/Response  
✅ **Metadata stored** — für downstream use (Summary, Translation, Audit)  
✅ **Best-effort** — wenn load_profile_language() fehlschlägt, fallback auf Detection  

---

## Debugging

```python
# Bei Problemen: Debug-Log anschauen
tail -f ~/.corvin/tenants/_default/sessions/web:*/chat_debug.jsonl | grep language_resolved

# Oder: Unit-Tests laufen
pytest core/console/tests/test_language_resolution.py -v
```
