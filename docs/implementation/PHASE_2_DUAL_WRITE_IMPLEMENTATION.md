# Phase 2 Dual-Write Implementation Guide

**Status:** Code present, NOT ACTIVATABLE — every Phase 2 mode is refused (see "Current status" below)  
**Version:** 1.0.0  
**Last Updated:** 2026-09-27  
**ADR Reference:** ADR-0532 (OS-Skills Architecture)  
**Related:** ADR-0722 (Decision Attribution), ADR-0314 (Learning Infrastructure)

---

## Current status (2026-09-27, adversarial review) — read this first

**Phase 2 cannot be switched on.** `CORVIN_ACP_PHASE=phase2_dual_write` and
`CORVIN_ACP_PHASE=phase2_real` are REFUSED by
`corvin_operator/bridges/shared/delegation_policy.py::_get_phase_mode`: the
request is logged as a warning, audited once per process and tenant as
`l5_routing_phase_refused` (`requested_phase`, `effective_phase=phase1_shadow`,
`reason_code=rollback_guard_not_wired`), and L5 routing stays in **Phase 1
shadow mode** — the bundled engine is served, the Skill's answer is only
audited (ADR-0613).

Why: Phase 2 serves the Skill's engine, and ADR-0532 admits that only behind a
working auto-rollback. The rollback detector reads a correctness window that
only `dual_write.record_routing_outcome` feeds, and that function has no
production caller — it needs a per-request `ground_truth` ("which engine WOULD
have been right") that no outcome sink observes. With no samples the detector
never fires, so Phase 2 would change served routing with no brake. The guard
flag `_PHASE2_ROLLBACK_GUARD_WIRED` is `False`; `dual_write.py`,
`rollback_detector.py` and `correctness_tracker.py` carry the
"NOT WIRED: no production caller" marker.

Also not present: there is no `spec.acp` block read from `tenant.corvin.yaml`
and no `/v1/console/l5-routing/metrics` route. The configuration, rollout and
activation sections below describe the DESIGN; none of their commands changes
routing today. Setting the env var to a Phase 2 value only produces the refusal
record.

## Executive Summary

This document describes the Phase 2a design of the os.delegation_router L5 routing upgrade (code present, activation refused — see above). Phase 2 transitions from Phase 1's shadow mode (advisory-only) to dual-write mode, where the Skill-driven routing decision is used for real routing while the bundled decision is tracked for correctness monitoring and auto-rollback.

**Implemented components (none of them serves routing today):**

- ✅ Dual-write decision executor (`resolve_worker_engine_dual_write()`)
- ✅ Confidence threshold gating (default 0.75, learned override)
- ⚠️ Auto-rollback trigger (>2% correctness drop) — detector exists, but nothing feeds its correctness window, so it can never fire
- ✅ Agreement rate tracking (Skill vs. bundled decision comparison)
- ✅ Audit trail integration (ADR-0722 decision attribution)
- ✅ Learning feedback integration (ADR-0314 optimizer signals)
- ✅ E2E test suite (15+ tests covering all scenarios)
- ⚠️ `CORVIN_ACP_PHASE` env var — only `phase1_shadow` takes effect; Phase 2 values are refused and audited (`l5_routing_phase_refused`)

---

## Architecture Overview

### Decision Flow (Phase 2a Dual-Write)

```
Request arrives at resolve_worker_engine()
│
├─ Compute bundled engine (pure rule)
│
├─ Check rollback state
│  ├─ If rollback active → return bundled (fail-closed)
│  └─ Else → continue to Phase 2a logic
│
├─ Fetch Skill decision (os.delegation_router)
│
├─ Load confidence threshold (default 0.75, learned override)
│
├─ Compare skill_confidence vs. threshold
│  ├─ skill_confidence >= threshold → use Skill decision (real routing)
│  └─ skill_confidence < threshold → use bundled (fail-closed)
│
├─ Record both decisions (dual-write audit)
│
├─ Emit decision metrics (agreement rate, confidence distribution)
│
└─ Return routing engine
```

### Key Components

#### 1. `resolve_worker_engine_dual_write()` (core/skills/os_skills/monitoring/dual_write.py)

Main entry point for Phase 2a routing. Coordinates:
- Skill decision fetching
- Confidence threshold application
- Dual-write audit logging
- Metric emission for learning feedback

**Inputs:**
- `request_id`: Unique request identifier
- `bundled_engine`: Fallback routing decision (from pure rule)
- `complexity`: Task complexity (1-10) for Skill
- `force_delegate`: User explicitly requested delegation
- `is_big_data`: Task is big-data shaped
- `task_type`: Task type ('chat', 'big_data', 'delegate')
- `tenant_id`: Tenant for config + audit isolation

**Outputs:**
- Returns routing engine name ('native', 'acs', 'tde')
- Emits audit events (dual-write decision, metrics, rollback)

#### 2. `_fetch_skill_decision()` (Helper)

Executes os.delegation_router Skill via skill_registry.

**Behavior:**
- Timeout: 500ms (fail-fast on Skill timeout)
- On error/timeout: Returns fallback decision with confidence=0.0
- Input validation: Ensures 'decision' and 'confidence' fields present
- Audit-first: LoM binding for every execution

**Returns:**
```python
{
    "decision": "native" | "acs" | "tde",
    "confidence": 0.0–1.0,
    "reasoning": "explanation of decision"
}
```

#### 3. `_load_confidence_threshold()` (Helper)

Loads confidence threshold from learned config (ADR-0314) or returns default.

**Default:** 0.75 (Skill needs ≥75% confidence to be trusted)  
**Learned Override:** Loaded from skill_adapter if available  
**Isolation:** Per-tenant configuration

#### 4. Correctness Tracker (core/skills/os_skills/monitoring/correctness_tracker.py)

Tracks Skill vs. bundled decision agreement in rolling window.

**Window:** 1000 decisions (ADR-0532 synthesis)  
**Bootstrap:** 100 samples before triggering rollback  
**Metric:** P(Skill correct | last 1000 decisions)

#### 5. Rollback Detector (core/skills/os_skills/monitoring/rollback_detector.py)

Monitors correctness metrics and triggers auto-fallback.

**Trigger:** Correctness drops >2% below bundled baseline  
**State:** Non-reversible per deployment cycle (requires restart to re-enable)  
**Audit:** Every rollback event logged with metrics snapshot

---

## Decision Trees

### Confidence Threshold Tree

```
Load learned threshold (or default 0.75)
│
├─ IF skill_confidence >= threshold
│  └─ USE skill_decision (real routing)
│
├─ ELSE (skill_confidence < threshold)
│  └─ USE bundled_engine (fail-closed)
```

**Rationale:**
- Default 0.75 balances: trust Skill when moderately confident, defer to bundled when unsure
- Learned threshold: ADR-0314 feedback loop can adjust based on actual outcomes
- Fail-closed: Never escalate beyond bundled without sufficient confidence

### Agreement Tracking

```
On every request:
├─ skill_engine: what Skill decided
├─ bundled_engine: what bundled rule decided
├─ used_engine: what we actually routed (Skill or bundled)
├─ agreement: 1.0 if skill == bundled, else 0.0
│
→ Aggregate over rolling window (1000 decisions)
→ If agreement < (shadow_baseline - 2%), trigger rollback
```

### Rollback Recovery Tree

```
IF correctness_drops > 2.0% THEN
  ├─ Set state = ROLLBACK_TRIGGERED
  ├─ Emit audit event (l5_rollback_triggered)
  ├─ Log metrics snapshot
  └─ All future routing uses bundled_engine (fail-closed)

RECOVERY ONLY VIA:
  └─ Restart + manual operator decision
     └─ Re-enable Phase 2 only after verification
```

---

## Audit Trail (ADR-0722 Decision Attribution)

### Events Emitted

#### `l5_routing_dual_write`
Logged on every Phase 2a routing decision.

```json
{
  "event_type": "l5_routing_dual_write",
  "request_id": "req_1234567890",
  "decision_source": "skill" | "bundled",
  "used_engine": "native" | "acs" | "tde",
  "skill_engine": "...",
  "bundled_engine": "...",
  "skill_confidence": 0.85,
  "decision_reason": "Skill confidence 0.85 >= threshold 0.75",
  "task_type": "chat" | "big_data" | "delegate",
  "agreement": true | false,
  "tenant_id": "_default",
  "timestamp": "2026-09-27T15:46:18.935Z"
}
```

#### `l5_routing_metrics`
Emitted for observability + learning feedback integration.

```json
{
  "event_type": "l5_routing_metrics",
  "request_id": "req_1234567890",
  "agreement": 1.0 | 0.0,
  "threshold_met": 1.0 | 0.0,
  "skill_confidence": 0.85,
  "threshold": 0.75,
  "task_type": "chat",
  "used_engine": "acs",
  "tenant_id": "_default"
}
```

#### `l5_rollback_triggered`
Emitted when auto-rollback is triggered.

```json
{
  "event_type": "l5_rollback_triggered",
  "reason": "correctness dropped from 0.95 to 0.92 (3% > threshold 2%)",
  "metrics": {
    "window_size": 1000,
    "correct_count": 920,
    "total_count": 1000,
    "correctness": 0.92,
    "shadow_correctness": 0.95,
    "skill_confidence_mean": 0.81
  },
  "timestamp": 1695844378.935
}
```

### Compliance

- **GDPR Art. 30** (Processing Record): All decisions recorded in immutable audit chain
- **GDPR Art. 32** (Security): Hash-chained audit trail, fail-closed on corruption
- **EU AI Act Art. 50** (Transparency): LoM binding + lom_hash in every decision
- **ADR-0232/0233** (Boot Tripwire): Audit chain verified before Phase 2 execution
- **ADR-0722** (Decision Attribution): Every decision attributed to decision_source (Skill or bundled)

---

## Learning Loop Integration (ADR-0314)

### Feedback Signals

Phase 2a emits these signals for the learning loop optimizer:

1. **Decision Metrics** (every request)
   - `agreement`: Does Skill match bundled? (1.0/0.0)
   - `threshold_met`: Does Skill meet confidence threshold? (1.0/0.0)
   - `skill_confidence`: Actual Skill confidence (0.0–1.0)

2. **Outcome Signal** (post-execution)
   - `success`: Did the routed request succeed?
   - `ground_truth`: Which engine was actually correct?
   - `latency_ms`: Request latency (for cost/latency tradeoffs)

3. **Rollback Event** (on trigger)
   - Correctness drop metrics
   - Rollback timestamp
   - Restored state (bundled routing)

### Optimizer Integration

The ADR-0314 learning optimizer reads these signals to:

1. **Compute confidence delta**
   - If Skill was right more often → increase confidence threshold
   - If Skill was wrong more often → decrease confidence threshold

2. **Update learned config**
   - Write new `confidence_threshold` to skill_adapter config
   - Per-tenant, versioned updates

3. **Feedback loop closure**
   - Operator confirms/denies Skill decisions → confidence updates
   - System learns to improve decision quality over time

---

## Feature Flags

### CORVIN_ACP_PHASE Environment Variable

Selects the L5 routing phase. Only Phase 1 can be in effect today.

| Value | Behavior today |
|-------|----------------|
| `phase1_shadow` (default) | Advisory mode; bundled engine served; Skill decision audited |
| `phase2_dual_write` | **Refused** — logged + audited as `l5_routing_phase_refused`; stays `phase1_shadow` |
| `phase2_real` | **Refused** — logged + audited as `l5_routing_phase_refused`; stays `phase1_shadow` |
| anything else | Treated as `phase1_shadow` |

### Activation

There is nothing to activate. Do not set `CORVIN_ACP_PHASE` to a Phase 2
value in production: it does not change routing, it only writes the refusal
record. Phase 2 becomes activatable only when a real correctness feed for the
rollback detector exists and `_PHASE2_ROLLBACK_GUARD_WIRED` is flipped in the
same change (with an ADR).

### Configuration via tenant.corvin.yaml (design only — not read by any code)

```yaml
spec:
  acp:
    phase: phase2_dual_write
    confidence_threshold: 0.75  # Override default
    rollback_threshold_percent: 2.0  # % correctness drop to trigger rollback
    window_size: 1000  # Rolling window size for agreement tracking
```

---

## Deployment & Rollout Strategy

### Phase 2a Rollout (design only — blocked, see "Current status")

**Stage 1: Internal Testing (1–2 weeks)**
- Enable on staging + canary instances
- Monitor: agreement rate, confidence distribution, rollback events
- Verify: audit trail completeness, learning feedback flow

**Stage 2: Canary (1% traffic)**
- Gradually enable CORVIN_ACP_PHASE=phase2_dual_write
- Watch: correctness metrics, latency changes, rollback frequency
- Success criterion: >90% agreement rate, no rollbacks

**Stage 3: Progressive Rollout**
- 1% → 10% → 50% → 100% over 2–4 weeks
- Monitor: agreement rate trending, latency SLA compliance
- Rollback condition: Any single correctness drop >2% or latency increase >10%

**Stage 4: Default (GA)**
- Set CORVIN_ACP_PHASE=phase2_dual_write as default
- Maintain Phase 1 shadow mode for rollback/safety

### Monitoring Dashboard (not built — no such route exists)

Planned console `/v1/console/l5-routing/metrics`:

```json
{
  "phase": "phase2_dual_write",
  "is_rolled_back": false,
  "window": {
    "size": 1000,
    "samples": 523
  },
  "correctness": {
    "skill_correctness": 0.945,
    "bundled_correctness": 0.928,
    "agreement_rate": 0.912
  },
  "confidence": {
    "mean": 0.82,
    "median": 0.85,
    "p50": 0.85,
    "p95": 0.97
  },
  "routing_distribution": {
    "native": 0.62,
    "acs": 0.35,
    "tde": 0.03
  },
  "rollback_events": []
}
```

---

## Testing Strategy

### Unit Tests

- Confidence threshold logic (`test_skill_confidence_above/below_threshold`)
- Decision metrics calculation (`test_agreement_rate_tracking`)
- Fallback behavior (`test_skill_timeout_falls_back_to_bundled`)

### Integration Tests

- Dual-write audit emission (`test_dual_write_audit_events_emitted`)
- Rollback state machine (`test_rollback_active_uses_bundled_only`)
- Learned config loading (`test_confidence_threshold_default`)

### E2E Tests

- Full Phase 2a flow with real Skill execution
- End-to-end audit trail verification
- Rollback trigger + recovery
- Multi-tenant isolation

### Adversarial Tests

- Skill timeout under load (500ms budget)
- Correctness degradation cascade (100+ failures)
- Rollback recovery from failure state
- Agreement rate with hostile bundled rule

---

## Troubleshooting

### Symptom: High Rollback Frequency

**Cause:** Skill producing wrong decisions frequently  
**Check:**
1. Are decision metrics showing low agreement rate?
2. Is skill_confidence consistently below threshold?

**Recovery:**
1. Set CORVIN_ACP_PHASE=phase1_shadow (revert to advisory)
2. Debug Skill decision logic (complexity input, heuristic rules)
3. Verify learned config is being loaded correctly

### Symptom: Latency Increase After Phase 2 Activation

**Cause:** Skill execution timeout budget (500ms)  
**Check:**
1. Is _fetch_skill_decision timing out?
2. Is skill_registry booted correctly?

**Recovery:**
1. Increase timeout budget (500ms → 1s) if acceptable latency
2. Verify Skill execution path (no import failures)
3. Check skill_registry initialization logs

### Symptom: Audit Events Not Appearing

**Cause:** audit_logger import failure  
**Check:**
1. Is core.security.audit_logger available?
2. Are audit events configured?

**Recovery:**
1. Verify audit chain is writable (permissions, disk space)
2. Check audit configuration in tenant.corvin.yaml
3. Enable debug logging for dual_write module

---

## Migration from Phase 1 to Phase 2a (blocked — every Phase 2 mode is refused today)

### Pre-Activation Checklist

- [ ] Verify Phase 1 shadow mode has been running ≥7 days
- [ ] Confirm agreement rate baseline (Skill vs. bundled)
- [ ] Check audit chain is healthy + writable
- [ ] Verify learning loop is processing feedback signals
- [ ] Enable monitoring dashboard (console routes)
- [ ] Set up alerts (rollback triggers, correctness drops)

### Activation Steps

1. **Stage 1: Verify Phase 1 baseline**
   ```bash
   # Monitor for 7+ days
   CORVIN_ACP_PHASE=phase1_shadow
   
   # Check: agreement rate, confidence distribution, audit completeness
   curl http://localhost:8765/v1/console/l5-routing/metrics
   ```

2. **Stage 2: Enable Phase 2 on canary**
   ```bash
   # On 1% of instances
   CORVIN_ACP_PHASE=phase2_dual_write
   
   # Monitor correctness metrics, latency SLA, rollback events
   ```

3. **Stage 3: Progressive rollout**
   ```bash
   # Gradually increase from 1% to 100%
   # Rollback if: correctness_drop > 2% OR latency_increase > 10%
   ```

### Rollback (If Needed)

```bash
# Immediate: Revert to Phase 1 shadow mode
CORVIN_ACP_PHASE=phase1_shadow

# Restart all instances
systemctl restart corvin-service

# Audit trail preserved; no data loss
```

---

## Next Steps (Phase 2b)

### Phase 2b: Skill-Primary Routing

**Status:** Planned (not yet implemented)

In Phase 2b, the Skill decision becomes primary (no fallback to bundled):
- Remove confidence threshold gating
- Require rollback recovery to be built-in (no restart needed)
- Enable faster A/B testing + experimentation

**Prerequisites:**
- Phase 2a stable for 2+ weeks in production
- Agreement rate >95%
- Zero unplanned rollbacks
- Learning loop confidence trending upward

### Phase 3: Multi-Skill Composition

**Status:** Future (ADR-0535)

Compose multiple Skills (routing + context + workflow):
- Skill dependencies (DAG validation)
- Skill composition timeout budgets
- Cross-Skill feedback loops

---

## References

- **ADR-0532:** OS-Skills Architecture
- **ADR-0722:** Decision Attribution + Loss Signals
- **ADR-0314:** Learning Infrastructure
- **ADR-0232/0233:** Audit Chain + Boot Tripwire
- **CLAUDE.md § Skills 2.0:** Operator guide

---

## Appendix: Configuration Examples

### Minimal Phase 2 Config (design only — `spec.acp` is not read)

```yaml
# tenant.corvin.yaml
spec:
  acp:
    phase: phase2_dual_write
    confidence_threshold: 0.75
```

### Production-Grade Phase 2 Config (design only — `spec.acp` is not read)

```yaml
# tenant.corvin.yaml
spec:
  acp:
    phase: phase2_dual_write
    confidence_threshold: 0.75
    rollback_threshold_percent: 2.0
    window_size: 1000
    bootstrap_samples: 100
    metrics_persist: true
    metrics_dir: /home/shumway/.corvin/metrics/

alerts:
  - event: l5_rollback_triggered
    action: notify_ops
    severity: CRITICAL
  - event: l5_routing_metrics
    condition: correctness_drop > 3.0
    action: log_warning
    severity: WARNING

monitoring:
  dashboard_enabled: true
  dashboard_path: /v1/console/l5-routing/metrics
  export_prometheus: true
  export_interval: 60s
```

---

**Document Version:** 1.0.0  
**Last Updated:** 2026-09-27  
**Status:** Code present, activation refused (Phase 1 shadow is the only effective mode)  
**Next Review:** 2026-10-11 (2 weeks post-activation)
