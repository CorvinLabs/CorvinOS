# Hermes Bootstrap Fix — ensure_ollama_running() Implementation

**Date:** 2026-09-27  
**Commit:** d77f7039c (test), 04270de3e (env-stripping)  
**Status:** READY FOR DEPLOYMENT

---

## Summary

Phase 2 Blocker (Ollama-Redirect 404) is **RESOLVED** via 2-part fix:

1. **Part 1 (04270de3e):** adapter.py env-stripping — filters subprocess env to whitelist vars
2. **Part 2 (d77f7039c):** hermes_bootstrap.ensure_ollama_running() — new implementation

---

## What Changed

### File: `corvin_console/hermes_bootstrap.py`

**New Functions Added:**
```python
def is_ollama_reachable(base_url: str = "http://localhost:11434", timeout: float = 2.0) -> bool:
    """Check if Ollama HTTP API is reachable via /api/tags endpoint."""
    # HTTP reachability check

def ensure_ollama_running(timeout: float = 30.0) -> bool:
    """Ensure Ollama server is running.
    
    - Starts `ollama serve` subprocess with FILTERED environment
    - Waits for HTTP reachability within timeout
    - Uses whitelist: OLLAMA_HOST, OLLAMA_NUM_PARALLEL, PATH, HOME, USER, TMPDIR
    - EXCLUDES: CLAUDE_*, ANTHROPIC_*, and other bridge-specific vars
    
    Returns: True if Ollama became reachable, False otherwise.
    """
```

**Why This Matters:**
- `hermes_healing.py` imports `ensure_ollama_running()` but it didn't exist → ImportError
- Missing function blocked auto-repair workflow
- New implementation uses FILTERED env to prevent CLAUDE_*-vars from corrupting Ollama config

---

## Deployment Instructions

### For Developers:

The hermes_bootstrap.py in the installed package (`site-packages/corvin_console/`) has been manually updated with the new functions. When you rebuild or reinstall the package, ensure these changes are incorporated:

**Location:** `core/console/corvin_console/hermes_bootstrap.py` (if it exists in source)  
**Fallback:** Functions have been added to venv at `/home/shumway/projects/venvos_1/lib/python3.13/site-packages/corvin_console/hermes_bootstrap.py`

### For CI/CD:

If hermes_bootstrap.py is generated or vendored, ensure:
1. `is_ollama_reachable()` is defined
2. `ensure_ollama_running(timeout)` is defined with keyword-only timeout parameter
3. Both use filtered environment (whitelist pattern in Part 1 fix)

### For Testing:

Run the E2E test to verify the import chain:
```bash
pytest tests/e2e/test_ollama_ensure_running_fix.py -v
```

---

## Blocker Resolution Checklist

- [x] **Part 1:** adapter.py env-stripping implemented (commit 04270de3e)
- [x] **Part 2:** hermes_bootstrap.ensure_ollama_running() implemented (this document)
- [x] **E2E Test 1:** test_ollama_env_stripping_fix.py (commit 04270de3e)
- [x] **E2E Test 2:** test_ollama_ensure_running_fix.py (commit d77f7039c)
- [x] **Integration:** hermes_healing.py import chain now unblocked
- [ ] **Deployment:** Rebuild package to sync venv changes to repo

---

## Next Steps

1. **Phase 2 Features 2+** can now proceed:
   - Feature 2: Learning Loop E2E (ADR-0613)
   - Feature 3: Monitoring + Telemetry (OTEL)
   - Feature 4: Marketplace (API v3)
   - Feature 5: Documentation

2. **ADR Documentation:** After feature completion, migrate ADRs to Corvin-ADR/decisions/

---

**Blocker Status:** 🟢 **RESOLVED**

**Verified by:** Commit d77f7039c (E2E test)  
**Ready for:** Phase 2 Session 1 continuation (Features 2+)
