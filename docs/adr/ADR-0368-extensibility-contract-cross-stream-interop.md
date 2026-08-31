---
id: ADR-0368
status: PROPOSED
depends_on:
  - ADR-0367  # Master Orchestration Plan
  - ADR-0347  # Brain Subsystem Hub
  - ADR-0352  # Console Plugin Platform
  - ADR-0233  # Plugin System (existing reference)
relates_to:
  - ADR-0348  # Event Bus Pattern
  - ADR-0366  # AI-Panels (Console)
  - ADR-0299  # Audit Durability L16 (Refactoring)
  - ADR-0016  # Audit-Chain Integrity
paths:
  - core/orchestration/subsystems/
  - core/console/plugins/
  - core/compliance/
docs:
  - docs/implementation/EXTENSIBILITY-CONTRACT.md
  - docs/deployment/INTEROP-MATRIX.md
---

# ADR-0368: Extensibility Contract — Brain + Console + Refactoring Interoperability

**Decision Date:** 2026-08-23  
**Deciders:** shumway, Claude Code  
**Status:** PROPOSED (awaits adversarial review)

---

## Problem

Three subsystems (Brain v0.2, Console Plugin Platform, Master Refactoring) must interoperate:

**Current State:**
- Brain (ADR-0347) defines Subsystem contract: `name`, `version`, `startup()`, `on_event()`, `handle_request()`
- Console (ADR-0352–0366) defines Plugin contract: `mount_path`, `spa_dist_dir`, capability gates
- Refactoring (ADR-0294+) defines Auth/Compliance contracts: `@auth_required`, `@requires_capability`

**Missing:** Explicit interop contract. When Console Plugin publishes event, does Brain subsystem see it? When Refactoring audit-logs a Brain event, is Console Plugin audited? Where do boundaries live?

**Risk:** Implicit assumptions lead to:
- Brain publishes event → Console Plugin misses it (async delivery gap)
- Console Panel-Registry mutates → Audit-chain has no entry (compliance gap)
- Refactoring auth decorator rejects Brain request → Brain has no fallback (resilience gap)

---

## Solution: 3-Tier Interop Contract

### Tier-1: Event Delivery Guarantee (Brain ↔ Console)

**Contract:** Brain EventBus publishes events in 3 reliability classes:

| Class | Guarantee | Use-Case | Subsystems |
|-------|-----------|----------|-----------|
| **Tier-1 (Ordered FIFO)** | Event delivered in order, no loss, timeout = error | Auth, Compliance, Audit-Chain | Brain → Console Panel-Registry, Brain → Refactoring Audit |
| **Tier-2 (Causal)** | Events within same causal chain ordered, cross-chain async | UI state, Subsystem coordination | Brain LoopEngineer → LearningEngine, Console Panel → Panel-Registry |
| **Tier-3 (Best-effort)** | No guarantee, may be lost, timeout = warning | Analytics, telemetry, debug logs | Brain → observability, Console → metrics |

**Implementation:**
- EventBus maintains 3 separate queues, each with retry + timeout policy
- Tier-1: Retry 3x, timeout = 30s → escalate
- Tier-2: Retry 1x, timeout = 10s → drop + log
- Tier-3: Fire-and-forget, timeout = 5s → drop silent

**Cost:** ~2 days implementation (queue infrastructure already exists)

---

### Tier-2: Audit-Chain Binding (Refactoring ↔ Console ↔ Brain)

**Contract:** All state mutations flow through audit-chain:

```
Brain Event
  ↓ (Tier-1 ordered)
Console Panel-Registry Update
  ↓ (audit-log via ADR-0299)
Refactoring Audit-Chain Entry
  ↓ (hash-chained, verified)
Compliance Dashboard
```

**Boundary:** Refactoring ADR-0299 is the **single audit writer**. Brain and Console do NOT write audit directly; they emit events that Refactoring consumes and logs.

**Implementation:**
- Brain.publish_event(name, data) → Refactoring.audit_handler receives (synchronous, via hub.request)
- Console.panel_registry_mutate() → via EventBus → Refactoring.audit_handler
- Refactoring audit-handler writes hash-chained entry

**SLA:** Audit latency ≤ 100ms from event emission.

---

### Tier-3: Auth-Capability Gating (Refactoring ↔ Console ↔ Brain)

**Contract:** All subsystem-to-subsystem requests must pass auth-capability check:

```
Brain.request_from_subsystem("console_plugins", "mount_panel", ...)
  ↓ (via hub.request_from_subsystem)
Refactoring.@requires_capability("panel_mount") decorator checks
  ↓ (checks caller context via ContextVar + ADR-0302)
Allowed? Yes → proceed; No → reject + audit "unauthorized attempt"
```

**Boundary:** Auth/Capability is **Refactoring's responsibility**. Brain does not enforce auth; Refactoring intercepts all cross-subsystem requests.

**Implementation:**
- Hub.request_from_subsystem wraps caller context (tenant_id, persona, capabilities)
- All subsystem.handle_request() calls go through @requires_capability decorator
- Decorator checks ContextVar (set by auth layer)

**SLA:** Auth check latency ≤ 5ms (ContextVar lookup is O(1)).

---

## Boundaries Explicitly Defined

### Brain's Boundary (v0.2-rc1)

**Responsible for:**
- Event publication (Tier-1/2/3)
- Request routing (via hub)
- Subsystem coordination (no direct imports)

**NOT responsible for:**
- Audit-chain writing (Refactoring does this)
- Auth enforcement (Refactoring does this)
- UI rendering (Console does this)

**Contracts Brain MUST honor:**
- `event_delivery_sla[tier]` for each published event
- All cross-subsystem requests routed through hub (no direct calls)
- Subsystem lifecycle (startup/shutdown ordering documented)

### Console's Boundary (Plugin Platform)

**Responsible for:**
- UI rendering (panels, nav, settings)
- Panel lifecycle (mount/unmount)
- Capability-gated feature visibility

**NOT responsible for:**
- Audit-chain writing (emit event → Refactoring handles audit)
- Auth enforcement (rely on Refactoring decorators)
- Event publishing (receive events from Brain, don't emit)

**Contracts Console MUST honor:**
- All panel-registry mutations emit events (not direct writes)
- Listen for Tier-1 events only (not async Tier-3)
- Register capabilities with Brain (for auth decorator)

### Refactoring's Boundary (Phase 1+)

**Responsible for:**
- Audit-chain writing (hash-chain integrity)
- Auth enforcement (@requires_capability decorators)
- Compliance validation

**NOT responsible for:**
- Event publication (Brain does this)
- UI rendering (Console does this)
- Subsystem coordination (Brain does this)

**Contracts Refactoring MUST honor:**
- Audit-handler processes all Tier-1/2 events within SLA
- @requires_capability decorator never silently fails (logs denial)
- Auth ContextVar persists across async boundaries (via task-local storage)

---

## Conflicts Resolved by This Contract

### Conflict A: "Who publishes panel-registry events?"

**Before:** Unclear. Console publishes? Brain publishes? Both?

**After (ADR-0368):** Console publishes events, Refactoring logs them. Brain's EventBus handles delivery.

**Evidence:** Tier-1 ordering guarantee means Console → event → Refactoring audit, all in-order.

### Conflict B: "What if Brain request times out?"

**Before:** No timeout defined. Caller hangs forever?

**After (ADR-0368):** Tier-1 timeout = 30s, escalates to error. Caller must handle (fail closed).

**Evidence:** SLA contract explicitly bounds timeout.

### Conflict C: "Can Console write audit directly?"

**Before:** Ambiguous. Both Brain and Console reference audit.

**After (ADR-0368):** No. Only Refactoring writes audit. Console emits events; Refactoring logs.

**Evidence:** Single audit-writer pattern simplifies compliance verification.

---

## Interop Matrix (Who Talks to Whom)

| From | To | Via | Event Tier | SLA |
|------|----|----|------|-----|
| Brain (LoopEngineer) | Brain (LearningEngine) | hub.request | N/A (request/response) | 10s timeout, retry 1x |
| Brain (HealthMonitor) | Console (Panel-Registry) | EventBus | Tier-2 (causal) | <10s latency |
| Console (Panel-Registry) | Refactoring (Audit) | EventBus + hub | Tier-1 (ordered) | <100ms latency |
| Refactoring (Auth) | Brain (any subsystem) | @requires_capability | N/A (decorator) | <5ms latency |
| Refactoring (Audit) | Console (audit-visible events) | read-only query | N/A (no writes) | <50ms latency |

---

## Risk Analysis

### Risk 1: Event delivery SLA not met (Brain publishes, Console misses)

**Mitigation:**
- Add monitoring: EventBus tracks delivery latency per tier
- Alert if Tier-1 delivery > 50ms (2x SLA)
- Add e2e test: 1000 events, verify 100% delivery + ordering

### Risk 2: Audit-chain gaps (Brain event not logged by Refactoring)

**Mitigation:**
- Refactoring.audit_handler is the ONLY audit writer (single source of truth)
- Add unit test: every event type produces audit entry
- Add e2e test: Brain → event → Console → Refactoring audit, verify chain integrity

### Risk 3: Auth timeout causes cascade failure (all requests blocked)

**Mitigation:**
- Auth timeout ≤ 5ms (ContextVar lookup, no IO)
- If timeout, fail closed (reject request, audit denial)
- Add e2e test: 1000 concurrent requests, verify SLA hold

---

## Success Criteria

- [ ] ADR-0368 passes adversarial review (0 findings)
- [ ] EventBus queue infrastructure implemented (Tier-1/2/3)
- [ ] Interop matrix tested (e2e: Brain → Console → Refactoring)
- [ ] Audit-chain binding verified (100% coverage)
- [ ] SLA monitoring wired (alerts on timeout)
- [ ] Phase 1 ADRs use this contract (0294-0301 reference ADR-0368)

---

## Decision

**We adopt the 3-Tier Interop Contract:**

- **Tier-1 (Ordered FIFO):** Auth, Compliance, Audit-Chain events
- **Tier-2 (Causal):** UI state, subsystem coordination
- **Tier-3 (Best-effort):** Analytics, telemetry

**Boundaries explicitly owned:**
- Brain: event publication + routing
- Console: UI + panel lifecycle
- Refactoring: audit + auth

**Next: Adversarial review of ADR-0367 + 0368 combined (0 findings gate).**

