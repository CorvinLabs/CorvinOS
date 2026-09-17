# Track E: Learning→Skill Integration (ADR-0675 + ADR-0676 Amendment)

**Status:** IMPLEMENTED (Gates 1-5 Complete)  
**Date:** 2026-09-18  
**Related:** ADR-0675 (Skill Forge v2.0), ADR-0676 (Learning Daemon)  
**Implementation:** Complete feedback→config→execution loop wired  
**Tests:** 50+ integration tests, 18 adversarial tests

---

## Overview

Track E integrates Learning Loop infrastructure (Track B) into Skill Forge (Track A). When users provide feedback on a skill, the system:

1. **Collects feedback** (1-5 rating, PII-scrubbed)
2. **Batches feedback** (threshold: 10 feedback OR 1h timeout)
3. **Computes config delta** via gradient descent
4. **Applies delta to SkillInstance** (clamped ±10%, fail-closed)
5. **Reloads skill** with improved config
6. **Audits every change** (GDPR Art. 30, 32)
7. **Measures improvement** (loss reduction, convergence detection)

**Result:** Self-optimizing skills that improve via real-world feedback.

---

## Architecture

### Three Core Components

```
┌─────────────────────────────────────────────────────────────┐
│ Track E: Learning→Skill Integration                          │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  SkillInstance (core/skills/skill_instance.py)              │
│  ├─ Load config on init (from config store)                 │
│  ├─ Execute skill with current config                       │
│  ├─ Capture execution metrics (latency, success, errors)    │
│  ├─ Receive feedback (1-5, PII-scrubbed)                    │
│  └─ Apply config updates (validate, clamp, persist, audit)  │
│                                                               │
│  SkillConfigTuner (core/skills/skill_config_tuner.py)       │
│  ├─ Convert feedback → FeedbackSignal (0-1 confidence)      │
│  ├─ Gradient descent: compute config delta                  │
│  ├─ Clamp changes to ±10% (fail-closed safety)             │
│  ├─ Estimate loss improvement (before → after)             │
│  └─ Convergence detection (slope → plateau)                │
│                                                               │
│  FeedbackCollector (core/learning/feedback_collector.py)    │
│  ├─ Validate feedback structure & ranges                    │
│  ├─ Scrub PII (email, phone, credit card patterns)         │
│  ├─ Emit FeedbackReceivedEvent (audit-logged)             │
│  └─ Tenant-scoped storage (no cross-tenant leakage)        │
│                                                               │
└─────────────────────────────────────────────────────────────┘
```

### Data Flow

```
User Feedback (1-5)
       ↓
[Feedback Validation]
       ↓ (scrub PII, validate range)
[FeedbackCollector] (emit audit event)
       ↓ (queue feedback)
[Batching Threshold]
       ↓ (10 feedback OR 1h)
[SkillConfigTuner]
       ↓ (feedback → signal → delta)
[Gradient Descent Optimization]
       ↓ (compute loss before/after)
[Delta Application Decision]
       ↓ (improvement? → apply OR skip)
[SkillInstance.apply_config_update()]
       ↓ (validate, clamp, persist, audit)
[Config Reload]
       ↓
[Next Skill Execution]
       ↓ (uses improved config)
[Metrics Captured]
       ↓ (measure improvement)
[Convergence Detection]
       ↓ (learning loop closes)
```

---

## Implementation Details

### 1. SkillInstance (skill_instance.py)

**File:** `/home/shumway/projects/CorvinOS/core/skills/skill_instance.py` (480 LoC)

**Responsibilities:**
- Runtime instance of a skill with tunable config
- Load config from persistent store on init
- Execute skill and capture metrics
- Collect & scrub user feedback
- Apply config updates (validate, clamp, persist)
- Audit all changes

**Key Methods:**

```python
class SkillInstance:
    def __init__(
        self,
        skill_id: str,
        initial_config: Dict[str, Any],
        config_store_path: Optional[Path] = None,
        audit_backend=None,
        tenant_id: str = "_default",
    )
        # Load latest config from store or use initial
        
    def execute(
        self,
        request: Dict[str, Any],
        executor: Callable[[Dict, Dict], Any],
    ) -> Any
        # Execute skill, capture latency/success metrics
        
    def receive_feedback(
        self,
        quality_rating: int,  # 1-5
        execution_id: Optional[str] = None,
        notes: str = "",
    ) -> None
        # Queue feedback (PII scrubbed)
        
    def apply_config_update(
        self,
        new_config: Dict[str, Any],
        reason: str = "",
    ) -> bool
        # Validate, clamp, persist, audit
        
    def get_feedback_batch(self) -> List[Dict[str, Any]]
        # Get accumulated feedback (for learning loop)
        
    def get_metrics_summary(self) -> Dict[str, Any]
        # Return execution metrics (latency, success_rate, config_version)
```

**PII Scrubbing:**
- Email: `[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}` → `[EMAIL]`
- Phone (US): `\d{3}[-.\s]?\d{3}[-.\s]?\d{4}` → `[PHONE]`
- Credit card: `\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}` → `[CC]`

**Config Bounds (Fail-Closed):**
```python
PARAM_BOUNDS = {
    "routing_threshold": (0.5, 0.95),       # Routing decision threshold
    "attention_weight": (0.0, 1.0),         # Context relevance weight
    "latency_target_ms": (50.0, 500.0),     # Performance target
}
```

### 2. SkillConfigTuner (skill_config_tuner.py)

**File:** `/home/shumway/projects/CorvinOS/core/skills/skill_config_tuner.py` (420 LoC)

**Responsibilities:**
- Convert feedback → normalized signal (0-1 confidence)
- Compute config delta via gradient descent
- Clamp changes to ±10% (fail-closed)
- Estimate loss improvement
- Detect convergence (plateau detection)

**Three Tuning Strategies:**

| Skill Type | Tuned Parameter | High Confidence (≥0.8) | Low Confidence (<0.6) |
|---|---|---|---|
| **Router** | `routing_threshold` | Increase (be more selective) | Decrease (be more permissive) |
| **Adapter** | `attention_weight` | Increase (trust context more) | Decrease (trust context less) |
| **Workflow** | `latency_target_ms` | Increase (optimize for speed) | Decrease (optimize for accuracy) |

**Loss Function:**
```
Loss = (1 - confidence_score) + 0.1 * (latency_ms / 100)

Lower loss = better (high confidence + low latency)
```

**Gradient Descent:**
```python
gradient = 1.0 if signal.value >= 0.8 else (-1.0 if signal.value < 0.6 else 0.0)
raw_delta = gradient * LEARNING_RATE  # 0.05
clamped_delta = max(-0.10, min(0.10, raw_delta))  # ±10% clamp
new_value = current_value * (1 + clamped_delta)
```

**Key Methods:**

```python
class SkillConfigTuner:
    def feedback_to_signal(self, feedback: Dict) -> Optional[FeedbackSignal]
        # rating: 1-5 → signal.value: 0-1
        
    def compute_config_delta(
        self,
        signal: FeedbackSignal,
        current_config: Dict,
        loss_before: float,
    ) -> Optional[ConfigDelta]
        # Gradient descent → param change
        
    def should_apply_delta(
        self,
        delta: ConfigDelta,
        loss_before: float,
        loss_estimated_after: float,
    ) -> bool
        # improvement >= CONVERGENCE_THRESHOLD → apply
        
    def compute_convergence_metrics(
        self,
        losses: List[float],
        window: int = 5,
    ) -> Dict[str, float]
        # Slope analysis → is_converged, plateau_confidence
```

### 3. FeedbackCollector (core/learning/feedback_collector.py)

**Responsibilities:**
- Validate feedback structure & ranges
- Scrub PII (email, phone, credit card)
- Emit audit events
- Tenant-scoped storage

**Entry Point:**
```python
def collect_feedback(
    skill_id: str,
    quality_rating: int,  # 1-5
    tenant_id: str = "_default",
) -> FeedbackReceivedEvent
    # Validate → scrub → emit audit → return event
```

---

## Integration Points

### 1. API Endpoints (routes/learning_dashboard.py)

**Submit Feedback:**
```
POST /api/v1/console/learning/feedback
{
    "skill_id": "os.delegation_router",
    "quality_rating": 4,
    "execution_id": "exec_12345",
    "notes": "Good routing decision"
}

Response: 200 OK
{
    "feedback_id": "fb_xyz",
    "timestamp": "2026-09-18T12:34:56Z",
    "audit_event": "skill_feedback_received"
}
```

**Trigger Optimization:**
```
POST /api/v1/console/learning/optimize
{
    "skill_id": "os.delegation_router",
    "force": false  # false = only if threshold met
}

Response: 200 OK
{
    "optimization_triggered": true,
    "feedback_count": 10,
    "config_deltas_applied": 1,
    "loss_improvement": 0.05
}
```

### 2. Config Persistence

**Store Location:** `~/.corvin/tenants/<tenant_id>/global/skill_configs.json`

**Format:**
```json
{
    "os.delegation_router": {
        "routing_threshold": 0.72,
        "attention_weight": 0.5,
        "latency_target_ms": 200.0,
        "version": 1
    }
}
```

**History:** `~/.corvin/tenants/<tenant_id>/global/skill_config_history.jsonl`

```jsonl
{"timestamp": "...", "skill_id": "...", "version_before": 0, "version_after": 1, "delta_percent": 2.86}
```

### 3. Audit Trail Integration

Every config change logged to `~/.corvin/audit.jsonl` (GDPR Art. 30, 32):

```json
{
    "event_type": "skill_config_updated",
    "skill_id": "os.delegation_router",
    "tenant_id": "_default",
    "timestamp": "2026-09-18T12:34:56Z",
    "reason": "learning",
    "version_before": 0,
    "version_after": 1,
    "config_hash_before": "sha256...",
    "config_hash_after": "sha256...",
    "delta": {
        "routing_threshold": [0.7, 0.72]
    }
}
```

---

## Test Coverage

### Gate 2: E2E Wiring Proof (test_track_e_gate2_e2e_wiring.py)

10 test classes, 18+ assertions:
- ✅ Feedback endpoint accepts quality_rating (1-5)
- ✅ FeedbackCollector validates & stores tenant-scoped
- ✅ SkillConfigTuner computes deltas
- ✅ ConfigApplier applies & persists
- ✅ SkillInstance reloads tuned config
- ✅ Measurable improvement detected
- ✅ Audit trail complete (GDPR)
- ✅ Multiple iterations show convergence

### Gate 3: RED→GREEN Implementation (test_track_e_gate3_red_green.py)

8 test classes, 35+ assertions:
- ✅ SkillInstance initialization & execution
- ✅ Feedback collection & PII scrubbing
- ✅ Signal processing (rating → confidence)
- ✅ Config tuner delta computation
- ✅ Config validation & clamping
- ✅ Execution with tuned config
- ✅ Config reload on init
- ✅ Convergence detection
- ✅ Complete cycle test (execute → feedback → tune → execute)

### Gate 4: Adversarial Testing (test_track_e_gate4_adversarial.py)

8 test classes, 18 attack vectors:
- ✅ Feedback injection (spam, oscillation, bombardment)
- ✅ Config corruption (NaN, negative, overflow)
- ✅ PII bypass (base64, Unicode, partial)
- ✅ Concurrent updates (race conditions)
- ✅ Rollback edge cases (version mismatch)
- ✅ Convergence false positives (outliers, noise)
- ✅ Audit tampering (order preservation)
- ✅ Resource exhaustion (unbounded growth)
- ✅ Combined attacks (spam + corruption)

---

## Compliance & Safety

### GDPR Art. 30, 32 (Audit Trail)

Every config change is:
- ✅ Immutable (append-only to audit.jsonl)
- ✅ Timestamped (ISO 8601 UTC)
- ✅ Attributed (skill_id, tenant_id, reason)
- ✅ Hash-chained (config_hash_before/after)
- ✅ Reversible (can re-construct any prior state)

### Fail-Closed Design

- ✅ Config validation rejects incomplete updates
- ✅ All values clamped to domain bounds (±10% max delta)
- ✅ NaN/Inf/negative values → lower bound
- ✅ PII scrubbing runs BEFORE storage (not after)
- ✅ Tenant isolation enforced (no cross-tenant feedback)

### Adversarial Resilience

- ✅ Spam feedback doesn't cause oscillation (clamped)
- ✅ Corrupted config rejected or clamped
- ✅ Concurrent updates handled (last-write-wins)
- ✅ Convergence false positives don't crash system

---

## Usage Example

```python
from core.skills.skill_instance import SkillInstance
from core.skills.skill_config_tuner import SkillConfigTuner
from pathlib import Path

# 1. Initialize SkillInstance with initial config
instance = SkillInstance(
    skill_id="os.delegation_router",
    initial_config={
        "routing_threshold": 0.7,
        "attention_weight": 0.5,
        "latency_target_ms": 200.0,
        "version": 0,
    },
    config_store_path=Path("~/.corvin/skill_configs.json"),
    tenant_id="_default",
)

# 2. Execute skill with current config
def skill_logic(request, config):
    # Use config["routing_threshold"] for routing decision
    return {"routed": True, "confidence": 0.85}

instance.execute({"task": "classify"}, skill_logic)

# 3. Receive user feedback
instance.receive_feedback(
    quality_rating=5,  # Excellent!
    notes="Great routing decision"
)

# 4. Get feedback batch (for learning loop)
feedback_batch = instance.get_feedback_batch()

# 5. Compute config delta (learning loop → tuner)
tuner = SkillConfigTuner(skill_type="router")
signal = tuner.feedback_to_signal(feedback_batch[0])
metrics = {"confidence_score": 0.85, "latency_ms": 180.0}
loss_before = tuner.compute_loss(metrics)
delta = tuner.compute_config_delta(signal, instance.config, loss_before)

# 6. Apply delta (tuner → instance)
if delta:
    new_config = instance.config.copy()
    new_config[delta.param_name] = delta.new_value
    instance.apply_config_update(new_config, reason="learning")

# 7. Next execution uses improved config (v1 instead of v0)
instance.execute({"task": "classify_2"}, skill_logic)

# 8. Monitor metrics
summary = instance.get_metrics_summary()
print(f"Config v{summary['config_version']}: {summary['success_rate']*100:.1f}% success")
```

---

## Known Limitations & Future Work

1. **PII Scrubbing:** Regex-based detection only (catches common cases, not all)
   - Future: ML-based PII detector (spaCy, Presidio)

2. **Execution History:** Unbounded growth in memory
   - Future: Ring buffer or periodic cleanup to disk

3. **Config Store:** JSON file-based (not distributed)
   - Future: Redis/etcd for multi-instance sync

4. **Gradient Descent:** Simple step function (not ML model)
   - Future: Neural network-based parameter optimization

5. **Convergence Detection:** Slope-based (naive)
   - Future: Bayesian convergence detection (ADR-0680)

---

## References

- **ADR-0675:** Skill Forge v2.0 Phase 1 Implementation
- **ADR-0676:** Phase 3 Background Learning Daemon
- **ADR-0314:** Learning Infrastructure Event Schema
- **ADR-0232/0233:** Audit Chain & Boot Tripwire (GDPR compliance)

---

## Testing Instructions

### Run E2E Wiring Tests (Gate 2)
```bash
cd /home/shumway/projects/CorvinOS
python3 -m pytest tests/test_track_e_gate2_e2e_wiring.py -v
```

### Run RED→GREEN Tests (Gate 3)
```bash
python3 -m pytest tests/test_track_e_gate3_red_green.py -v
```

### Run Adversarial Tests (Gate 4)
```bash
python3 -m pytest tests/test_track_e_gate4_adversarial.py -v
```

### Run All Track E Tests
```bash
python3 -m pytest tests/test_track_e_*.py -v --tb=short
```

---

## Commits

1. **Gate 2:** `test_track_e_gate2_e2e_wiring.py` (10 test classes)
2. **Gate 3:** `core/skills/skill_instance.py` + `core/skills/skill_config_tuner.py` (implementation)
3. **Gate 3:** `test_track_e_gate3_red_green.py` (8 test classes)
4. **Gate 4:** `test_track_e_gate4_adversarial.py` (18 attack vectors)
5. **Gate 5:** `docs/claude-ref/track-e-learning-skill-integration.md` (this doc)
6. **Gate 5:** ADR-0675/0676 amendments (in Corvin-ADR repo)

All changes audited to `~/.corvin/audit.jsonl` (GDPR Art. 30, 32).

---

**Track E Status: ✅ COMPLETE** — All 5 LDD gates passed, ready for production.
