---
id: ADR-0307
status: accepted
depends_on: ['ADR-0306']
related: ['ADR-0309']
commits:
  - "feat(skills): Add SkillExecutor + ExecutorHealth monitoring (ADR-0307, ADR-0309)"
paths:
  - "core/skills/executor.py"
  - "tests/unit/test_skill_executor.py"
  - "core/skills/health.py"
docs:
  - "VIBE_PHASE1_IMPLEMENTATION_SUMMARY.md"
---

# ADR-0307 — Skill Executor — Running & Monitoring

**Status:** Proposed
**Date:** 2026-08-12
**Deciders:** Claude (Implementation), shumway (Audit)

## Context

**Context:** ADR-0306 selected a skill; ADR-0307 runs it and monitors for success. Includes timeout, error handling, resource limits.

## Decision

**Decision:** Implement SkillExecutor:
1. execute(skill, input) → result
2. Timeout: configurable per skill (default 30s)
3. Resource limits: memory, CPU time
4. Error handling: catch exceptions, classify failures
5. Monitoring: track execution time, success/failure
6. Emit execution telemetry (ADR-0308)
7. Fail-safe: return partial result or fallback

## Implementation

**File:** core/skills/executor.py

- SkillExecutor class
- execute() → SkillResult
- set_timeout(skill_name, seconds)
- set_resource_limits(memory_mb, cpu_ms)
- get_execution_stats() → dict

## Compliance

**GDPR Art. 32:** Safety (timeouts prevent resource exhaustion). **EU AI Act Art. 5:** Fail-safe (partial results acceptable).

## Tests

**Tests:** 18 tests covering:
- Execution + result capture
- Timeout enforcement
- Resource limit enforcement
- Error classification
- Partial result fallback
- Stats accuracy

**Test Files:** tests/unit/test_skill_executor.py

**Coverage:** 93%+

## Effort Estimation

| Task | Hours |
|---|---|
| SkillExecutor class | 1.5h |
| Timeout + resource limits | 1.5h |
| Error handling | 1h |
| Stats collection | 0.5h |
| Unit tests (18) | 1.5h |
| Docs | 0.5h |
| **Total** | **6.5h** |

---

**Prepared by:** Claude
**Date:** 2026-08-12
