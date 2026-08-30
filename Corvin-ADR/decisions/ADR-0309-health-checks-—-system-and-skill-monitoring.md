---
id: ADR-0309
status: accepted
depends_on: ['ADR-0307']
related: ['ADR-0307']
commits:
  - "feat(skills): Add SkillExecutor + ExecutorHealth monitoring (ADR-0307, ADR-0309)"
paths:
  - "core/skills/health.py"
  - "core/skills/executor.py"
  - "tests/unit/test_health_checks.py"
  - "tests/unit/test_skill_executor.py"
docs:
  - "VIBE_PHASE1_IMPLEMENTATION_SUMMARY.md"
---

# ADR-0309 — Health Checks — System & Skill Monitoring

**Status:** Proposed
**Date:** 2026-08-12
**Deciders:** Claude (Implementation), shumway (Audit)

## Context

**Context:** Is the skill system healthy? Are grading workers running? Is the event queue growing unbounded? ADR-0309 defines health checks that feed into the dashboard.

## Decision

**Decision:** Implement HealthCheckFramework:
1. HealthStatus enum: healthy, degraded, unhealthy
2. Per-component checks: grading, telemetry, event_queue, learning_store
3. Metrics: queue depth, worker count, last_event_timestamp
4. Thresholds: warn at 70%, critical at 90%
5. Periodic checks (every 30s)
6. Emit health events to dashboard

## Implementation

**File:** core/skills/health.py

- HealthCheckFramework class
- check_component(component_name) → HealthStatus
- check_all() → dict[str, HealthStatus]
- get_health_metrics() → dict

## Compliance

**GDPR Art. 32:** System resilience (health checks prevent silent failures). **Audit:** Health status changes logged.

## Tests

**Tests:** 12 tests covering:
- Component health checks
- Threshold enforcement
- Metric accuracy
- Periodic execution
- Health event emission

**Test Files:** tests/unit/test_health_checks.py

**Coverage:** 89%+

## Effort Estimation

| Task | Hours |
|---|---|
| HealthCheckFramework class | 1h |
| Component checks (4 types) | 1.5h |
| Threshold logic | 0.5h |
| Metric collection | 0.5h |
| Unit tests (12) | 1h |
| Docs | 0.5h |
| **Total** | **5h** |

---

**Prepared by:** Claude
**Date:** 2026-08-12
