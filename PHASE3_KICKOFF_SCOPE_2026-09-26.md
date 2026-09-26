# Phase 3 Kickoff — Scope & Decision Framework

**Status:** PROPOSED for Phase 3 (Sessions 4+)  
**Prepared:** 2026-09-26 (Session 4)  
**Previous:** Phase 2 COMPLETE (P1-P4 shipped)  
**Token Estimate:** 20-30h across 4-6 sessions

---

## Executive Summary

Phase 3 transforms CorvinOS from a **console + skill executor** into a **self-learning agentic OS** via three coordinated workstreams:

1. **Skill Forge v2:** Versioned, composable skills with canary rollout (ADR-0836)
2. **Learning Loop Closure:** End-to-end feedback → optimization (ADR-0314 + Phase 2 P4)
3. **OTEL Dashboard:** Real-time observability of skills, learning, and plugins (Phase 2 P5 + Part 1)

**Success Metric:** OS can optimize its own decision-making based on user feedback within a single session.

---

## Phase 3 Workstreams (Priority Order)

### Workstream A: Skill Forge v2 (Weeks 1-2, 8h)

**Goal:** Replace ad-hoc skill loading with versioned, composable skill registry.

**Scope:**
- Skill manifest schema (version, dependencies, constraints per ADR-0836)
- Canary rollout: pilot 5% of users on new skill version
- Rollback mechanism: automatic revert on error rate >5%
- Marketplace integration: distribute skills as ZIP packages (ADR-0677)

**Deliverables:**
- `core/skills/skill_manifest.py` (schema + validation)
- `core/skills/skill_canary.py` (rollout + health check)
- E2E: skill versioning + rollback e2e tests
- ADR-0836 update: ACCEPTED status

**Blockers:** None (depends on Phase 2 P1-P4, all done)

**Relates to:** ADR-0243 (plugin boot layers), ADR-0035 (skill registry)

---

### Workstream B: Learning Loop Closure (Weeks 2-3, 10h)

**Goal:** Close the feedback loop: user feedback → skill config optimization → next invocation uses tuned config.

**Scope:**
- EventStore → optimizer: extract feedback signals from ADR-0314 events
- Skill config versioning: rollback safe (each skill pins a config version)
- Dashboard feedback UI: thumbs up/down + text reason (V2)
- Optimizer convergence: measure confidence trend over 10+ feedback points

**Deliverables:**
- `core/learning/optimizer_loop.py` (feedback → config)
- `core/skills/skill_config_versions.py` (version lineage)
- React component: enhanced feedback UI (20 LoC)
- E2E: 10+ tests (feedback capture, optimizer convergence)
- ADR-0314 Phase 2: update with Phase 3 learnings

**Blockers:** None (Phase 2 P4 wired everything up)

**Relates to:** ADR-0613 (learning loop closure), ADR-0314 (event schema)

---

### Workstream C: OTEL Dashboard + Learning Observability (Week 4, 8h)

**Goal:** Operators see skill health, learning metrics, and plugin telemetry in one unified console.

**Scope:**
- Dashboard tab: "Skills Health" (confidence trend, error rate, feedback volume)
- Tab: "Learning Progress" (optimizer convergence, config updates)
- Tab: "Plugin Audit" (boot layer status, lifecycle events)
- Integration: combine Phase 2 P5 (OTEL metrics) + Phase 3 A/B (skill metrics)

**Deliverables:**
- React component: SkillsHealthTab.tsx (120 LoC)
- React component: LearningProgressTab.tsx (100 LoC)
- Wire into VibeDashboard (8 tabs total)
- E2E: 5 tests per tab
- Docs: observability architecture (how to read each metric)

**Blockers:** None (Workstreams A+B complete first)

**Relates to:** ADR-0761 (console charts), ADR-2075 (Phase 2 dashboard patterns)

---

## Architectural Constraints (Load-Bearing)

All Phase 3 work must respect these invariants:

| Constraint | Source | Implication |
|-----------|--------|------------|
| **Multi-tenant isolation** | ADR-0232, ADR-0033 | Every skill config, feedback, optimizer state scoped by tenant |
| **Audit chain immutability** | ADR-0232, ADR-0233 | Every skill version, config change, feedback → audit event |
| **PII fail-closed** | ADR-0297 | No user feedback text in dashboard (redact on display) |
| **Plugin lifecycle** | ADR-0243 | Skills are plugins (boot_layer, disable/enable, version) |
| **Learning convergence** | ADR-0314, ADR-0613 | Optimizer *must* demonstrate convergence on real feedback (not simulated) |
| **E2E proof** | ADR-0759 (worker routing) | Every skill version change E2E tested (real feedback loop) |

---

## Decision Framework

### Skill Versioning (Workstream A)

**Decision:** Semantic versioning (MAJOR.MINOR.PATCH) per skill.

**Rationale:**
- MAJOR: breaking API change (param rename, return type shift) → canary 5%, health check
- MINOR: backward-compat enhancement (new optional param, better heuristics) → canary 25%, auto-rollout
- PATCH: bug fix (off-by-one, missing validation) → canary 100% (direct rollout)

**Alternative considered:** Calendar versioning (date-based). **Rejected:** skill maturity ≠ release date; a skill released 6 months ago might be patched today.

---

### Feedback Signal Design (Workstream B)

**Decision:** Three feedback types: outcome (success/fail), preference (LLM/deterministic/neither), confidence (0-100%).

**Rationale:**
- Outcome: binary, easy to capture, directly impacts skill routing decision
- Preference: reveals operator intent (e.g., "always use LLM for this task type")
- Confidence: self-assessed (after feedback received, what's your trust in this skill?)

**Tuning:** Optimizer weights feedback based on confidence (high confidence = bigger delta in config)

---

### Learning Convergence Validation (Workstream B)

**Decision:** No skill version ships without E2E proof of convergence on ≥10 real feedback points.

**Why:** Simulated feedback is useless (ADR-0613 violated this; learned nothing). Real user feedback only.

**Measurement:** plot confidence trend over feedback points; slope must be positive (learning, not random noise).

---

## Phase 3 Timeline & Dependencies

```
Week 1-2: Skill Forge v2 (A)
  ├─ Manifest schema
  ├─ Canary rollout
  └─ Rollback health check
  
Week 2-3: Learning Loop Closure (B) [depends on A being done]
  ├─ Optimizer loop
  ├─ Config versioning
  └─ Feedback UI v2
  
Week 4: OTEL Dashboard (C) [depends on A+B being done]
  ├─ Skills Health tab
  ├─ Learning Progress tab
  └─ Plugin Audit tab
```

**Critical path:** A → B → C (sequential, ~4 weeks)

**Parallelization:** None (each layer depends on prior), but within each workstream, tasks can run in parallel (e.g., schema + tests simultaneously).

---

## Success Criteria (Phase 3 Gate)

Phase 3 is **DONE** when ALL of the following are true:

1. ✅ **Skill Forge v2 shipped:** versioned skills, canary rollout, rollback working
2. ✅ **Learning loop closed:** operator feedback → skill config update → next invocation uses new config
3. ✅ **Convergence proven:** ≥5 skills show positive confidence trend over 10+ feedback points
4. ✅ **OTEL dashboard live:** operators see real-time skill health + learning metrics
5. ✅ **All tests pass:** 60+ new E2E tests (20 per workstream), coverage >80%
6. ✅ **ADRs updated:** ADR-0836, ADR-0314 (Phase 3 extension), ADR-0761 (dashboard)
7. ✅ **Zero PII leaks:** audit trail + dashboard scrubbed per ADR-0297
8. ✅ **Multi-tenant verified:** each tenant's skills, feedback, metrics isolated

---

## Out of Scope (Phase 4+)

- Marketplace v2 (skill commerce, licensing, payment) → Phase 4
- Federated skills (cross-org skill sharing) → Phase 5
- Natural language skill generation ("create a skill that...") → Phase 6
- Skill auto-merging (two similar skills consolidate) → Phase 7

---

## Risk Matrix

| Risk | Likelihood | Severity | Mitigation |
|------|-----------|----------|-----------|
| Optimizer oscillates (config ping-pongs) | Medium | High | Require 10+ feedback points + slope test before applying delta |
| Skill version explosion (too many versions) | Medium | Low | Auto-prune versions older than 1 week + unused (zero calls) |
| Feedback loop becomes adversarial (bad actors) | Low | High | Feedback source auth + rate-limit per source + anomaly detection |
| Canary rollout causes cascading failures | Low | High | Health check thresholds in ADR-0836; manual circuit breaker + auto-rollback |
| OTEL telemetry impacts performance | Low | Medium | Benchmark P99 latency before/after; target <50ms overhead |

---

## Related ADRs & Dependencies

| ADR | Status | Implication | Phase |
|-----|--------|-------------|-------|
| ADR-0836 | PROPOSED | Skill Forge v2 manifest schema | 3A |
| ADR-0677 | PROPOSED | ZIP packaging for skills (marketplace) | 3A |
| ADR-0314 | ACCEPTED (Phase 2) | Learning event schema; Phase 3 extends | 3B |
| ADR-0613 | ACCEPTED | Learning loop closure framework | 3B |
| ADR-0243 | ACCEPTED | Plugin boot layers; skills = plugins | 3A |
| ADR-0232 | ACCEPTED | Audit chain + tenant isolation | 3 (all) |
| ADR-0297 | ACCEPTED | PII detection; no feedback text in UI | 3C |
| ADR-0761 | ACCEPTED | Console chart patterns; re-use for dashboards | 3C |
| ADR-2075 | ACCEPTED | Phase 2 Vibe Dashboard (foundation for Phase 3) | 3C |

---

## Implementation Handoff (for Session 4)

**Part 1 (Session 4):** OTEL Multi-Tenant Instrumentation ✅ DONE (9 tests pass)

**Part 2 (Session 4):** Phase 3 Scope Document — **THIS DOCUMENT**

**Part 3 (Session 5+):** Begin Workstream A (Skill Forge v2) with ADR-0836 gate

---

## Appendix: Key Concepts

### Skill Manifest
```yaml
id: os.delegation_router
version: 2.1.3
boot_layer: core  # ADR-0243
dependencies:
  - os.context_adapter:^2.0
constraints:
  - require_audit: true  # ADR-0232
  - require_tenant_id: true  # ADR-0033
config:
  confidence_threshold: 0.70
  max_retries: 3
```

### Feedback Signal Flow
```
User feedback (thumbs up/down) 
  → ADR-0314 FeedbackEvent stored 
  → Optimizer reads 10+ events 
  → Computes config delta 
  → New skill version deployed (canary 5%) 
  → Next invocation uses new config
  → Confidence trend monitored
```

### Learning Convergence Proof
```
Feedback points: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
Confidence:      [0.4, 0.42, 0.45, 0.48, 0.50, 0.52, 0.55, 0.57, 0.59, 0.61]
Trend:           ✅ POSITIVE (slope > 0.02 per point)
Verdict:         ✅ CONVERGING (learning is happening)
```

---

**Phase 3 Kickoff Status: 🟢 READY FOR IMPLEMENTATION**

Next: Begin Workstream A (Session 5+)
