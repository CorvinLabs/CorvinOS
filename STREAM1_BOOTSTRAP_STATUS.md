# Phase 10 Stream 1: Workflow Optimizer — Bootstrap Status

**Date:** 2026-09-22  
**Status:** 🟡 **BOOTSTRAP IN PROGRESS** (Week 0.5)  
**ADR:** ADR-2030 (ACCEPTED)  
**Timeline:** Week 1–10 (8–10 weeks total)

---

## Completed (Week 0 Bootstrap)

- ✅ ADR-2030 written + ACCEPTED (architectural foundation)
- ✅ Project structure created (`core/skills/os_skills/workflow_optimizer/`)
- ✅ Core Skill logic scaffolded (350 LoC Week 1–2 target)
- ✅ Config manager skeleton (250 LoC Week 2–3 target)
- ✅ Test suite infrastructure created (82 test stubs)
- ✅ Weekly status tracking file (this file)

---

## 🚀 CRITICAL PATH (Week 1–10)

### Week 1–2: Core Skill Logic (350 LoC)
**Target:** `os.workflow_optimizer.WorkflowOptimizer` class + task classifier

**Deliverables:**
- [ ] `core/skills/os_skills/workflow_optimizer/skill.py` (200 LoC)
  - Class: `WorkflowOptimizer` (extends base Skill)
  - Method: `route_task(task) → RoutingDecision` (classify task type + pick model)
  - Method: `classify_complexity(task) → Literal["simple", "medium", "complex"]`
  - Method: `pick_model(complexity) → Literal["haiku-4-5", "sonnet-5", "opus-5"]`
  - Confidence scoring (0–1 range)
  - Audit event emission

- [ ] `core/skills/os_skills/workflow_optimizer/classifier.py` (150 LoC)
  - Task feature extraction (keyword count, nesting depth, API complexity, etc.)
  - Classification thresholds (confidence levels for each complexity tier)
  - Unit tests: 10 tests for feature extraction + classification

**Exit Criteria:**
- [ ] 25 unit tests passing (10 classifier + 15 routing logic)
- [ ] Audit events logged correctly (each routing decision captured)
- [ ] No hard-coded paths (all configurable via config manager, Week 2)

---

### Week 2–3: Config Manager (250 LoC)
**Target:** Persistence layer for routing config

**Deliverables:**
- [ ] `core/skills/os_skills/workflow_optimizer/config_manager.py` (200 LoC)
  - Class: `WorkflowConfig` (immutable, frozen dataclass)
  - Class: `ConfigManager` (load/save/validate config)
  - Tenant-scoped persistence: `<tenant>/global/workflow_optimizer_config.json`
  - Schema validation (Pydantic or attrs)
  - Fallback to defaults on missing config

- [ ] `core/skills/os_skills/workflow_optimizer/models.py` (50 LoC)
  - Data models: `RoutingDecision`, `FeedbackEvent`, `ConfigDelta`

**Exit Criteria:**
- [ ] 15 unit tests (config I/O + validation + defaults)
- [ ] Config can be read/written without errors
- [ ] Tenant isolation verified (no cross-tenant leakage)

---

### Week 3–4: Learning Loop Integration (400 LoC)
**Target:** Wire to ADR-0314 feedback loop

**Deliverables:**
- [ ] `core/skills/os_skills/workflow_optimizer/learning.py` (200 LoC)
  - Class: `LearningOptimizer` (receives feedback, computes config deltas)
  - Method: `process_feedback(feedback_event) → ConfigDelta`
  - Method: `update_confidence_scores(feedback_list) → Dict[str, float]`
  - Feedback validation (ignore contradictory signals)

- [ ] `core/console/corvin_console/routes/workflow_optimizer.py` (200 LoC)
  - Route: POST `/v1/console/workflow-optimizer/route` (classify + route)
  - Route: POST `/v1/console/workflow-optimizer/feedback` (ingest feedback)
  - Route: GET `/v1/console/workflow-optimizer/config` (read current config)
  - Route: PUT `/v1/console/workflow-optimizer/config` (operator override)
  - Audit event emission on all routes

**Exit Criteria:**
- [ ] 20 E2E tests (5 per route type + 5 feedback processing)
- [ ] Feedback loop closes: feedback → config update → routing change
- [ ] Console routes handle errors gracefully (timeouts, validation failures)

---

### Week 4–6: UI + Observability (350 LoC)
**Target:** Console panel + dashboards

**Deliverables:**
- [ ] `core/console/corvin_console/web-next/src/pages/workflow-optimizer/` (300 LoC)
  - Component: `WorkflowOptimizerDashboard` (main panel)
  - Component: `RoutingAccuracyChart` (line chart, trend over time)
  - Component: `ConfidenceGauge` (0–1 confidence indicator)
  - Component: `ExecutionPathHeatmap` (which paths are used most?)
  - Component: `FeedbackForm` (user feedback capture)
  - Component: `ConfigOverride` (operator controls)

- [ ] Grafana dashboard (50 LoC YAML)
  - Metrics: routing decisions/sec, accuracy %, confidence trend
  - Alerts: low confidence, feedback spike, config instability

**Exit Criteria:**
- [ ] 20 UI tests (React component rendering + interaction)
- [ ] 7 audit trail tests (decisions logged + immutable)
- [ ] Console routes correctly wired to UI
- [ ] Hard-refresh verified to show new UI

---

### Week 6–8: Production Hardening (200 LoC)
**Target:** Resilience + security + GDPR compliance

**Deliverables:**
- [ ] Timeout handling (non-blocking, fail-safe routing)
- [ ] Rollback on config corruption (safe defaults)
- [ ] Multi-tenant isolation verified (audit reads tenant-scoped only)
- [ ] GDPR compliance: no PII in configs, feedback, or logs
- [ ] Load test: 1,000 routing decisions/sec
- [ ] Security review: 0 critical findings (CWE-89 SQL injection, CWE-434 arbitrary file upload, etc.)

**Exit Criteria:**
- [ ] 15 adversarial security tests (injection attacks, timeout abuse, config tampering)
- [ ] Load test report: throughput + latency p99 measured
- [ ] GDPR audit: no PII signals detected in any payload
- [ ] Gate 3 approval: 82 tests ✅, code review ✅

---

### Week 8–10: Integration + Staging QA (150 LoC)
**Target:** End-to-end production readiness

**Deliverables:**
- [ ] Integration with L5 auto-routing (shadow mode wiring)
- [ ] E2E test: full workflow learning loop (real task → feedback → optimization → next task)
- [ ] Staging deployment canary (5% traffic, 7-day soak test)
- [ ] Production monitoring (metrics + alerting configured)
- [ ] Documentation: operator guide + troubleshooting

**Exit Criteria:**
- [ ] 82/82 tests passing ✅
- [ ] ADR-2030 status → ACCEPTED (marked as production-ready)
- [ ] Code review approved (2 reviewers sign-off)
- [ ] Security review: ≤2 high findings (none critical)
- [ ] Staging soak: 7 days, 0 critical incidents
- [ ] Ready for production rollout

---

## 📊 METRICS TRACKING

| Week | Target LoC | Actual LoC | Tests Passing | Gate Status |
|---|---|---|---|---|
| 1–2 | 350 | — | 25/82 | — |
| 2–3 | 250 | — | 40/82 | — |
| 3–4 | 400 | — | 60/82 | — |
| 4–6 | 350 | — | 70/82 | — |
| 6–8 | 200 | — | 82/82 | Gate 3 ✅ |
| 8–10 | 150 | — | 82/82 | Final ✅ |
| **TOTAL** | **1,700** | — | **82/82** | **Ready** |

---

## 🚨 BLOCKERS & RISKS

| Blocker | Impact | Mitigation |
|---|---|---|
| Phase 9 Remediation incomplete | CRITICAL | ADR-0232/0233 fixes must land by 2026-09-26 (3 days) |
| ADR-0314 (Learning Infra) not ready | HIGH | Block Week 3 if feedback schema not finalized |
| Console auto-reload flag OFF in production | MEDIUM | Manual hard-refresh required; document prominently |
| Load testing infrastructure missing | MEDIUM | Use existing `core/benchmarking/` tools or create mock load generator |
| Staging deployment blocked by Phase 9 security issues | HIGH | Resolve by Gate 3 (Week 6) for staging canary |

---

## 📅 GATE SCHEDULE (FIRM DATES)

| Gate | Week | Date | Criteria |
|---|---|---|---|
| **Gate 1** | 1 | 2026-09-29 | ADR-2030 scope confirmed + Week 1 code scaffolded + 10 tests running |
| **Gate 2** | 3 | 2026-10-06 | Config manager done (Week 2–3 complete) + 40 tests ✅ |
| **Gate 3** | 6 | 2026-10-13 | Stream 1 complete + 82 tests ✅ + code review approved + security review ✅ |
| **Final** | 10 | 2026-10-20 | Staging soak test ✅ + ready for production |

---

## 📋 DEPENDENCIES (Must Complete Before Week 1)

- [ ] Phase 9 remediation: all P0 + P1 fixes merged (due 2026-09-26)
- [ ] ADR-0314 (Learning Infrastructure) validated
- [ ] ADR-0534 (Feedback Integration Schema) finalized
- [ ] Staging environment stable (no deployments in flight)
- [ ] Team assignments confirmed (lead + 2 senior engineers)

---

## 🎯 SUCCESS DEFINITION

**Stream 1 is COMPLETE when:**

1. ✅ All 1,450 LoC delivered (target: 1,700 actual with tests/docs)
2. ✅ 82/82 tests passing (0 flakes)
3. ✅ ADR-2030 status remains ACCEPTED (production-ready, no revisions)
4. ✅ Code review: 2 approvals, 0 blocking comments
5. ✅ Security review: 0 critical, ≤2 high findings
6. ✅ Load test: 1,000 req/sec, p99 latency <200ms
7. ✅ Staging soak: 7 days, 0 critical incidents
8. ✅ Audit trail integrity: all decisions logged + hash-chain verified
9. ✅ GDPR compliance: no PII signals, tenant isolation verified
10. ✅ Documentation: operator guide + troubleshooting complete

---

## 📝 WEEKLY REPORTING

Every Friday EOD, update:
- LoC progress (vs 1,700 target)
- Test pass rate (vs 82 target)
- Blocker status (resolved or escalated)
- Next week priorities

Report template: See `STREAM1_WEEKLY_LOG.md` (created automatically each Friday)

---

## 📞 TEAM ASSIGNMENT (To Be Confirmed at Kickoff)

- **Stream Lead:** __________ (architect, final approval)
- **Dev 1:** __________ (core skill logic + classifier)
- **Dev 2:** __________ (config manager + learning loop)
- **Test:** __________ (E2E + adversarial tests)
- **Security:** __________ (hardening + pen testing)
- **DevOps:** __________ (staging deployment + monitoring)

---

## 🔗 REFERENCE DOCUMENTS

- **ADR-2030:** `/home/shumway/projects/Corvin-ADR/decisions/ADR-2030-workflow-optimizer-skill.md`
- **ADR-0532:** Skills 2.0 Architecture (OS-Skills foundation)
- **ADR-0314:** Learning Infrastructure (feedback loop)
- **ADR-0534:** Feedback Integration Schema (Week 1 deliverable)
- **PHASE_10_MASTER_ORCHESTRATION.md:** Full 12-week plan

---

**Status Last Updated:** 2026-09-22, 18:00 UTC (Bootstrap Phase)  
**Next Update:** 2026-09-29 (Gate 1 checkpoint)

---

> **MISSION:** Transform the deprecated `workflow_optimizer.py` (dead code) into a production-ready OS-level Skill that learns optimal task routing from operator feedback. Timeline: 8–10 weeks (Sep 26 – Oct 20 + Nov). Success: 82 tests ✅, audit trail intact, Streams 2–3 can start in parallel by Week 3.
