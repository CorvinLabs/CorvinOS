---
id: ADR-0367
status: PROPOSED
depends_on:
  - ADR-0347  # Brain Subsystem Hub Architecture
  - ADR-0352  # Console Plugin Platform P0
  - ADR-0365  # Web-Surface Loader (P7 complete)
  - ADR-0294  # Auth Decorator Layer (Phase 1 done)
  - ADR-0299  # Audit Durability + L16 (Phase 1 critical blocker)
relates_to:
  - ADR-0348  # Event Bus Pattern
  - ADR-0366  # AI-Panels (Console)
  - ADR-0300  # Dual-Gate Context Pipeline (Phase 1, needs design review)
  - ADR-0301  # Pipeline Call-Site Wiring (Phase 1)
paths:
  - core/orchestration/
  - core/console/
  - core/compliance/
  - Corvin-ADR/decisions/ADR-0367*
docs:
  - docs/implementation/MASTER-ORCHESTRATION-PLAN.md
  - docs/deployment/PHASE-1-SEQUENCING.md
---

# ADR-0367: Master Orchestration Plan — Cross-Stream Sequencing

**Decision Date:** 2026-08-23  
**Deciders:** shumway, Claude Code  
**Status:** PROPOSED (awaits adversarial review)

---

## Problem

Three major architectural initiatives run in parallel:
- **Brain v0.2-rc1** (13 subsystems, pub/sub coordination) — PRODUCTION READY but Event-Ordering underspecified
- **Console Plugin Platform** (P0-P7 complete, ADR-0352–0366) — 2 gaps: P7 panel-mount-UI + dynamic nav from registry
- **Master Refactoring Phase 1** (9 ADRs, Foundation Security) — 49% done, 7 ADRs todo (0296-0301), blocked on ADR-0300 design review

**Challenge:** These streams have hidden dependencies and conflicts:
1. **Event-Ordering Conflict**: Brain v0.2 publishes events async (no guaranteed order). Console Plugin ADR-0366 (AI-Panels) needs reliable event delivery. ❌ Incompatible.
2. **Audit-Durability Gap**: Master Refactoring ADR-0299 (Audit Durability L16) is a critical blocker. Console Panel-Registry needs audit-chained events. ADR-0299 must complete before ADR-0366 ships.
3. **Design-Review Bottleneck**: Master Refactoring ADR-0300 (Dual-Gate Context Pipeline) needs "design review before impl" (memory note). This blocks ADR-0301, which blocks all Pipeline call-site wiring. **Single point of failure.**

**Goal:** Formalize critical path, dependencies, and conflict resolution so 3 streams converge correctly.

---

## Solution: 2-Phase Orchestration

### Phase 0: Pre-Flight (Weeks 1-2, parallel)

**Goal:** Resolve 3 conflicts + pre-schedule critical review.

| Task | Owner | Timeline | Blocker? | Evidence |
|------|-------|----------|----------|----------|
| **ADR-0367 + 0368 Adversarial Review** | Code Review Panel | Week 1 (5d) | ✅ BLOCKING | Conflict identification before any impl |
| **ADR-0300 Design Review** (Phase 1) | Architect | Week 1 (2d) | ✅ BLOCKING | Pre-schedule NOW (memory: "needs design review before impl") |
| **Brain Event-Ordering SLA Definition** | Brain Maintainer | Week 1 (3d) | ✅ BLOCKING | Spec: which events MUST be ordered? (FIFO, per-subsystem, causal?) |
| **Console Panel-Mount-UI Scope** (P7) | Console Maintainer | Week 1 (2d) | ⚠️ HIGH | Clarify: Is dynamic mount from Panel-Registry required for MVP? |

**Success Criteria:**
- ✅ Zero findings from ADR-0367/0368 adversarial review
- ✅ ADR-0300 design review complete + approved
- ✅ Brain Event-Ordering SLA documented (ADR amendment)
- ✅ Console P7 scope finalized (in/out of critical path)

### Phase 1: Sequential Implementation (Weeks 2-8)

**Critical Path:**
```
Week 2-2.5: Master Refactoring Phase 1 (ADRs 0296-0301)
├─ ADRs 0296, 0295 (parallel, 1 week)
├─ ADRs 0297, 0298 (parallel, 1 week)
├─ ADR-0299 (Audit Durability L16, 1 week) ← blocks everything downstream
└─ ADRs 0300, 0301 (sequential, 1.5 weeks total)
   └─ (includes design-review from Phase 0)

Week 3-3.5: Brain v0.2 Event-Ordering (ADR-0348 amendment)
├─ Spec event-ordering invariants (FIFO? causal? per-subsystem?)
├─ Add request timeouts (identified in Brain adversarial review)
└─ Test event-delivery reliability (50+ events, no loss)

Week 4-5: Console Plugin Integration
├─ Wire ADR-0299 Audit trails into ADR-0366 (AI-Panels)
├─ Implement P7 panel-mount-UI (if in-scope after Phase 0)
└─ Nav registry integration (dynamic nav from panel-registry)

Week 6-8: E2E Integration + Review
├─ Brain + Console + Refactoring end-to-end tests
├─ Adversarial review of integrated system (0 findings gate)
├─ Security assessment (compliance matrix)
└─ Production readiness validation
```

**Ownership:**
- **Phase 1 Refactoring (ADRs 0296-0301):** shumway (estimated 83.5h remaining)
- **Brain Event-Ordering (ADR-0348):** Brain Maintainer (estimated 20h)
- **Console P7 + Integration (ADRs 0366, P7 panel-mount):** Console Maintainer (estimated 30h)
- **E2E Review + Security:** Code Review Panel (estimated 40h)

---

## Conflicts Identified & Resolved

### Conflict 1: Brain Event-Ordering vs. Console Panel-Registry

**Tension:**
- Brain v0.2 publishes events async, no guaranteed order (memory: "Event Ordering is Underspecified")
- Console ADR-0366 (AI-Panels) needs reliable event delivery for Panel Installation events
- If events arrive out-of-order, Panel-Registry state corrupts

**Resolution:**
- **Define Event-Ordering SLA**: Classify events into 3 tiers:
  - **Tier-1 (Ordered)**: Authentication, Compliance, Audit-Chain events → FIFO guaranteed
  - **Tier-2 (Causal)**: UI state updates → causal ordering within event stream
  - **Tier-3 (Best-effort)**: Analytics, telemetry → no ordering guarantee
- **Implementation**: Add per-tier delivery guarantee in EventBus (ADR-0348 amendment)
- **Cost**: ~2 days dev, validated by e2e tests

**Tradeoff**: Stricter event ordering adds latency (~5-10ms per event). Acceptable for v0.2 (total wall-clock still << manual).

### Conflict 2: ADR-0299 Audit-Durability blocks ADR-0366 AI-Panels

**Tension:**
- Master Refactoring ADR-0299 (Audit Durability L16) is a critical foundation piece
- Console ADR-0366 (AI-Panels generation) needs audit trail for compliance
- If ADR-0299 slips, ADR-0366 cannot ship

**Resolution:**
- **Dependency Clarity**: Mark ADR-0299 as CRITICAL-PATH in sequencing
- **Parallel where possible**: AI-Panels can implement audit-trail CALLS before ADR-0299 ships, then wire audit backend when ready
- **Fallback**: If ADR-0299 slips, AI-Panels ships with audit-calls stubbed (no data loss, just queued)

**Tradeoff**: Decouples panels from audit, but audit data is only written AFTER ADR-0299 lands. Brief gap acceptable.

### Conflict 3: ADR-0300 Design Review Bottleneck

**Tension:**
- ADR-0300 (Dual-Gate Context Pipeline) needs design review before implementation
- Design review takes 3-5 days (estimates from prior reviews)
- ADR-0300 blocks ADR-0301, which blocks all Phase 1 wiring

**Resolution:**
- **Pre-schedule NOW** (Week 1 of Phase 0) instead of waiting for implementation
- **Parallel path**: ADRs 0296-0299 proceed in parallel while ADR-0300 is in design review
- **Benefit**: If design review uncovers issues, we catch them before coding starts

**Timeline Impact**: Design review +5d Week 1, but saves 15d of rework if issues found in implementation. Net positive.

---

## Key Assumptions & Risks

### Assumption 1: Event-Ordering SLA is tractable

**Risk:** Brain subsystems may have implicit ordering dependencies not yet documented.

**Mitigation:** Adversarial review (Phase 0) will stress-test ordering assumptions with edge cases (concurrent events, subscriber timeouts, event loss).

### Assumption 2: Console P7 panel-mount-UI scope is known

**Risk:** If panel-mount-UI is larger than estimated, Console integration timeline slips.

**Mitigation:** Phase 0 scope-finalization task (2 days) clarifies whether panel-mount is MVP-critical or v0.3 item.

### Assumption 3: Phase 1 ADRs implement cleanly without design issues

**Risk:** Implementation discovers design flaws (as happened with ADR-0300), causing re-work.

**Mitigation:** ADR-0300 design review in Phase 0 (pre-implementation). Adversarial review gates all 7 ADRs before coding starts.

---

## Success Criteria

| Milestone | Gate | Evidence |
|-----------|------|----------|
| **Phase 0 Complete (Week 2)** | Adversarial review 0 findings | ADR-0367/0368 approved, ADR-0300 design OK, Event-Ordering SLA defined |
| **Phase 1 Complete (Week 2.5)** | All 7 ADRs (0296-0301) merged | Tests green, audit chain verified, feature flags off |
| **Brain Event-Ordering (Week 3.5)** | SLA implemented + tested | 50+ concurrent events, zero loss, ordering validated |
| **Console Integration (Week 5)** | P7 + AI-Panels wired | Panel registry live, audit trail flowing, nav dynamic |
| **E2E Review Complete (Week 8)** | Security gate + compliance matrix | Zero findings, production readiness signed off |

---

## Alternatives Considered

### Alternative A: Implement streams independently, then integrate

**Rejected** because:
- Hidden conflicts only discovered at integration time (2-4 week delay)
- Risk of re-architecture mid-implementation
- No explicit ownership/sequencing

### Alternative B: Delay Console Plugin until Brain + Refactoring done

**Rejected** because:
- Console Panel-Registry can start in parallel (no blocker on Brain/Refactoring)
- Unnecessary serialization adds 4 weeks to timeline
- Blocks user-facing feature (AI-Panels) artificially

### Alternative C: Merge Event-Ordering fix into Brain v0.2 now

**Rejected** because:
- v0.2 already shipped, event-ordering is v0.3 item
- Amending v0.2 mid-production risks stability
- Better: document SLA now, implement guarantee in v0.3

---

## References

- **Brain v0.2 Review** (memory: brain-final-adversarial-review.md) — Event-Ordering underspecified, Request timeouts missing
- **Master Refactoring Phase 1** (memory: master-refactoring-plan-phase1-ongoing.md) — 9 ADRs, 49% complete, ADR-0300 design-review blocker
- **Console Plugin Roadmap** (memory: console-plugin-roadmap.md) — P0-P7 done, P7 panel-mount-UI + nav-gating open

---

## Decision

**We adopt the 2-Phase Orchestration approach:**

1. **Phase 0 (Weeks 1-2)**: Resolve conflicts, pre-schedule critical reviews, define SLAs
2. **Phase 1 (Weeks 2-8)**: Sequential implementation with explicit critical path (ADR-0299 → ADR-0300 → ADR-0301)

**Next: ADR-0368 (Extensibility Contract) formalizes the architectural boundaries between Brain, Console, and Refactoring.**

