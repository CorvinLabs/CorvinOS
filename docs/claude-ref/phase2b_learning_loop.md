# Phase 2b Learning Loop Optimization

**Status:** Phase 2b (in development)  
**ADRs:** 0314 (learning events), 0532 (skills), 0722 (loss signals)  
**Files:**
- `core/learning/phase2b_optimizer.py` (confidence monitoring, parameter optimization)
- `core/learning/convergence_detector.py` (phase tracking, false convergence detection)
- `core/learning/phase2b_integration.py` (audit-first event logging)
- `tests/e2e/test_learning_loop_optimization_e2e.py` (E2E test suite)

---

## Overview

Phase 2b implements automated learning loop optimization for Skill-primary routing. When a Skill's confidence exceeds **0.75**, it switches from fallback behavior to primary routing. The learning loop monitors confidence trends, detects convergence, triggers parameter optimizations, and alerts on divergence.

**Phase 2b Switch Logic:**
```python
if skill_confidence > 0.75:
    route_to_skill_primary()  # Primary, not fallback
else:
    route_to_fallback()       # Original behavior
```

---

## Components

### 1. Phase2bOptimizer (`phase2b_optimizer.py`, 669 LoC)

Monitors skill confidence and triggers parameter optimizations.

**Key Methods:**
- `record_confidence_score(skill_id, confidence)` → ConfidenceTrend
  - Records a confidence observation
  - Maintains 7-day rolling average/min/max/variance
  - Detects trend direction (climbing/plateau/diverging)
  - Checks Phase 2b eligibility (confidence > 0.75)

- `detect_convergence(skill_id)` → bool
  - True if confidence ≥ 0.90 AND stable for 7+ days

- `detect_divergence(skill_id)` → bool
  - True if was plateaued, now declining (confidence < 95% of rolling avg)

- `trigger_optimization(skill_id, reason)` → ParameterDelta
  - Triggers on: "convergence_detected", "feedback_signal", "velocity_improvement"
  - Adjusts confidence_threshold parameter (±2%, clamped [0.50, 0.95])
  - Returns ParameterDelta record for audit logging

- `get_confidence_trend(skill_id)` → ConfidenceTrend
  - Returns current trend snapshot

- `get_phase_2b_eligible_skills()` → List[str]
  - Returns all skills with confidence > 0.75

**Configuration:**
```python
PHASE_2B_CONFIDENCE_THRESHOLD = 0.75      # Switch point
CONVERGENCE_PLATEAU_THRESHOLD = 0.90      # Converged level
CONVERGENCE_PLATEAU_DAYS = 7              # Stability requirement
ROLLING_WINDOW_DAYS = 7                   # Trend window
IMPROVEMENT_THRESHOLD = 0.01              # 1% = non-plateau
FEEDBACK_VELOCITY_THRESHOLD_MS = 3600000  # 1 hour feedback-to-adjust cycle
THRESHOLD_ADJUST_DELTA = 0.02             # ±2% per optimization
```

**Data Structures:**
- `ConfidenceTrend`: immutable snapshot of confidence state
  - `current_confidence`, `rolling_avg_7day`, `rolling_min_7day`, `rolling_max_7day`
  - `variance_7day`, `n_samples`, `plateau_days`
  - `trend_direction`, `phase_2b_eligible`

- `ParameterDelta`: parameter change record
  - `skill_id`, `param_name`, `old_value`, `new_value`
  - `reason`, `confidence_boost`, `timestamp`

- `LearningVelocity`: feedback → adjustment cycle time
  - `skill_id`, `feedback_timestamp`, `adjustment_timestamp`
  - `cycle_time_ms`, `feedback_signal_strength`

---

### 2. ConvergenceDetector (`convergence_detector.py`, 608 LoC)

Tracks convergence phases and detects false convergence / divergence.

**Phase Machine:**
```
EXPLORATION (n < 10)
    ↓ (10+ samples, stable 3+ days)
PLATEAU (stable, no improvement)
    ├→ FALSE_POSITIVE? (if later resumes climbing > 2%)
    └→ EXPLOITATION (after 3-7 days stable, n ≥ 30)
        ├→ CONVERGED (confidence ≥ 0.90, stable 7+ days)
        └→ DIVERGING (confidence declining)
            ↓ (if recovering)
        EXPLOITATION (recovery path)
```

**Key Methods:**
- `update_phase(skill_id, confidence, n_samples, trend_direction, plateau_days)` → ConvergencePhase
  - Updates phase based on current state
  - Records transition with reason if phase changed
  - Schedules next optimization

- `detect_false_convergence(skill_id)` → bool
  - True if plateau was followed by > 2% improvement (early false positive)

- `detect_divergence(skill_id, current_confidence)` → DivergenceAlert | None
  - Detects confidence declining from plateau
  - Returns alert with severity (warning: -5% to -10%, critical: > -10%)

- `check_plateau_resumed(skill_id, new_confidence)` → bool
  - Checks if plateau resumed climbing significantly

- `get_current_phase(skill_id)` → ConvergencePhase
  - Returns current phase

- `get_next_optimization_date(skill_id)` → datetime
  - Scheduled optimization date based on phase

**Phase Thresholds:**
- EXPLORATION_SAMPLE_MIN = 10
- PLATEAU_STABILITY_DAYS_MIN = 3
- EXPLOITATION_SAMPLE_MIN = 30
- CONVERGENCE_CONFIDENCE_MIN = 0.90
- CONVERGENCE_PLATEAU_DAYS_MIN = 7

**Data Structures:**
- `ConvergencePhase`: enum with states (EXPLORATION, PLATEAU, EXPLOITATION, CONVERGED, DIVERGING)

- `ConvergencePhaseTransition`: records phase change
  - `skill_id`, `from_phase`, `to_phase`, `reason`
  - `confidence_at_transition`, `n_samples_at_transition`, `timestamp`

- `DivergenceAlert`: alert on confidence decline
  - `skill_id`, `plateau_confidence`, `current_confidence`
  - `decline_percent`, `severity`, `recommended_action`

- `FalseConvergenceBoundary`: tracks plateau for false positive detection
  - `plateau_confidence`, `observed_as_true`, `false_positive`

- `OptimizationSchedule`: scheduled optimization
  - `current_phase`, `next_optimization_date`, `reason`, `priority`

---

### 3. Phase2bAuditIntegration (`phase2b_integration.py`, 283 LoC)

Audit-first event logging for all Phase 2b operations (ADR-0314 EventStore integration).

**Events Logged:**
- `confidence_trend_recorded`: every observation
- `phase_transition_recorded`: when phase changes
- `convergence_detected`: when convergence achieved
- `false_convergence_detected`: when false positive detected
- `divergence_detected`: when divergence alert triggered
- `optimization_triggered`: when parameter optimization fires
- `threshold_adjusted`: when threshold auto-adjusted

All events:
- Immutable (appended, never modified)
- Tenant-scoped (GDPR Art. 32)
- Hash-chained via EventStore (ADR-0314)
- Timestamped and attributed

**Integration Points:**
```python
integration = Phase2bAuditIntegration(event_store)

# When optimizer records confidence
integration.log_confidence_trend(skill_id, trend, tenant_id)

# When phase changes
integration.log_phase_transition(transition, tenant_id)

# When optimization triggered
integration.log_optimization_triggered(delta, signal_strength, tenant_id)

# When divergence detected
integration.log_divergence_detected(alert, tenant_id)
```

---

## Usage Example

### Setup

```python
from pathlib import Path
from core.learning.phase2b_optimizer import Phase2bOptimizer
from core.learning.convergence_detector import ConvergenceDetector
from core.learning.phase2b_integration import Phase2bAuditIntegration
from core.learning.event_store import EventStore

tenant_home = Path.home() / ".corvin" / "tenants" / "default"
tenant_id = "default"

# Initialize components
optimizer = Phase2bOptimizer(tenant_id, tenant_home)
detector = ConvergenceDetector(tenant_id, tenant_home)
event_store = EventStore(tenant_home, tenant_id)
integration = Phase2bAuditIntegration(event_store)
```

### Record Confidence and Track Learning

```python
from datetime import datetime, timezone

# Record a confidence observation
trend = optimizer.record_confidence_score(
    skill_id="os.delegation_router",
    confidence=0.78,
    timestamp=datetime.now(timezone.utc),
)

# Log audit event
integration.log_confidence_trend(trend.skill_id, trend, tenant_id)

# Update phase
phase = detector.update_phase(
    "os.delegation_router",
    current_confidence=trend.current_confidence,
    n_samples=trend.n_samples,
    trend_direction=trend.trend_direction,
    plateau_days=trend.plateau_days,
)

# Log phase transition
if phase != detector.get_current_phase("os.delegation_router"):
    transitions = detector.get_phase_history("os.delegation_router")
    integration.log_phase_transition(transitions[-1], tenant_id)
```

### Monitor for Convergence

```python
# Check convergence
if optimizer.detect_convergence("os.delegation_router"):
    print("✅ Skill converged!")
    
    # Trigger optimization
    delta = optimizer.trigger_optimization(
        "os.delegation_router",
        reason="convergence_detected",
        feedback_signal_strength=0.85,
    )
    
    if delta:
        integration.log_optimization_triggered(delta, 0.85, tenant_id)
        print(f"Threshold adjusted: {delta.old_value:.3f} → {delta.new_value:.3f}")
```

### Detect Divergence

```python
# Check divergence
alert = detector.detect_divergence(
    "os.delegation_router",
    current_confidence=0.82,
)

if alert:
    print(f"⚠️  Divergence detected: {alert.severity}")
    print(f"Recommended action: {alert.recommended_action}")
    
    integration.log_divergence_detected(alert, tenant_id)
```

### Phase 2b Routing Decision

```python
# Decide routing based on confidence
eligible_skills = optimizer.get_phase_2b_eligible_skills()

for skill_id in eligible_skills:
    trend = optimizer.get_confidence_trend(skill_id)
    
    if trend.phase_2b_eligible:
        route_to_skill_primary(skill_id)
    else:
        route_to_fallback(skill_id)
```

---

## E2E Test Suite

File: `tests/e2e/test_learning_loop_optimization_e2e.py`

**Test Classes:**
- `TestPhase2bOptimizer`: 8 tests for confidence monitoring and optimization
- `TestConvergenceDetector`: 8 tests for phase tracking and false convergence
- `TestLearningLoopE2E`: 3 integration tests simulating 3-week learning cycles

**Key Scenarios:**
1. **3-week learning cycle**: exploration → plateau → exploitation → convergence
2. **False convergence recovery**: early plateau that resumes climbing
3. **Divergence and recovery**: confidence decline followed by recovery

All tests use in-memory storage (TemporaryDirectory) and are fully isolated.

---

## Audit Trail

Every Phase 2b operation is logged to EventStore with:
- Event type (confidence_trend_recorded, phase_transition_recorded, etc.)
- Skill ID and tenant ID
- Full event payload (immutable dataclass)
- Timestamp (UTC)
- Hash-chain link to previous event (ADR-0314)

**Sample Audit Event:**
```json
{
  "event_type": "convergence_detected",
  "skill_id": "os.delegation_router",
  "tenant_id": "default",
  "confidence": 0.92,
  "plateau_days": 7,
  "n_samples": 35,
  "timestamp": "2026-09-27T18:30:45.123456Z",
  "audit_id": "evt-abc123...",
  "prev_hash": "sha256(...)",
  "hash": "sha256(...)"
}
```

All events remain in the chain indefinitely (append-only). The hash chain is verified by the ADR-0232 boot tripwire before any learning loop operates.

---

## Compliance

**GDPR Art. 5 (Accountability):** Every confidence observation, phase transition, and optimization is immutably logged.

**GDPR Art. 30 (Processing Record):** EventStore maintains audit trail of all learning decisions.

**GDPR Art. 32 (Security):** Tenant isolation enforced on all queries and writes. Events are hash-chained.

**EU AI Act Art. 50 (Transparency):** Learning events support decision attribution and user understanding of AI behavior.

---

## Configuration

Configure Phase 2b thresholds in `tenant.corvin.yaml`:

```yaml
spec:
  learning:
    phase2b:
      enabled: true
      confidence_threshold: 0.75          # Switch to skill-primary
      convergence_threshold: 0.90         # Converged level
      convergence_plateau_days: 7         # Stability requirement
      rolling_window_days: 7
      threshold_adjust_delta: 0.02        # ±2% per optimization
```

Or programmatically:

```python
optimizer = Phase2bOptimizer(
    tenant_id="default",
    tenant_home=Path.home() / ".corvin" / "tenants" / "default",
    observation_window_days=7,
    convergence_threshold=0.90,
)
```

---

## Troubleshooting

**Issue:** Phase 2b skills not switching to primary routing  
**Cause:** Confidence below 0.75  
**Debug:** Check `optimizer.get_confidence_trend(skill_id).phase_2b_eligible`

**Issue:** False convergence detected repeatedly  
**Cause:** Skill oscillating around convergence boundary  
**Solution:** Increase `CONVERGENCE_PLATEAU_DAYS` or review parameter tuning

**Issue:** Divergence alert but skill recovering  
**Cause:** Normal variance in learning curve  
**Debug:** Check `detector.get_divergence_alerts(skill_id)` to see alert history

**Issue:** Optimization not triggering  
**Cause:** Feedback signal too weak (< 0.6) or convergence not detected  
**Debug:** Check `optimizer.trigger_optimization()` with explicit reason

---

## Related Documentation

- **ADR-0314:** Learning Infrastructure (EventStore, event schema)
- **ADR-0532:** OS-Skills Architecture (skill routing decisions)
- **ADR-0722:** Loss-Signal Learning Integration (feedback loop closure)
- **Layer 44:** House-Rules enforcement (skill decisions must respect compliance gates)

---

## Version

- **Phase 2b v1.0** (2026-09-27)
- Compatible with Core learning events (ADR-0314)
- Requires EventStore integration
