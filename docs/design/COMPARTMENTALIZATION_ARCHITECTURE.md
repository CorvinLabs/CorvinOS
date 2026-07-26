# CorvinOS Compartmentalization — Architecture Study
## Staged Plugin Architecture + Observability + Self-Healing

**Date:** 2026-07-26
**Status:** Long-form study. **Canonical decision: ADR-0231**, as corrected by
**ADR-0233** (both in the `Corvin-ADR` repo).
**Authors:** Claude Code (Architecture)
**Stakeholders:** Security, Ops, Eng leads
**Relates to:** ADR-0030 (plugin lifecycle contract), ADR-0177 (Nervous System)

> **Corrections from ADR-0233 — apply these when implementing:**
> 1. **Stage 1 is additive, not extractive.** "Every core feature becomes a plugin"
>    does NOT apply to L16 audit or L18-21 auth. Core keeps writing its own
>    hash-chained `audit.jsonl` unconditionally; a backend plugin gets a copy and may
>    fan it out, and its failure is swallowed after logging. A `user_backend` that
>    raises, times out, or returns `None` means **deny** — never guest admission.
> 2. **House rules and data classification are not pluginifiable in any tier** —
>    ADR-0232 declares them mandatory and non-disableable.
> 3. **Stage 1 runs on `core/plugins/corvin_plugins/`** (ADR-0030), not on the
>    retired `core/orchestration/plugin_system/`.
> 4. **Stages 3–4 (self-healing, intelligent healing) are out of scope** until
>    Stage 2 has been stable for one release — this document's own gate.
> 5. Execution sequence: [`PLUGIN_SYSTEM_IMPLEMENTATION_PLAN.md`](../implementation/PLUGIN_SYSTEM_IMPLEMENTATION_PLAN.md).

---

## Problem Statement

**Today:** CorvinOS is monolithic. 44 layers tightly coupled. If one fails → cascades.
- Audit fails → platform crashes
- Auth hangs → all users locked out
- Router disabled → must redeploy
- Debugging is hard (no per-component observability)
- Self-healing is impossible (no visibility + no isolation)

**Symptom:** Single-point-of-failure architecture. No graceful degradation.

---

## Decision: 4-Stage Compartmentalization + Observability + Safe Healing

### Stage 1: Compartmentalization + Structured Logging (Phases 1-2 roadmap)
**Goal:** Isolate features. Make everything observable.

**What changes:**
- Every core feature becomes a plugin (Audit, Auth, Compute, Router, STT)
- Circuit breaker pattern prevents cascades (if plugin fails → core continues)
- Structured logging with correlation IDs (not hierarchical, just structured)
- Per-tenant plugin registries (different tenants → different features)

**Integration points:**
- Plugin lifecycle hooks (on_load, on_config_change, on_disable, on_unload)
- Plugin health_check() protocol method (returns HealthStatus)
- Audit trail integration (log all plugin events)

**Outcome:** Every component isolated. Failures contained. Debugging possible via logs.

---

### Stage 2: Health Monitoring (After Stage 1 stable ~1 release)
**Goal:** Understand system health in real-time.

**What changes:**
- Extend NerveFiber to collect plugin metrics (latency, memory, error_count)
- Add per-plugin health polling (every 5-30s, configurable)
- NerveFiber dashboard shows plugin status
- Alerting on thresholds (plugin health_check failing for 3+ checks)

**Integration points:**
- PluginRegistry.health_check_all() called by NerveFiber collector
- Metrics exported to telemetry system (Prometheus-compatible)
- Each plugin has tunable health-check interval (critical vs optional)

**Outcome:** Ops can see plugin health in real-time. Alerts fire before users notice.

---

### Stage 3: Safe Self-Healing (After Stage 2 proven ~2 releases)
**Goal:** Autonomous failure recovery without manual intervention.

**What changes:**
- Self-healing orchestrator (new component) watches plugin health
- On repeated health_check failures, take action:
  - **Level 1 (Safe):** Circuit-break plugin (queue requests, fail gracefully)
  - **Level 2 (Reversible):** Soft-restart plugin (on_unload + on_load)
  - **Level 3 (Preserve):** Degrade gracefully (STT off → text-only; Router off → native only)
  - **Never:** Hard-kill, force-delete, modify data

**Per-plugin healing policies:**
```yaml
plugins:
  audit-backend:
    healing_policy: circuit_break_only  # Never restart (audit is precious)
  
  stt-provider:
    healing_policy: soft_restart        # Can recover via restart
    
  engine-router:
    healing_policy: disable_and_degrade # Disable TDE, fall back to native
```

**Integration points:**
- Healing events logged to audit trail (immutable, no PII)
- Healing metrics tracked (MTTR improvements)
- Each healing action is reversible (can re-enable plugin)
- Healing is scoped: only transient failures (timeout, OOM), not systematic (logic errors)

**Outcome:** Common transient failures auto-recover. Ops notified but not blocked.

---

### Stage 4: Intelligent Healing (After Stage 3 proven ~3-4 releases)
**Goal:** Learn which healing strategies work best. Auto-tune.

**What changes:**
- LDD measurement loop: measure MTTR per healing policy
- Collect loss signals (user satisfaction, resource efficiency, error rate)
- Adjust healing policies based on data (e.g., "soft-restart works better than circuit-break for STT")
- Predictive healing (heal before health_check fails if pattern matches)

**Integration points:**
- LDD layer provides loss signals
- Healing policy adjustment via config reload (no restart)
- A/B test different policies per tenant (if opt-in)

**Outcome:** Self-healing gets smarter with production data.

---

## Structured Logging Schema

**Not a hierarchy. Structured fields that can be filtered.**

### Event Format
```json
{
  "timestamp": "2026-08-15T10:30:45.123Z",
  "level": "ERROR",                       // ERROR | WARN | INFO | DEBUG
  "component": "audit-backend",           // Plugin class name
  "plugin_id": "audit-compliance/1.0.0",  // plugin_id + version
  "tenant_id": "_default",                // Multi-tenancy isolation
  "correlation_id": "req-abc123def456",   // Trace across plugins
  "operation": "log_event",               // Plugin method or lifecycle hook
  "duration_ms": 42,                      // Latency measurement
  "memory_mb": 128,                       // Resource usage
  "error_code": "HASH_MISMATCH",          // Structured error, no PII
  "recovered": true,                      // Did it auto-heal?
  "message": "Audit chain verification failed, circuit-breaker engaged",
  "context": {                            // Plugin-specific fields
    "event_type": "plugin.health_check",
    "health_status": "UNHEALTHY",
    "consecutive_failures": 3,
    "healing_action_taken": "circuit_break"
  }
}
```

### Multi-Level Filtering (All Same Log Stream)

**Example queries (Loki/ELK/Stackdriver):**
```
# Component-level debugging
{component="audit-backend", tenant_id="_default"}

# Feature-level observability
{component=~"user-backend|audit-backend", level=~"ERROR|WARN"}

# System-level health
{level="ERROR"} | rate [5m] > 0

# Healing history
{recovered=true} | stats count() by healing_action_taken
```

---

## NerveFiber Integration (Self-Awareness Layer)

### ADR-0177 Extension

CorvinOS Nervous System (ADR-0177) is extended to:

**1. Continuous Health Collection**
```python
class NerveFiberHealthCollector:
    async def poll_plugins(self):
        """Called every 5-30s per plugin (configurable)."""
        for plugin_id in registry.discover():
            try:
                status = plugin.health_check()  # HealthStatus
                self.emit_metric(
                    "plugin.health",
                    {"plugin_id": plugin_id, "status": status.ok}
                )
            except Exception as e:
                self.log_error(f"health_check failed for {plugin_id}", e)
```

**2. Metrics Export**
- Prometheus-compatible metrics endpoint (`/metrics`)
- Grafana dashboards per plugin
- Dashboards per tenant (isolated)

**3. Threshold Alerting**
- Alert if plugin health_check fails 3+ times in 5 min
- Alert if plugin latency > threshold
- Alert if plugin memory > limit
- Alert if healing triggered N times in 1 hour (repeated failures)

**4. Self-Awareness Loop (New)**
- NerveFiber collects metrics
- Healing Orchestrator reads metrics
- Makes autonomous decisions (Stage 3+)
- Logs decisions to audit trail
- Cycle repeats

---

## Self-Healing Orchestrator

### Healing Decision Logic (Flow Chart)

```
Plugin health_check() fails
        ↓
Wait for 3 consecutive failures (30-150s depending on interval)
        ↓
Check healing policy for this plugin
        ↓
┌─────────────────────────────────────────────────┐
│                                                 │
├──→ "circuit_break_only"    ├──→ Circuit-break  │
├──→ "soft_restart"          ├──→ on_unload()    │
│                            │    on_load()      │
├──→ "disable_and_degrade"   ├──→ Degrade mode   │
│                            │    (STT → text)   │
└─────────────────────────────────────────────────┘
        ↓
Log healing action to audit trail (immutable)
        ↓
Wait 5 min before next healing attempt
        ↓
If health_check passes: exit healing
If still failing: escalate to Level 2
```

### Healing Policies

```yaml
# audit-backend: Never heal autonomously (audit is precious)
audit-backend:
  healing_policy: circuit_break_only
  consecutive_failures_threshold: 5  # Higher tolerance
  escalation: "page_on_call"         # Call human

# stt-provider: Soft-restart is safe
stt-provider:
  healing_policy: soft_restart
  consecutive_failures_threshold: 3
  escalation: "circuit_break"        # Degrade if restart fails

# engine-router: Disable if it fails (native-only is fine)
engine-router:
  healing_policy: disable_and_degrade
  consecutive_failures_threshold: 2
  escalation: "disable_only"
```

---

## GDPR + Security Compliance

### Audit Trail Integration

Every healing action logged:
```json
{
  "event_type": "plugin.healing_action",
  "plugin_id": "stt-provider/1.0.0",
  "healing_action": "soft_restart",
  "consecutive_failures": 3,
  "reason": "health_check.timeout",
  "timestamp": "2026-08-15T10:30:45Z",
  "tenant_id": "_default",
  "hash_chain": "sha256:prev_hash^this_event_hash"
}
```

**Important:** Audit trail is immutable (hash-chained). Healing is logged but cannot be hidden.

### Privacy Safeguards

- ✅ **No PII in logs.** Use error codes (HASH_MISMATCH) not details (which user).
- ✅ **Per-tenant isolation.** Logs never leak across tenants.
- ✅ **Reversible actions.** Healing can be undone (re-enable plugin).
- ✅ **Human oversight.** Critical plugins (audit, auth) require human sign-off to heal.

---

## LDD Integration (Loss-Driven Development)

### How Stages Use LDD

**Stage 1-2:** Provide loss signals
- Latency per component
- Error rates
- Circuit-breaker activations
- Health-check failure rates

**Stage 3:** Use loss signals for decisions
- If soft-restart reduces MTTR by 50% → use it
- If circuit-break is more stable → prefer it
- If healing fails → manual review (human decision)

**Stage 4:** Auto-tune based on loss signals
- Measure MTTR before/after each healing policy
- Adjust thresholds in prod (A/B test)
- Gradually increase autonomy as confidence grows

---

## Implementation Roadmap

### Phase 1: Compartmentalization + Logging
**Duration:** 3-4 months (Phase 1 of previous ADR)
**Deliverable:** 
- 5 core plugins (Audit, Auth, Compute, Router, STT)
- Structured logging + correlation IDs
- Circuit breakers working
- 56+ tests passing

**Owner:** 3-4 engineers

### Phase 2: Health Monitoring
**Duration:** 1-2 months (after Phase 1 stable 1 release)
**Deliverable:**
- NerveFiber plugin health collector
- Prometheus metrics export
- Grafana dashboards
- Alert thresholds
- 20+ tests

**Owner:** 1-2 engineers

### Phase 3: Self-Healing (Proposal)
**Duration:** 2-3 months (after Phase 2 proven)
**Deliverable:**
- Healing Orchestrator
- Per-plugin healing policies
- Audit trail logging
- Reversible healing actions
- 30+ tests + chaos tests

**Owner:** 2-3 engineers

### Phase 4: Intelligent Healing (Proposal)
**Duration:** 2-3 months (after Phase 3 proven 3-4 releases)
**Deliverable:**
- LDD loss signal integration
- Automated policy tuning
- Predictive healing
- 20+ tests

**Owner:** 1-2 engineers

---

## Alternatives Considered

### Alternative A: "Monolithic with Monitoring"
Add observability to existing monolithic system without compartmentalization.

**Rejected because:** Doesn't solve cascade failures. Monitoring a broken system doesn't fix it.

### Alternative B: "Manual Healing Only"
Stage 1 + 2 (compartments + monitoring) but no automatic healing. Humans decide.

**Status:** Valid for Stage 1-2. Stage 3-4 can be deferred if ops prefer manual.

### Alternative C: "All-in-One Magical Self-Healing"
Implement all 4 stages simultaneously from day 1.

**Rejected because:** 
- Coupling risk too high
- Not reversible if Stage 3-4 are wrong
- No feedback loop before going autonomous
- Testing matrix explodes

---

## Success Criteria

### Stage 1 Success
- ✅ Every core feature is a plugin
- ✅ Circuit breaker prevents cascades
- ✅ Structured logs enable debugging
- ✅ 90% test coverage
- ✅ GDPR compliance (immutable audit trail)

### Stage 2 Success
- ✅ Plugin health visible in real-time
- ✅ Alerts fire 5+ min before user impact
- ✅ Ops dashboard shows all plugin status
- ✅ MTTR for known issues <30 min

### Stage 3 Success
- ✅ Common transient failures auto-recover
- ✅ Healing success rate >85% (doesn't make things worse)
- ✅ Healing MTTR <5 min (vs. 30 min manual)
- ✅ Zero unintended side-effects (no cross-tenant impact)

### Stage 4 Success
- ✅ Healing policies improve with production data
- ✅ Autonomous decisions match human decisions 95%+ of time
- ✅ MTTR improves month-over-month
- ✅ No regression in audit trail integrity

---

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|-----------|
| **Stage 1: Breaking change** | MEDIUM | Backwards-compat mode (env vars → plugins) |
| **Stage 2: Health-check overhead** | MEDIUM | Configurable intervals per plugin + sampling |
| **Stage 3: Healing fails** | HIGH | Only reversible actions; circuit-break only for critical |
| **Stage 3: Healing cascades** | HIGH | Per-plugin healing policies; max N heals/hour |
| **Stage 4: ML makes wrong decisions** | MEDIUM | Always fallback to manual; humans review healings |
| **All: NerveFiber is sick** | MEDIUM | Monitoring is optional; core works without it |

---

## Open Questions (For Team Discussion)

1. **Stage 2 interval:** Health-check every 5s (reactive) or 30s (lazy)? Trade-off between MTTR and overhead.
2. **Stage 3 scope:** Should auth plugin ever auto-heal? Or always require manual approval?
3. **Stage 4 eligibility:** Which plugins get to be "intelligent"? All or just a few?
4. **Healing reversibility:** Can we reverse a plugin disable mid-flight? What if users are connected?

---

## Decision

**Approved for Stage 1-2. Stage 3-4 to be re-proposed after Stage 2 is stable (1+ release in production).**

This decision:
- ✅ Mitigates coupling risk (staged approach)
- ✅ Integrates with LDD (loss signals feed later stages)
- ✅ Integrates with NerveFiber (extends existing system)
- ✅ Preserves GDPR compliance (audit trail immutable)
- ✅ Preserves reversibility (each stage can be disabled)

---

## References

- ADR-0030: Plugin System (foundational)
- ADR-0177: Nervous System (extends)
- PHASE_1_IMPLEMENTATION_PLAN.md (execution roadmap)
- ARCHITECTURE_REFACTOR_PROPOSAL.md (strategic vision)

---

**Next step:** Implement Phase 1. Propose Stage 2 design review after Phase 1 is stable in production.
