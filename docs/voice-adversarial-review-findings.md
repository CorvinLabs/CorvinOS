# Voice Summary System — Adversarial Review Findings

**Date:** 2026-07-23  
**Status:** FIXING IN PROGRESS (Iteration 2 of 5)  
**Bugs Found:** 13 + 1 NEW (3 CRITICAL, 5 HIGH, 5 MEDIUM + 1 TIMING)
**Fixed in Iteration 1:** BUG-1.2, BUG-2.3 (NEW)

## Critical Bugs (Being Fixed in Iteration 1)

### BUG-1.2: CRITICAL — detect_lang.py Ignored, Text-Language Detection Dead Code
- **Impact:** English text with German display_language → German summary
- **Root:** No language auto-detection fallback in console voice.py
- **Fix:** Wire _resolve_voice_output_language() to console, import detect_lang + i18n
- **Files:** core/console/corvin_console/routes/voice.py

### BUG-2.2: HIGH — naive_truncate Truncates List Items (Completeness Violation)
- **Impact:** List items >350 chars read with … instead of full
- **Root:** _first_clause() truncates each item to 350 chars
- **Fix:** Verify COMPLETENESS rule: EVERY list item must be full length
- **Files:** operator/voice/scripts/summarize.py

### BUG-2.3: CRITICAL — Voice Result Event Timing (FIXED Iteration 1)
- **Impact:** Voice summary spoken from intermediate result, not final output
- **Root:** setLastTts() called on EVERY result event, even intermediate ones
- **Fix:** Only update setLastTts() on final result (annotation_pending === false)
- **Files:** core/console/corvin_console/web-next/src/pages/chat.tsx
- **Status:** ✅ FIXED (commit 9bc46f0)

### BUG-4.1: HIGH — voice_audience_learning Silent Drop
- **Impact:** User-setting display_language ignored, no error
- **Root:** _extract_appendix() fails silently if marker not found
- **Fix:** Add error logging + re-try with fallback markers
- **Files:** operator/voice/scripts/summarize.py

## Remaining High-Severity Bugs (Iteration 2-5)

### BUG-2.1: HIGH — strip_for_tts Timeout Context Loss
- Parallel requests can share `text` reference → wrong session in summary

### BUG-3.2: HIGH — summarize_smart Byte-Truncation Fallback
- Falls back to byte-truncate instead of structural naive_truncate

### BUG-5.5: HIGH — session_recap Silent Empty Return
- Timeout with no error message to caller

### BUG-5.2: MEDIUM — No E2BIG Size-Check Before CLI Spawn
- Large texts timeout unexpectedly without warning

### BUG-5.4: MEDIUM — think=False Not End-to-End Tested
- Regression possible if think=True slips in

## Testing Strategy

- **Tier-1:** Lint / type checks (ruff, mypy)
- **Tier-2:** Unit tests (detect_lang integration, naive_truncate)
- **Tier-3:** Integration (mock summarize.py calls)
- **Tier-4:** E2E (real summarize.py with fixture texts)

## Production-Ready Checklist

- [ ] Language-routing: User preference → Auto-detect → System locale
- [ ] Completeness: Every list item preserved full-length
- [ ] Profile Respect: All settings (display_language, audience_learning) honored
- [ ] Error Paths: No silent failures, all errors logged
- [ ] E2E Tests: Real voice output for German/English/Mixed scenarios
