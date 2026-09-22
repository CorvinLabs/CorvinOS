# PHASE 10: ADVANCED SKILLS & AUTONOMOUS LEARNING

## Planning Document v1.0

**Status:** PLANNING (Phase 10 BLOCKED by Phase 9 remediation)  
**Timeline:** 8–12 weeks (after Phase 9 remediation complete; estimated kickoff 2026-09-26)  
**Goal:** Build 3 production-ready OS-level Skills on ADR-0532 + ADR-0314 foundation

---

## PREREQUISITE: Phase 9 Remediation

**BLOCKER:** Phase 9 security remediation (2–3 days) must complete before Phase 10 starts.

See `/home/shumway/projects/CorvinOS/docs/PHASE_9_SECURITY_REMEDIATION_PLAN.md` for details.

**Phase 10 Kickoff:** 2026-09-26 (after Phase 9 fixes verified)

---

## VISION: Skills as Agentic Control Plane

Phase 10 transforms CorvinOS from **task-runner** → **agentic operating system** by making **OS-level Skills** the unified control plane.

A Skill is NOT a static prompt; it is a **stateful program** that:
- ✅ Owns a domain (routing, workflow, security, data flow)
- ✅ Executes deterministic Python (fast, auditable, fail-closed)
- ✅ Calls LLM on demand (e.g., "classify task complexity")
- ✅ Learns via feedback (ADR-0314 learning events)
- ✅ Composes with other Skills (dependency graph, topological sort)
- ✅ Versioned & deployed (semantic versioning, canary rollout)
- ✅ Auditable (every decision logged, immutable, tenant-scoped)

---

## SCOPE: Three Skills, Three Subsystems

| Skill | Input | Output | Effort | Timeline |
|---|---|---|---|---|
| **Workflow Optimizer** | User feedback on execution chains | Optimized task routing (fast/slow path prioritization) | 3 weeks | Weeks 1–3 |
| **Security Orchestrator** | Attack patterns from audit trail | Dynamic security policy updates (tighten gates on threats) | 4 weeks | Weeks 4–7 |
| **Flow Guard** | Data classification labels + flow outcomes | Dynamic data flow policies (allow safe, block risky) | 3 weeks | Weeks 5–7 |

**Shared Infrastructure** (all 3 skills):
- Console UI panels (3 panels for Skill control/observability)
- Learning loop integration (feedback → optimizer → config update)
- Compliance gates (audit-first, consent-gated, house-rules non-bypassable)
- Tests: 15 E2E integration + 20 adversarial gates
- Timeline: Weeks 7–8

---

## ARCHITECTURE: Skills 2.0 Pattern

```
Phase 9 (DONE):          Phase 10 (NEW):
Intent Router      →     Workflow Optimizer
Plugin Manager    →     Security Orchestrator
Subsystems        →     Flow Guard
Overrides                (Dynamic policy)
Snapshots

All wired to:            Learning Loop (ADR-0314)
  - Feedback events      - Outcome events
  - Consent gates        - Config optimization
  - Audit trail          - Confidence scoring
  - Tenant isolation     - Decision graphs
```

---

## SKILL 1: Workflow Optimizer (Weeks 1–3)

**Responsibility:** Learn execution chains from user feedback; optimize task routing.

### Input
```python
# User feedback on execution chains
FeedbackEvent = {
    "task_id": "task_123",
    "chain": ["intent_classify", "plugin_exec", "subsystem_control"],
    "latency_ms": 2341,
    "success": True,
    "outcome": "recommended_for_future",  # yes/no/neutral
    "reasoning": "Fast path; good result"
}
```

### Output
```python
# Optimized routing config
SkillConfig = {
    "fast_paths": ["intent_classify", "intent_quick_fallback"],
    "slow_paths": ["plugin_market_lookup", "cross_tenant_check"],
    "priorities": {"fast_paths": 0.95, "slow_paths": 0.05},
    "confidence": 0.87  # Based on feedback count
}
```

### Key Features
1. **Real-time learning:** Feedback → config delta → next invocation uses tuned params
2. **Confidence scoring:** How confident is the optimizer in its routing decision?
3. **Fallback safety:** Always have a slow/safe fallback path
4. **Audit trail:** Every routing decision + feedback + optimization step logged

### Tests
- 5 E2E: feedback → config update → routing change
- 5 E2E: fallback activation on timeout
- 5 E2E: confidence scoring accuracy

---

## SKILL 2: Security Orchestrator (Weeks 4–7)

**Responsibility:** Detect threat patterns from audit trail; dynamically harden security gates.

### Input
```python
# Attack patterns detected in audit trail
ThreatSignal = {
    "pattern": "brute_force_auth",
    "detected_at": "2026-09-22T12:34:56Z",
    "attack_count": 15,
    "affected_users": ["user1", "user2"],
    "confidence": 0.92
}
```

### Output
```python
# Dynamic security policy update
SecurityDelta = {
    "gate": "auth_gate",
    "action": "tighten",
    "old_threshold": 3,  # 3 failed attempts allowed
    "new_threshold": 1,  # Now 1 attempt
    "reason": "Brute force detected",
    "TTL_s": 3600  # Revert after 1h if no more attacks
}
```

### Key Features
1. **Real-time threat detection:** Audit trail → threat signal → policy tightening
2. **Automatic reversion:** Tightened gates expire after TTL if threat clears
3. **Compliance-first:** All policy changes logged, audit-first, house-rules always enforced
4. **Tenant-scoped:** Each tenant has independent threat model + policy state

### Tests
- 5 E2E: attack pattern detected → policy tightened → attack blocked
- 5 E2E: gate reversion after TTL
- 5 E2E: policy doesn't violate house-rules

---

## SKILL 3: Flow Guard (Weeks 5–7)

**Responsibility:** Learn safe data shapes from flow outcomes; dynamically allow/block data paths.

### Input
```python
# Data flow classification + outcome
FlowOutcome = {
    "data_class": "personal_email",
    "destination_engine": "gpt-4",  # or "anthropic", "bedrock"
    "flow_allowed": True,
    "result": "success",
    "reasoning": "Email → Anthropic is safe per policy"
}
```

### Output
```python
# Dynamic flow policy
FlowPolicy = {
    "allow": [
        {"data_class": "personal_email", "destination": "anthropic", "confidence": 0.99},
        {"data_class": "structured_data", "destination": "*", "confidence": 0.85}
    ],
    "deny": [
        {"data_class": "credentials", "destination": "*", "confidence": 1.0}  # Always deny
    ]
}
```

### Key Features
1. **Learning from outcomes:** Each flow attempt → outcome → policy refinement
2. **Confidence-based:** Allow higher-confidence flows, require approval for uncertain ones
3. **Never weaken:** Confidence only goes up (stricter), never down
4. **Audit + reasoning:** Every allow/deny decision + confidence + reasoning logged

### Tests
- 5 E2E: flow outcome → policy confidence update → next flow uses new policy
- 5 E2E: confidence-based approval gate (uncertain flows require review)
- 5 E2E: policy never weakens (deny stays deny)

---

## CONSOLE UI: Skill Control Panels

### Skill 1: Workflow Optimizer Panel
- Show: routing accuracy trend, latency distribution, confidence score
- Control: toggle fast/slow paths, adjust priority weights
- Feedback: list recent user feedback, confidence sources

### Skill 2: Security Orchestrator Panel
- Show: threat timeline, policy tightening/reversion history, compliance status
- Control: manual policy override (with approval), TTL adjustment
- Alerts: active threats + mitigations applied

### Skill 3: Flow Guard Panel
- Show: data class distribution, allow/deny ratio, confidence heatmap
- Control: adjust confidence thresholds, approve high-uncertainty flows
- Audit: all allow/deny decisions + reasoning

---

## INTEGRATION & TESTING

### Week 7: Full Integration
- All 3 skills running in parallel
- Feedback loop wired end-to-end (outcome → learning event → config update)
- Console panels live and responsive

### Week 8: Adversarial Testing
- 20 adversarial gates (feedback injection, policy bypass, confidence spoofing)
- Cross-skill consistency (skills don't contradict each other)
- Audit trail integrity (all decisions logged, hash-chain verified)
- Tenant isolation (no cross-tenant leaks)

---

## TESTING REQUIREMENTS

### E2E Tests (60 total)
- 15 per skill (Workflow, Security, Flow)
- 15 integration (skills working together)

### Adversarial Tests (20 total)
- Feedback injection attacks
- Policy bypass attempts
- Confidence spoofing
- Audit trail tampering
- Cross-tenant attacks

### Unit Tests
- Per-skill logic (routing, threat detection, classification)
- Config serialization + deserialization
- Confidence scoring accuracy
- Feedback event validation

---

## COMPLIANCE GATES (All Skills)

### Audit-First (ADR-0232/0233)
Every Skill decision logged BEFORE committed:
```python
@audit_first
def workflow_optimizer.route_task(task) -> RoutingDecision:
    # 1. Write audit event
    # 2. If audit fails, don't route (fail-closed)
    # 3. Return routing decision
```

### Consent-Gated (ADR-0232)
All Skill operations require user consent:
```python
@consent_required("workflow_optimization")
@consent_required("security_hardening")
@consent_required("data_flow_learning")
async def activate_skill(skill_id: str):
    # Only if user consented
```

### House-Rules Non-Bypassable (CLAUDE.md)
No Skill can weaken compliance mechanisms:
```python
def security_orchestrator.tighten_policy(gate_id):
    # Policy can only tighten, never bypass house-rules
    # Fail-closed if conflict detected
```

### Tenant-Scoped (ADR-0007)
All Skill state is tenant-local:
```python
workflow_config = get_config(tenant_id=rec.tenant_id)
# No cross-tenant leakage
```

---

## DEPENDENCIES

### Required (Blocking)
1. **Phase 9 remediation** (2–3 days) — Security fixes must complete
2. **ADR-0314** (Learning Infrastructure) — Event schema + persistence ✅ DONE
3. **ADR-0532** (Skills 2.0 Architecture) — Skill execution model ✅ DONE

### Recommended
4. **ADR-0534** (Feedback Integration Schema) — Standardized feedback format ↔ Phase 10 Week 1

### Optional (But Nice)
5. **Grafana dashboard** (monitoring) — For observability

---

## TIMELINE

| Week | Phase | Deliverables |
|---|---|---|
| 1–3 | **Skill 1: Workflow Optimizer** | 550 LoC + 15 tests + console panel |
| 4–7 | **Skills 2–3: Security + Flow Guard** | 950 LoC + 30 tests + 2 panels |
| 7–8 | **Integration + Adversarial + Go/No-Go** | 500 LoC tests + sign-off |
| **Total** | **8 weeks, 3–4 FTE** | **2,000 LoC, 65 tests, 20 adversarial gates** |

---

## RESOURCES REQUIRED

- **1 Lead Engineer** (architecture + workflow skill)
- **2 Senior Engineers** (security + flow guard skills)
- **1 QA Engineer** (testing + adversarial gates)
- **1 PM** (planning + stakeholder updates)

**Total:** 3–4 FTE for 8 weeks

---

## SUCCESS CRITERIA

- ✅ All 3 skills production-ready (code review approved)
- ✅ 60 E2E tests passing (all green)
- ✅ 20 adversarial gates verified
- ✅ Audit trail integrity verified (hash-chain + tenant isolation)
- ✅ Console panels live + responsive (<500ms)
- ✅ Learning loop closed (feedback → config → outcome)
- ✅ Monitoring + alerting live

---

## BLOCKERS & RISKS

| Blocker | Status | Mitigation |
|---|---|---|
| **Phase 9 remediation** | 🔴 ACTIVE (2–3 days) | Execute ASAP; Phase 10 kickoff dependent |
| **ADR-0534 not written** | 🟡 PENDING | Write Week 1 (1-day task) |
| **Feedback schema unclear** | 🟡 PENDING | Define in ADR-0534 Week 1 |
| **Learning event volume** | 🟢 MANAGEABLE | Event store can handle 1k events/s |
| **Skill versioning** | 🟢 SOLVED | Use semantic versioning + canary |

---

## NEXT STEPS

1. **Complete Phase 9 remediation** (2–3 days)
2. **Write ADR-0534** (Feedback Integration Schema) — 1 day
3. **Review Phase 10 plan with stakeholders** — 1 day
4. **Kick off Week 1** (Workflow Optimizer) — 2026-09-26

---

**Document Version:** 1.0  
**Date:** 2026-09-22  
**Status:** DRAFT — Awaiting Phase 9 remediation completion  
**Next Review:** 2026-09-25 (post-Phase 9 fixes)
