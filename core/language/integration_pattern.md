# Language Integration Pattern — Skills (EN) → Outputs (Nutzer Language)

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│ User Input (any language: DE, EN, etc.)                         │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
        ┌─────────────────────────────────────────┐
        │ LanguageResolver.resolve()              │
        │ Priority: Profile > Input > Response    │
        │ Output: LanguageContext (target_lang)   │
        └────────────────┬────────────────────────┘
                         │
                         ▼
        ┌─────────────────────────────────────────┐
        │ ACP Skills / Core Logic                 │
        │ ✅ ALWAYS ENGLISH (internal)            │
        │ ✅ LLM models optimized for English     │
        │ ✅ No language switching                │
        │ Output: StructuredSummary (English)     │
        └────────────────┬────────────────────────┘
                         │
                         ▼
        ┌──────────────────────────────────────────┐
        │ LanguageOutputBoundary                   │
        │ (Skills EN → Nutzer Language Boundary)   │
        │                                          │
        │ - Check: target_lang == "en"?            │
        │   YES → pass through (no-op)             │
        │   NO → translate_output()                │
        └────────────────┬─────────────────────────┘
                         │
                         ▼
        ┌──────────────────────────────────────────┐
        │ OutputTranslator                         │
        │ (if target_lang != "en")                 │
        │                                          │
        │ - LLM-based translation (if available)   │
        │ - Cache lookup (future)                  │
        │ - Fail-closed (fallback to English)      │
        │ Output: Translated text                  │
        └────────────────┬─────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│ Output to Nutzer (in their language: DE, EN, etc.)              │
│ - Voice Summary (TTS speaks correct language)                   │
│ - Text Output (translated if needed)                            │
│ - Audit event (language decision logged)                        │
└─────────────────────────────────────────────────────────────────┘
```

## Integration Points

### 1. chat_runtime.py (Turn Processing)

```python
from core.language import (
    LanguageResolver,
    LanguageOutputBoundary,
)

async def stream_turn(...):
    # Load profile language
    profile = load_voice_profile()
    
    # Phase 1: Resolve language (input + profile)
    resolver = LanguageResolver(
        profile_lang=profile.get("display_language")
    )
    lang_ctx = resolver.resolve(user_text, response_text="")
    
    # Phase 2: Create output boundary (Skills → Nutzer)
    boundary = LanguageOutputBoundary(lang_ctx)
    
    # Phase 3: Wire translator (LLM-based, optional)
    async def translate_fn(req):
        # Use claude -p to translate
        result = await claude_translate(req.source_text, req.target_lang)
        return result.text
    boundary.set_translator(translate_fn)
    
    # Phase 4: Skills run (always English)
    skill_output = skill_execute(...)  # → English
    
    # Phase 5: Translate to nutzer's language
    final_output = boundary.process_skill_output(skill_output)
    
    # Final: nutzer sees their language
    return final_output
```

### 2. Summary Generator Integration

```python
from core.notification.summary_generator import SummaryGenerator
from core.language import LanguageOutputBoundary

async def send_summary(completion_event, language_context):
    # Generate summary (always English)
    generator = SummaryGenerator()
    summary = await generator.generate(completion_event)
    # summary.voice_lines = ["Task completed successfully", ...]
    
    # Translate voice lines if needed
    boundary = LanguageOutputBoundary(language_context)
    translated_lines = [
        boundary.process_skill_output(line, skill_name="summary")
        for line in summary.voice_lines
    ]
    
    # Voice synthesis with correct language
    await tts_synthesize("\n".join(translated_lines), language_context.resolved_lang)
```

### 3. ACP Skills Integration

```python
from core.language import LanguageOutputBoundary

class DelegationRouterSkill:
    """Routes tasks to appropriate agent (always English logic)."""
    
    def execute(self, task_text: str, language_context) -> str:
        # ✅ Skill logic always in English
        classification = self.classify_task(task_text)  # EN
        recommendation = self.generate_recommendation()  # EN
        
        # ⚠️ Do NOT change LLM prompts to match user's language
        # LLM models are optimized for English
        
        # Return English output
        return recommendation  # "Delegate to Opus: complex task"
    
    def deliver_to_user(self, skill_output: str, language_context) -> str:
        """Called by output routing layer."""
        boundary = LanguageOutputBoundary(language_context)
        return boundary.process_skill_output(
            skill_output,
            skill_name="delegation_router"
        )
```

## Load-Bearing Guarantees

| Guarantee | Implementation | Test |
|-----------|---|---|
| **Skills always English** | Code review + linter (no hardcoded non-EN text) | `test_skill_output_english` |
| **Output matches nutzer language** | `LanguageOutputBoundary` | `test_boundary_german_profile` |
| **No translation = no-op** | `TranslationMethod.NONE` when langs match | `test_no_translation_english_to_english` |
| **Failed translation → fallback to EN** | Catch + return source (fail-closed) | `test_llm_translation_failed_fallback_to_source` |
| **Language decision is immutable** | `LanguageContext` frozen, `TranslationResult` frozen | `test_boundary_immutability` |

## Timeline

| Phase | What | When | Status |
|-------|------|------|--------|
| 1 | LanguageResolver (input resolution) | Week 1 | ✅ DONE |
| 2 | Frontend TTS + comment fix | Week 1 | ✅ DONE |
| 3 | OutputTranslator + boundary | Week 2 | ⏳ NOW |
| 4 | chat_runtime integration | Week 2 | ⏳ TODO |
| 5 | Summary Generator integration | Week 2 | ⏳ TODO |
| 6 | ACP Skills wiring | Week 3 | ⏳ TODO |
| 7 | Console Dashboard (optional) | Week 4 | ⏳ TODO |

## Open Questions

1. **LLM Translation Costs?** — Should we cache translations? (future optimization)
2. **Language Pairing Support?** — Which language pairs do we prioritize?
3. **Audit Trail?** — Should translation decisions be logged? (Recommendation: YES)
4. **A/B Testing?** — Should we measure user satisfaction with translations?
