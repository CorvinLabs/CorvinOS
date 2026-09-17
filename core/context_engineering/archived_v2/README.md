# Archived: Context Pipeline v2 (2026-08 Research Prototype)

**Status: DEPRECATED & ARCHIVED**  
**Decision:** ADR-0866 "v2-Context-Pipeline Deprecation" (2026-09-17)  
**Effective:** FIX #4 Implementation

---

## What's Here

This directory contains the research prototype for Context Pipeline v2:
- `v2_context_preservation.py` — DAG-based context isolation (from context_pipeline/)
- `hybrid_context_request_pipeline.py` — Hybrid context request handling (from learning/)
- `hybrid_context.py` — Hybrid context module (from learning/)

## Why Archived

| Aspect | Details |
|---|---|
| **Previous Goal** | Implement cross-tenant isolation via DAG-based context composition |
| **Research Outcome** | Successfully proved DAG isolation patterns work, but added complexity |
| **Winner** | `dual_gate.py` (v1, simpler, proven in production) |
| **Decision** | Replace v2 with v1 for production; archive v2 for reference |

## When to Use

**Only if future requirements demand:**
- Cross-tenant adversarial context handling beyond v1 capabilities
- DAG-based composition for complex context isolation scenarios
- Need to resurrect experimental context isolation patterns

## Resurrection Process

If you need to use v2 again:

1. **Copy from archive:**
   ```bash
   cp core/context_engineering/archived_v2/v2_context_preservation.py \
      core/context_pipeline/
   cp core/context_engineering/archived_v2/hybrid_context_request_pipeline.py \
      core/learning/
   ```

2. **Re-integrate:**
   - Update all imports (paths have changed)
   - Re-add tests from git history
   - Run full test suite (`pytest tests/`)
   - Update CLAUDE.md and this README

3. **Decision Gate:**
   - Requires new ADR documenting why v2 is superior to v1 for your use case
   - Blockers on cross-tenant isolation must be understood first

## Historical Value

These modules document:
- DAG-based context composition patterns (if needed in future)
- Adversarial handling approaches (reference implementation)
- Testing patterns for context isolation (reusable test fixtures)

## References

- **Active v1:** `core/context_engineering/dual_gate.py` (use this for production)
- **Tests:** `core/context_engineering/tests/` (includes v2 test fixtures)
- **Decision:** See Corvin-ADR repo for ADR-0866

## Do NOT

- ❌ Keep old v2 references in code (no "see also: v2" comments)
- ❌ Resurrect v2 without a new ADR + proof that v1 is insufficient
- ❌ Maintain two versions in parallel (defeats the purpose of archival)

---

**Archived:** 2026-09-17  
**Previous Location:** 
- `core/context_pipeline/v2_context_preservation.py`
- `core/learning/hybrid_context_request_pipeline.py`
