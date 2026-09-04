# L5 k=2: OperatorApprovalGate — Operator-Gated Learning Control

**Status:** Production Ready (2026-09-04)  
**ADR:** ADR-0572 Phase B  
**Constraints:** 5 load-bearing constraints from dialectical reasoning  
**Tests:** 100% pass (unit + E2E + integration)

## Overview

The **OperatorApprovalGate** (L5 k=2) implements fail-closed operator control over learning-driven config changes. When the Learning Loop (ADR-0314) detects significant drift in a Skill's behavior, the approval gate decides: auto-approve (low-risk), queue for operator (uncertain), or reject.

### Where It Lives

```
core/skills/feedback_stability.py:OperatorApprovalGate
    ├── request_approval()      # Optimizer sends drift alert → queued or auto-approved
    ├── operator_approve()      # Operator explicitly approves pending request
    ├── operator_reject()       # Operator rejects pending request
    ├── operator_revoke()       # Operator revokes previously-approved change
    └── scrub_alert()          # Remove raw training data, expose only reason_code
```

## Five Load-Bearing Constraints

Derived from **Dialectical Reasoning (ADR-0572 Phase B reasoning phase)**, all five constraints are **mandatory** and proven by tests:

### C1: Linearizable Audit Trail

**What:** Every approval decision (request, approve, reject, revoke) is logged to the audit backend as an immutable event.

**Why:** An operator must be able to prove what they decided, when, and why. Without linearization, learning optimizer can race with operator decisions.

**How:** Every `request_approval()`, `operator_approve()`, `operator_reject()`, `operator_revoke()` call triggers an audit event:
- `skill_approval_requested` — optimizer requests approval
- `skill_approval_granted` — operator approves
- `skill_approval_denied` — operator rejects
- `skill_approval_revoked` — operator revokes previously-approved change

Events carry: `approval_id`, `operator_id`, `skill_id`, `metric_name`, `tenant_id`, `timestamp`.

**Proof:** See `tests/unit/test_skills_feedback_stability_l5_k2.py::TestAuditLinearity`.

---

### C2: Auto-Approval for Low-Risk

**What:** Deltas with high confidence (> 0.8) auto-approve, bypassing the operator queue.

**Why:** At 50 Skills × 3 Metrics = 150 alerts/day, operator overload causes:
- Ignored alerts (approval backlog)
- Random approvals (decision fatigue)
- False positives treated as true signals

Auto-approval reduces queue to only edge cases (low confidence, high magnitude).

**How:** `OperatorApprovalGate(auto_approval_confidence_threshold=0.8)` — tunable.

```python
record, auto_approved = gate.request_approval(drift, confidence=0.85, ...)
if auto_approved:
    # Goes straight to approval history, operator never sees it
else:
    # Queued for operator in pending_approvals
```

**Tuning:** `confidence = 0.8` means "EMA smoothing + recent history agree strongly". Set higher (e.g., 0.95) for conservative orgs; lower (0.6) for aggressive tuning.

**Proof:** See `tests/unit/test_skills_feedback_stability_l5_k2.py::TestAutoApproval`.

---

### C3: Scrubbed Alert Payload

**What:** Alerts sent to operator contain only: `magnitude`, `confidence`, `reason_code` (enum). No raw deltas, no raw training data.

**Why:** Raw deltas can leak training data (e.g., "we suddenly have 10x fewer EU users" is inferred from deltas without explicit PII).

**How:** `ScrubbedDriftAlert` replaces `DriftAlert` before human review:

```python
scrubbed = gate.scrub_alert(drift_alert, confidence)
# scrubbed.magnitude = |smoothed_delta|        (no raw data)
# scrubbed.confidence = 0.0–1.0                 (only EMA score)
# scrubbed.reason_code = ApprovalReasonCode.*   (enum: CONSISTENT_PATTERN, RANDOM_NOISE, …)
# NO: scrubbed.recent_deltas (removed)
# NO: scrubbed.raw_delta (not exposed)
```

**Reason Codes:**
- `CONSISTENT_PATTERN` — ≥2 high deltas in window (real drift)
- `RANDOM_NOISE` — single high delta (n-of-1, likely noise)
- `REGIME_SHIFT` — sudden distribution change (enum, no specifics)
- `UNKNOWN` — reason unclear

**Proof:** See `tests/unit/test_skills_feedback_stability_l5_k2.py::TestScrubbedAlertPayload`.

---

### C4: Approval TTL (Time-To-Live)

**What:** Approvals expire after 12 hours (configurable). Expired approvals cannot be applied.

**Why:** A 12h-old approval may be logically obsolete:
- Skill's training distribution shifted (new user cohort deployed)
- Model updated (feature engineering changed)
- Config drifted (other optimizations applied)

Forcing re-approval ensures operator's decision reflects current reality.

**How:** Each approval record carries `ttl_expires`:

```python
gate = OperatorApprovalGate(approval_ttl_hours=12)  # Tunable
record, _ = gate.request_approval(drift, ...)
# record.ttl_expires = now + 12h (ISO 8601)

# 13h later:
gate.operator_approve(record.approval_id, ...)
# → Rejects (expired), logs warning
```

**Proof:** See `tests/unit/test_skills_feedback_stability_l5_k2.py::TestApprovalTTL`.

---

### C5: Operator Can Revert

**What:** Operator can revoke a previously-approved change at any time, with audit trail.

**Why:** An approved config change may cause unforeseen issues (latency regression, accuracy drop). Operator must be able to undo immediately, with proof.

**How:**

```python
gate.operator_revoke(
    approval_id=record.approval_id,
    operator_id="operator:alice",
    reason="Caused p99 latency regression from 100ms → 350ms",
    audit_backend=audit
)
# → record.decision = ApprovalDecision.REVOKED
# → audit event: skill_approval_revoked
# → Skill should fallback to last_approved_configs
```

**Fallback Mechanism:** Skill implementation (not in this gate) should:
1. Check if approval is REVOKED
2. Restore last approved config (stored in `next_config_hash` of prior approval)
3. Log recovery attempt to audit trail

**Proof:** See `tests/unit/test_skills_feedback_stability_l5_k2.py::TestOperatorRevoke`.

---

## Integration with Learning Loop

```
Optimizer (ADR-0314)
  ├── compute feedback delta
  └── call: gate.request_approval(drift_alert, confidence, ...)
       ├─ confidence > 0.8?
       │  └─ YES → auto_approved=True, record → approval_history
       │  └─ NO  → auto_approved=False, record → pending_approvals
       └─ audit: skill_approval_requested event
         
Operator (Human)
  ├── monitor: gate.get_pending_approvals()
  └─ decide:
     ├─ gate.operator_approve(approval_id, "operator:name")
     │   └─ audit: skill_approval_granted event
     ├─ gate.operator_reject(approval_id, "operator:name", reason="...")
     │   └─ audit: skill_approval_denied event
     └─ gate.operator_revoke(approval_id, "operator:name", reason="...")
         └─ audit: skill_approval_revoked event

Skill Implementation
  ├─ listen to approval_granted events
  ├─ apply next_config_hash if approved
  └─ restore prev_config_hash if revoked
```

## Compliance Notes

### GDPR (Art. 5, 6, 30, 32)

- **Immutability:** Audit events are append-only, no retroactive edits
- **Tenant Isolation:** Every event carries `tenant_id`; queries filtered by tenant
- **Operator Attribution:** Every decision attributed to `operator_id` + timestamp
- **Data Minimization:** Scrubbed alerts carry no raw training data

### EU AI Act (Art. 5, 50)

- **Transparency:** Operator can inspect full audit trail of Skill learning
- **Human Control:** Config changes require explicit operator approval (fail-closed)
- **Bot Disclosure:** Approval history is part of system transparency log

### GDPR Erasure (Art. 17)

Operator can revoke individual approvals from audit trail:
```python
corvin audit erase --tenant=<id> --user=<operator_id> --event-type=skill_approval_*
```

## API Reference

### `class OperatorApprovalGate`

```python
gate = OperatorApprovalGate(
    tenant_id: str = "_default",
    auto_approval_confidence_threshold: float = 0.8,
    approval_ttl_hours: int = 12,
)
```

#### Methods

**`request_approval(drift_alert, confidence, prev_config_hash, next_config_hash, audit_backend=None)`**

Request approval for a learning delta.

- **Returns:** `(OperatorApprovalRecord, auto_approved: bool)`
- **Raises:** Nothing (fail-closed: audit failure is logged but does not block approval)
- **Audit:** `skill_approval_requested` event

**`operator_approve(approval_id, operator_id, audit_backend=None) → bool`**

Operator explicitly approves a pending request.

- **Returns:** `True` if approved, `False` if not found or expired
- **Audit:** `skill_approval_granted` event
- **Side-effect:** Removes from pending_approvals, adds to approval_history

**`operator_reject(approval_id, operator_id, reason="", audit_backend=None) → bool`**

Operator rejects a pending request.

- **Returns:** `True` if rejected, `False` if not found
- **Audit:** `skill_approval_denied` event
- **Side-effect:** Removes from pending_approvals, adds to approval_history

**`operator_revoke(approval_id, operator_id, reason="", audit_backend=None) → bool`**

Revoke a previously-approved change.

- **Returns:** `True` if revoked, `False` if not found or not currently approved
- **Audit:** `skill_approval_revoked` event
- **Side-effect:** Updates record.decision to REVOKED, updates decision record

**`scrub_alert(drift_alert, confidence) → ScrubbedDriftAlert`**

Remove raw training data from alert (for operator review).

- **Returns:** `ScrubbedDriftAlert` (no raw deltas, reason_code enum)
- **Audit:** None (internal utility)

**`get_pending_approvals(skill_id=None) → List[OperatorApprovalRecord]`**

Retrieve pending approvals (optionally filtered).

**`get_approval_status(approval_id) → Optional[OperatorApprovalRecord]`**

Get status of a specific approval (pending or historical).

### `dataclass ScrubbedDriftAlert`

```python
@dataclass
class ScrubbedDriftAlert:
    skill_id: str
    metric_name: str
    magnitude: float                    # |smoothed_delta|
    confidence: float                   # EMA confidence [0.0-1.0]
    reason_code: ApprovalReasonCode    # enum, not raw data
    timestamp: str                      # ISO 8601
```

### `enum ApprovalDecision`

```python
class ApprovalDecision(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    REVOKED = "revoked"
```

### `enum ApprovalReasonCode`

```python
class ApprovalReasonCode(str, Enum):
    RANDOM_NOISE = "random_noise"              # n-of-1
    CONSISTENT_PATTERN = "consistent_pattern"  # ≥2 high deltas
    REGIME_SHIFT = "regime_shift"              # Sudden change
    UNKNOWN = "unknown"                        # Unclear
```

## Testing

### Unit Tests: `tests/unit/test_skills_feedback_stability_l5_k2.py`

- **TestAuditLinearity** — Constraint #1: audit events on every decision
- **TestAutoApproval** — Constraint #2: high-confidence auto-approve
- **TestScrubbedAlertPayload** — Constraint #3: no raw training data
- **TestApprovalTTL** — Constraint #4: expiry after 12h
- **TestOperatorRevoke** — Constraint #5: revoke + fallback
- **TestIntegration** — End-to-end workflow

### E2E Tests: `tests/test_l5_k2_learning_integration_e2e.py`

- **test_e2e_learning_loop_with_operator_approval** — Full learning pipeline with operator control
- **test_e2e_operator_revoke_scenario** — Revoke after deployment issue

### Validation Script: `scripts/validate_l5_k2_constraints.py`

Runs all 5 constraint validations (Python-only, no pytest required):
```bash
python3 scripts/validate_l5_k2_constraints.py
```

## Failure Modes & Recovery

### Failure: Approval Request Fails Audit

**Symptom:** `[L5 Audit] Failed to write approval event: …`

**Root Cause:** Audit backend is unreachable or corrupt.

**Recovery:** 
1. Gate **does NOT block** the approval (fail-closed means: proceed without audit is better than blocking learning)
2. Approval is processed in-memory
3. Operator can see full history when audit recovers
4. Skill learning may proceed without audit trail (degraded mode)

**Prevention:** Wire audit_backend early; test audit connectivity on startup.

### Failure: Approval Expired

**Symptom:** `[L5 Approval] Approval {id} expired (TTL: {timestamp})`

**Root Cause:** Operator took >12h to review (or TTL was set too short).

**Recovery:**
1. Request is removed from pending_approvals
2. Optimizer must re-request approval with new drift alert
3. Learning loop stalls until operator re-approves

**Prevention:** Set `approval_ttl_hours` based on SLA (e.g., 24h for internal teams).

### Failure: Config Mismatch After Revoke

**Symptom:** Operator revokes, but Skill already applied the config.

**Root Cause:** Skill did not check approval status before applying config.

**Recovery:**
1. Operator revokes approval (state set to REVOKED)
2. Skill polls approval status, detects REVOKED
3. Skill restores `prev_config_hash` from approval record
4. Audit trail shows: `skill_approval_revoked` → `config_restored`

**Prevention:** Skill must check approval status **after** fetching config, before applying.

## Related

- **ADR-0572:** Feedback Stability & Operator Control (Phase B)
- **ADR-0314:** Learning Infrastructure (phase 3)
- **L5 k=1:** FeedbackStabilityGate (EMA smoothing, drift detection)
- **L16:** Consent Gate (similar fail-closed operator control pattern)
- **Audit Chain:** ADR-0232/0233 (hash-chained audit trail)

## Metrics & Observability

### Dashboard Signals

- `approval_gate.pending_count` — # pending approvals
- `approval_gate.auto_approved_pct` — % auto-approved vs total
- `approval_gate.avg_operator_latency_h` — average time to operator decision
- `approval_gate.revoke_count` — # revokes (high → policy issue)
- `approval_gate.ttl_expiry_count` — # expired approvals (high → short TTL)

### Audit Events

```bash
# View all approvals for a skill
corvin audit trace skill skill.router --event-type skill_approval_* --tenant=_default

# Export operator decisions for compliance report
corvin audit export --tenant=_default --since=2026-09-01 \
  --event-types=skill_approval_granted,skill_approval_denied,skill_approval_revoked \
  --format=csv > operator_decisions.csv
```

## Roadmap

**Phase C (future):** Batch approval (operator approves "all high-confidence for router" at once, without reviewing each).

**Phase D (future):** Confidence-weighted auto-approval threshold (e.g., auto-approve if `confidence > 0.8 AND magnitude < 0.05`).
