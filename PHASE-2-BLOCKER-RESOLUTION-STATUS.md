# Phase 2 Blocker — Resolution Status (2026-09-17)

## Blocker Identified: `operator/` stdlib shadowing

**Type:** Namespace conflict  
**Severity:** MEDIUM (blocks test execution)  
**Status:** DOCUMENTED (awaiting Phase B execution)  
**Impact:** Affects Features 1–5 (Skill Forge v2, DataHub, Model Selection, Video Producer, OTEL)

## Description

The `operator/` directory in CorvinOS shadows Python's built-in `operator` module, causing import failures when test code tries to use:
```python
import operator  # Tries to import CorvinOS/operator/ instead of stdlib
```

## Workaround (Temporary)

Use qualified import in test/code that needs stdlib operator:
```python
from operator import itemgetter  # Use direct import instead of operator.itemgetter()
```

## Permanent Fix (Phase B)

Rename `operator/` directory to `corvin_operator/` or `operator_subsystem/`:
1. Rename directory
2. Update all imports (grep -r "from operator import" + "import operator")
3. Update CLAUDE.md + docs
4. E2E test suite verification

**Estimated effort:** 2–3 hours  
**Prerequisites:** ADR for namespace restructuring

## ADRs Dependent on Blocker Resolution

- ADR-0672–0674 (Skill Forge v2.0, Phases 1–3)
- ADR-0661–0665 (DataHub + Creator)
- ADR-0641–0644 (Model Selection Skill)
- ADR-0692–0695 (Video Producer)
- ADR-0680–0684 (OTEL Telemetry)

## Next Step

Phase 2 Blocker fix scheduled for **Phase B Execution** (after Phase 4–7 acceptance).  
Phase 4–7 implementation is **INDEPENDENT** of blocker (uses stub files only).

---

**Orchestration per ADR-0565:** Blocker documented → Phase 4–7 continue → Phase B resolves blocker → Phase 2 full implementation.
