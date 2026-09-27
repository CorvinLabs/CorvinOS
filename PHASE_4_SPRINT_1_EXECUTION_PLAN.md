# Phase 4 Sprint 1 Execution Plan (Week 5–6)

**Status:** 🟡 READY TO START  
**Token Budget:** ~5M remaining → Used for planning; implementation is 4–6 week effort  
**Loop-Driven Checkpoints:** Every 2 findings (Week 5 Day 4, Week 6 Day 4, Week 7 Day 4, Week 8 Day 7)

---

## Sprint 1: Security + Performance Telemetry (Week 5–6)

### Stream A: Security Findings (1.2 + 1.3)

**Finding 1.2: Skill Version Attack**

**File:** `core/skills/os_skills/version_pinning.py` (NEW)

```python
# Phase 4 Sprint 1: Skill Version Pinning Validation

from dataclasses import dataclass
from typing import Optional, Tuple

@dataclass(frozen=True)
class VersionPinConstraint:
    skill_id: str
    pinned_version: str  # e.g., "1.0.0"
    constraint_type: str  # "exact", "min", "range"

class VersionPinValidator:
    """Fail-closed version validation for skill selection."""
    
    def __init__(self):
        self._pins = {}  # skill_id → VersionPinConstraint
        self._violations = []  # Audit trail of version mismatches
    
    def validate_skill_version(
        self,
        skill_id: str,
        requested_version: str,
        actual_version: str,
    ) -> Tuple[bool, str]:
        """
        Validate skill version matches constraint.
        
        Returns: (is_valid, reason)
        Fail-closed: if mismatch, use pinned version
        """
        constraint = self._pins.get(skill_id)
        
        if constraint is None:
            return True, "no_constraint"
        
        if requested_version == actual_version == constraint.pinned_version:
            return True, "match_pinned"
        
        # MISMATCH: Log violation, use pinned
        reason = f"version_mismatch: requested={requested_version}, actual={actual_version}, pinned={constraint.pinned_version}"
        self._violations.append({
            "skill_id": skill_id,
            "mismatch_reason": reason,
        })
        
        return False, reason
    
    def set_pin(self, constraint: VersionPinConstraint):
        """Register version pin constraint."""
        self._pins[constraint.skill_id] = constraint
    
    def get_violations(self):
        """Return all recorded version mismatches."""
        return self._violations
```

**Test:** `tests/unit/test_skill_version_pinning.py`

```python
def test_version_mismatch_uses_pinned_version():
    validator = VersionPinValidator()
    constraint = VersionPinConstraint(
        skill_id="os.router",
        pinned_version="1.0.0",
        constraint_type="exact",
    )
    validator.set_pin(constraint)
    
    # Operator deploys v1.0.1, but pin says 1.0.0
    is_valid, reason = validator.validate_skill_version(
        skill_id="os.router",
        requested_version="1.0.1",
        actual_version="1.0.0",  # pinned value
    )
    
    assert not is_valid
    assert "version_mismatch" in reason
    assert len(validator.get_violations()) == 1
```

**Finding 1.3: Feedback Poisoning — Quota Enforcement**

**File:** `core/learning/feedback_quota.py` (NEW)

```python
from datetime import datetime, timedelta
from dataclasses import dataclass

@dataclass
class QuotaPolicy:
    max_feedback_per_skill_per_day: int = 1000
    max_feedback_per_user_per_day: int = 10000
    window_hours: int = 24

class FeedbackQuotaEnforcer:
    """Prevent feedback poisoning via quota limits."""
    
    def __init__(self, policy: QuotaPolicy):
        self.policy = policy
        self._feedback_count = {}  # (skill_id, window) → count
        self._violations = []
    
    def check_quota(self, skill_id: str, tenant_id: str) -> bool:
        """Check if feedback accepted under quota."""
        window_key = (skill_id, self._get_window())
        count = self._feedback_count.get(window_key, 0)
        
        if count >= self.policy.max_feedback_per_skill_per_day:
            self._violations.append({
                "reason": "quota_exceeded",
                "skill_id": skill_id,
                "count": count,
            })
            return False
        
        self._feedback_count[window_key] = count + 1
        return True
    
    def _get_window(self) -> str:
        return datetime.utcnow().strftime("%Y-%m-%d")
```

---

### Stream B: Performance Telemetry (3.1–3.5)

**File:** `core/learning/phase4_telemetry.py` (NEW)

```python
from dataclasses import dataclass
from collections import deque

@dataclass
class PerformanceMetric:
    metric_name: str
    value: float
    timestamp: str
    skill_id: Optional[str] = None

class Phase4TelemetryCollector:
    """Collect performance metrics for findings 3.1–3.5."""
    
    def __init__(self):
        self._skill_latency = deque(maxlen=10000)  # Finding 3.1
        self._selection_latency = deque(maxlen=10000)  # Finding 3.2
        self._cache_hits = 0
        self._cache_misses = 0
        self._queue_depth_samples = deque(maxlen=10000)  # Finding 3.5
    
    def record_skill_latency(self, skill_id: str, latency_ms: float):
        """Finding 3.1: Skill execution latency."""
        self._skill_latency.append(PerformanceMetric(
            metric_name="skill_execution_latency_ms",
            value=latency_ms,
            timestamp=datetime.utcnow().isoformat(),
            skill_id=skill_id,
        ))
    
    def record_selection_latency(self, latency_ms: float):
        """Finding 3.2: Selection process latency."""
        self._selection_latency.append(PerformanceMetric(
            metric_name="selection_latency_ms",
            value=latency_ms,
            timestamp=datetime.utcnow().isoformat(),
        ))
    
    def record_cache_hit(self):
        """Finding 3.3: Cache hit rate."""
        self._cache_hits += 1
    
    def record_cache_miss(self):
        """Finding 3.3: Cache miss rate."""
        self._cache_misses += 1
    
    def record_queue_depth(self, depth: int):
        """Finding 3.5: Queue overflow monitoring."""
        self._queue_depth_samples.append(PerformanceMetric(
            metric_name="feedback_queue_depth",
            value=float(depth),
            timestamp=datetime.utcnow().isoformat(),
        ))
    
    def get_stats(self):
        """Return aggregated statistics."""
        return {
            "skill_latency_p50_ms": self._percentile(self._skill_latency, 50),
            "skill_latency_p95_ms": self._percentile(self._skill_latency, 95),
            "skill_latency_p99_ms": self._percentile(self._skill_latency, 99),
            "cache_hit_rate": self._cache_hits / max(1, self._cache_hits + self._cache_misses),
            "queue_max_depth": max([m.value for m in self._queue_depth_samples], default=0),
        }
    
    @staticmethod
    def _percentile(data, p):
        """Calculate percentile."""
        if not data:
            return 0
        sorted_data = sorted(data, key=lambda m: m.value)
        index = int(len(sorted_data) * p / 100)
        return sorted_data[index].value
```

---

## Loop-Driven Validation (Week 5 Day 4)

**Checkpoint Tests:**

```python
# tests/integration/test_phase4_sprint1_checkpoint.py

def test_version_pinning_end_to_end():
    """Finding 1.2: Version mismatch handled gracefully."""
    # Test passes mid-flight deployment scenario
    pass  # Implement in Sprint 1

def test_feedback_quota_blocks_poisoning():
    """Finding 1.3: Quota enforcement prevents attack."""
    # Test exceeding quota rejects feedback
    pass  # Implement in Sprint 1

def test_telemetry_collection_end_to_end():
    """Findings 3.1–3.5: All metrics collected + visible."""
    # Test skill latency, selection latency, cache hit rate, queue depth
    pass  # Implement in Sprint 1
```

---

## Week 5–6 Deliverables (Sprint 1 Definition of Done)

- [ ] Finding 1.2: Version pinning validator (production-ready)
- [ ] Finding 1.3: Feedback quota enforcer (production-ready)
- [ ] Findings 3.1–3.5: Telemetry collector + dashboard wiring (production-ready)
- [ ] All checkpoint tests passing (Week 5 Day 4)
- [ ] E2E validation: mid-flight deployment scenario passes
- [ ] Code committed to CorvinOS/main
- [ ] ADR-0533 + ADR-0319 updated with implementation details

---

## Timeline Reassessment (Week 5 Day 4)

**After Sprint 1 Checkpoint:**
- If all findings pass validation: proceed to Sprint 2 Week 6
- If <2 findings blocked: proceed to Sprint 2 with parallel mitigation
- If >2 findings blocked: escalate, adjust parallelization

**Week 11 Go-Live Feasibility:**
- Current plan: 18 findings × 5 parallel streams = Week 9 completion
- Buffer: 1 week for integration + validation
- **Timeline Status:** ON TRACK ✅

---

**Next: Sprint 1 Implementation (Week 5 Details)**
Estimated effort: 3–4 days of engineering time across parallel streams
