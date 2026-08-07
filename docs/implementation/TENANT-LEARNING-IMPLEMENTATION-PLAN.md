# Tenant-Level Learning Implementation Plan (ADR-0274)

**Status:** Planning  
**Date:** 2026-08-07  
**Integration Point:** After CEL Phase 4 Week 4 (ADR-0271 feedback infrastructure complete)

---

## Overview

This plan implements three-tier tenant learning (CONCEPT-0003, ADR-0274) on top of CEL Phase 4 foundations. The goal: transform per-session learning into persistent tenant identity.

**Timeline:** 2 weeks (Week 5–6 in CEL Phase 4 roadmap)

---

## Phase 1: Tier 2 Queue Infrastructure (3 days, Week 5 Mon–Wed)

### 1.1 Create `learning_queue.py` Module

**File:** `/home/shumway/projects/CorvinOS/operator/context_engineering/learning_queue.py`

**Purpose:** Implement append-only, date-based JSONL queue for ContextEvaluation records.

**Core Components:**

```python
# Data structure
@dataclass
class LearningQueueRecord:
    """Single immutable record appended to queue."""
    context_id: str
    task_id: str
    relevance_actual: float
    helpfulness: float
    correctness: float
    impact: str  # "CRITICAL" | "helpful" | "neutral" | "harmful"
    notes: Optional[str]
    timestamp: datetime
    user_id: str
    task_keywords: List[str]
    checksum: str  # SHA256 of record content

# Main class
class LearningQueue:
    def __init__(self, queue_root: Path = None):
        self.queue_root = queue_root or Path.home() / ".corvin" / "tenants" / "_default" / "learning-queue"
        self.queue_root.mkdir(parents=True, exist_ok=True)
    
    def append_record(self, record: LearningQueueRecord) -> bool:
        """Atomically append record to dated file."""
        # Implementation: tempfile + rename for atomicity
        # File locking for concurrent writes
        # Return: success flag
        pass
    
    def read_all_records(self, start_date: date = None, end_date: date = None) -> Iterator[LearningQueueRecord]:
        """Stream records from all files in date range."""
        pass
    
    def validate_checksums(self, filepath: Path) -> bool:
        """Verify file integrity."""
        pass
    
    def rotate_files(self) -> None:
        """Manage dated files (consolidate small files, cleanup old)."""
        pass
    
    def get_record_count(self, start_date: date = None) -> int:
        """Query total records in date range."""
        pass
```

**Key Requirements:**
- ✅ Atomic appends (lock-safe, crash-safe)
- ✅ Dated JSONL files (one per week minimum, more frequent if high volume)
- ✅ SHA256 checksums per file
- ✅ Concurrent write safety (threading.Lock)
- ✅ Streaming reader (not load-all-into-memory)

**Tests to Create:**
- `test_atomic_append.py` — Concurrent writes don't corrupt
- `test_file_rotation.py` — Date-based file management
- `test_checksum_validation.py` — Integrity checks work
- `test_reader_crash_safety.py` — Reader handles corrupt records gracefully

**Acceptance Criteria:**
- [ ] 100+ concurrent appends succeed without data loss
- [ ] Checksums verify correctly
- [ ] Files properly rotated by date
- [ ] Reader returns all records in order

---

### 1.2 Create `_metadata.json` Schema

**File:** `~/.corvin/tenants/_default/learning-queue/_metadata.json`

**Purpose:** Track queue state, record counts, date ranges.

**Schema:**
```json
{
  "version": "1.0",
  "created_at": "2026-08-07T18:00:00Z",
  "last_updated": "2026-08-07T18:30:00Z",
  "total_records": 1243,
  "date_range": {
    "earliest": "2026-08-01",
    "latest": "2026-08-07"
  },
  "files": [
    {
      "date": "2026-08-07",
      "filename": "2026-08-07.jsonl",
      "record_count": 187,
      "checksum": "sha256:abc123...",
      "size_bytes": 18700
    }
  ],
  "rotation_policy": {
    "days_per_file": 7,
    "compress_after_days": 30,
    "archive_after_days": 365
  }
}
```

**Update frequency:** After every append (or batch append)

**Tests:**
- `test_metadata_consistency.py` — Metadata matches actual files

---

### 1.3 Integrate with ADR-0271 Feedback

**Modify:** `/home/shumway/projects/CorvinOS/operator/context_engineering/feedback.py`

**Changes:**
```python
# Old (ADR-0271 Week 3)
class ContextEvaluation:
    # ... fields ...
    def persist(self) -> None:
        """Save to ~/.corvin/cel-feedback/evaluations/"""
        pass

# New (Week 5 integration)
class ContextEvaluation:
    # ... same fields ...
    
    def to_learning_queue_record(self) -> LearningQueueRecord:
        """Convert to queue format."""
        pass
    
    def persist(self, use_queue: bool = True) -> None:
        """Save to learning queue (Tier 2) instead of old structure."""
        if use_queue:
            learning_queue = LearningQueue()
            record = self.to_learning_queue_record()
            learning_queue.append_record(record)
        else:
            # Backward compat: fall back to old storage
            pass
```

**Impact:** All ADR-0271 outcome tracking now feeds into Tier 2 queue.

**Tests:**
- `test_feedback_to_queue_conversion.py` — Lossless conversion

---

## Phase 2: Tier 3 Profile Aggregation (4 days, Week 5 Thu–Fri + Week 6 Mon–Tue)

### 2.1 Create `profile_aggregator.py` Module

**File:** `/home/shumway/projects/CorvinOS/operator/context_engineering/profile_aggregator.py`

**Purpose:** Read Tier 2 queue, compute aggregated profiles, write Tier 3.

**Core Components:**

```python
@dataclass
class ConfidenceUpdate:
    """Result of one Bayesian update."""
    context_id: str
    old_score: float
    new_score: float
    delta: float
    base_update: float
    learning_rate: float
    decay_weight: float

class ProfileAggregator:
    def __init__(self, queue_root: Path = None, profile_root: Path = None):
        self.queue = LearningQueue(queue_root)
        self.profile_root = profile_root or Path.home() / ".corvin" / "tenants" / "_default" / "profiles"
        self.profile_root.mkdir(parents=True, exist_ok=True)
    
    def run(self, backfill: bool = False) -> Dict[str, Any]:
        """Run full aggregation pipeline."""
        # 1. Read all records from queue
        # 2. Apply Bayesian updates to baseline
        # 3. Compute per-user profiles
        # 4. Discover patterns
        # 5. Write versioned profiles
        # 6. Update symlinks
        # Return: metadata about aggregation
        pass
    
    def _bayesian_update(
        self,
        context_id: str,
        old_confidence: float,
        evaluation: LearningQueueRecord,
        last_update_timestamp: datetime,
    ) -> ConfidenceUpdate:
        """Apply Bayesian update from ADR-0271."""
        # Implementation: from ADR-0271 learning.py
        pass
    
    def _apply_decay(self, timestamp: datetime) -> float:
        """Calculate decay weight based on age."""
        # >90d: 0.3×, >180d: 0.1×, recent: 1.0×
        pass
    
    def _discover_patterns(self, records: List[LearningQueueRecord]) -> List[Pattern]:
        """Find high-success context combinations."""
        pass
    
    def _compute_tenant_identity(self, profiles: Dict) -> Dict:
        """Extract specialization, markers, persona."""
        pass
    
    def _write_profiles(self, version: str, data: Dict) -> None:
        """Write and symlink profiles."""
        pass
```

**Key Algorithms:**

**Bayesian Update (from ADR-0271):**
```python
def _bayesian_update(...):
    base_update = 0.0
    
    if evaluation.impact == "CRITICAL":
        base_update += 0.10
    elif evaluation.impact == "helpful":
        base_update += 0.05
    elif evaluation.impact == "neutral":
        base_update -= 0.02
    elif evaluation.impact == "harmful":
        base_update -= 0.15
    
    # Calibration check
    if old_confidence >= 0.80 and evaluation.helpfulness < 0.5:
        base_update -= 0.10
    elif old_confidence <= 0.40 and evaluation.helpfulness >= 0.8:
        base_update += 0.08
    
    decay_weight = self._apply_decay(evaluation.timestamp)
    learning_rate = 0.05
    
    new_score = old_confidence + (base_update * learning_rate * decay_weight)
    new_score = max(0.0, min(1.0, new_score))
    
    return ConfidenceUpdate(
        context_id=evaluation.context_id,
        old_score=old_confidence,
        new_score=new_score,
        ...
    )
```

**Decay Function:**
```python
def _apply_decay(self, timestamp: datetime) -> float:
    age_days = (datetime.now() - timestamp).days
    
    if age_days <= 30:
        return 1.0
    elif age_days <= 90:
        return 0.7
    elif age_days <= 180:
        return 0.3
    else:
        return 0.1
```

**Pattern Discovery:**
```python
def _discover_patterns(self, records: List[LearningQueueRecord]) -> List[Pattern]:
    """Find context combos with high success rate."""
    # Group records by task_id
    # For each task, collect all (context_id, impact) pairs
    # Find combos: (context_id_1, context_id_2, ...) that appear together
    # Calculate success_rate = CRITICAL/helpful / total
    # Filter: keep only >0.80 success rate, >=5 occurrences
    pass
```

**Tenant Identity:**
```python
def _compute_tenant_identity(self, profiles: Dict) -> Dict:
    """Extract What makes this tenant unique."""
    return {
        "specialization": self._infer_specialization(profiles),
        "confidence": self._compute_identity_confidence(profiles),
        "markers": self._extract_markers(profiles),
    }
    
    # Specialization inference
    # "ML infrastructure" if ADR-0269/0267 >> 0.85 and others < 0.50
    # "DevOps heavy" if ADR-0201/0202 >> 0.80
    # etc.
```

**Output: Versioned Profiles**
```
~/.corvin/tenants/_default/profiles/
├─ tenant-baseline.json → symlink to v202608071800
├─ tenant-baseline.v202608071400.json
├─ tenant-baseline.v202608071800.json (new)
│
├─ user-shumway.json → symlink
├─ user-shumway.v202608071400.json
├─ user-shumway.v202608071800.json (new)
│
├─ patterns-adr-skill-combos.v202608071800.json
├─ patterns-danger-zones.v202608071800.json
│
└─ _metadata.json (updated with new versions)
```

**Tests:**
- `test_bayesian_update_formula.py` — Math correct
- `test_decay_calculation.py` — Age-based decay
- `test_pattern_discovery.py` — Patterns discovered correctly
- `test_profile_generation.py` — Profiles match expectations
- `test_symlink_management.py` — Symlinks switched correctly
- `test_identity_inference.py` — Tenant identity reasonable

**Acceptance Criteria:**
- [ ] Bayesian update matches hand-calculated values (5 test cases)
- [ ] Decay applied correctly (age 0/90/180/365 days)
- [ ] Patterns discovered (>0.80 success rate, >=5 occurrences)
- [ ] Symlinks updated atomically (no broken symlinks during switch)
- [ ] Profiles valid JSON (schema validation)

---

### 2.2 Storage Schema: Tier 3 Profile Format

**File:** `~/.corvin/tenants/_default/profiles/tenant-baseline.v{timestamp}.json`

**Schema** (comprehensive):
```json
{
  "version": "202608071800",
  "computed_at": "2026-08-07T18:00:00Z",
  "source_tasks": 1243,
  "source_users": 47,
  "source_records_processed": 3100,
  
  "confidence_scores": {
    "adr-0269": {
      "combined": 0.92,
      "relevance": 0.95,
      "reliability": 0.90,
      "freshness": 0.88,
      "tier": "HIGH",
      "samples": 310,
      "confidence": 0.89,
      "last_updated": "2026-08-07T17:45:00Z",
      "trend": "stable"
    }
  },
  
  "patterns": [
    {
      "id": "pattern-0001",
      "context_combo": ["adr-0269", "skill-e2e-wiring", "memory-phase3"],
      "success_rate": 0.95,
      "frequency": 87,
      "confidence": 0.89,
      "last_seen": "2026-08-07T14:30:00Z",
      "sample_tasks": ["task-abc", "task-def", ...]
    }
  ],
  
  "user_style_distribution": {
    "pragmatic_pct": 0.65,
    "rigorous_pct": 0.30,
    "balanced_pct": 0.05
  },
  
  "language_distribution": {
    "de": 0.72,
    "en": 0.28
  },
  
  "tenant_identity": {
    "specialization": "ML infrastructure optimization",
    "confidence": 0.78,
    "markers": [
      "High ADR-0269/0267 usage",
      "Predominantly pragmatic",
      "German-speaking",
      "E2E testing critical"
    ]
  },
  
  "metadata": {
    "next_aggregation": "2026-08-08T02:00:00Z",
    "checksum": "sha256:abc123..."
  }
}
```

**Per-User Profile Schema** (`user-{user_id}.v{timestamp}.json`):
```json
{
  "user_id": "shumway",
  "version": "202608071800",
  "computed_at": "2026-08-07T18:00:00Z",
  
  "task_count": 320,
  "success_rate": 0.89,
  
  "decision_style": "pragmatic",
  "language": "de",
  "detail_level": "summary",
  
  "learned_preferences": {
    "care_about": ["production-ready", "testing", "security"],
    "avoid": ["manual-processes"],
    "tolerate": ["WIP-code"]
  },
  
  "adr_affinity": {
    "adr-0269": 0.96,
    "adr-0267": 0.88
  },
  
  "patterns_user_succeeded": [
    ("adr-0269", "skill-e2e-wiring") → 0.97
  ],
  
  "danger_zones": [
    "Skipping tests when urgent (70% fail)"
  ]
}
```

---

## Phase 3: Integration with TaskEngine (2 days, Week 6 Wed–Thu)

### 3.1 Modify `engine.py` to Load and Cache Tier 3

**File:** `/home/shumway/projects/CorvinOS/operator/task_analysis/engine.py`

**Changes:**
```python
class TaskEngine:
    def __init__(self, tenant_id: str = "_default"):
        # ... existing init ...
        
        # NEW: Load Tier 3 profiles into Tier 1 cache
        self.tenant_learning = TenantLearningCache(tenant_id)
        self.cel_confidence = self.tenant_learning.load_profiles()  # In-memory cache
    
    def route_task(self, task: Task) -> RichTaskBrief:
        # ... existing Phase 5.5a/b/c code ...
        
        # MODIFIED: Use Tier 1 cache for confidence scoring
        for match in memory_matches:
            confidence = self.cel_confidence.get("memory", match.id)  # Fast lookup
            match.attach_confidence(confidence)
        
        for decision in related_decisions:
            confidence = self.cel_confidence.get("adr", decision.id)
            decision.attach_confidence(confidence)
        
        # ... rest of phase ...
        
        # NEW: Track which context items are used (for feedback collection)
        brief.tracked_context_usage = self.cel_confidence.start_tracking()
        
        return brief

class TenantLearningCache:
    """Tier 1 cache: in-memory profile loaded at boot."""
    
    def __init__(self, tenant_id: str):
        self.tenant_id = tenant_id
        self.profiles = {}
        self.tracking = []  # For feedback collection
    
    def load_profiles(self) -> Dict:
        """Load latest Tier 3 profiles from disk."""
        profile_root = Path.home() / ".corvin" / "tenants" / tenant_id / "profiles"
        
        # Load symlinks (latest versions)
        baseline = self._load_json(profile_root / "tenant-baseline.json")
        self.profiles["baseline"] = baseline
        
        # Load per-user profile (if exists)
        user_id = os.getenv("CORVIN_USER_ID", "default")
        user_profile_path = profile_root / f"user-{user_id}.json"
        if user_profile_path.exists():
            self.profiles[f"user-{user_id}"] = self._load_json(user_profile_path)
        
        return self.profiles
    
    def get(self, item_type: str, item_id: str) -> float:
        """Get confidence score [0.0-1.0] for item."""
        # First check user profile (if exists)
        # Fall back to baseline
        # Default to 0.70 if not found
        pass
    
    def start_tracking(self) -> ContextUsageTracker:
        """Create tracker for this task's context usage."""
        return ContextUsageTracker(self.profiles)

class ContextUsageTracker:
    """Track which context items are used during task execution."""
    
    def track_usage(self, context_id: str, used: bool, reasoning: str) -> None:
        """Record that context item was (or wasn't) used."""
        pass
    
    def get_usage_records(self) -> List[ContextUsage]:
        """Finalize tracking, return records for feedback collection."""
        pass
```

**Key points:**
- ✅ Load latest profiles from Tier 3 at boot
- ✅ Cache in RAM (Tier 1)
- ✅ O(1) confidence lookups during task execution
- ✅ Track which context was actually used (for feedback loop)

**Tests:**
- `test_cache_loading.py` — Profiles load correctly
- `test_cache_lookup_speed.py` — <10ms per lookup
- `test_usage_tracking.py` — Usage tracked correctly

---

### 3.2 Modify ADR-0271 Feedback Collection

**File:** `/home/shumway/projects/CorvinOS/operator/context_engineering/feedback.py`

**Changes:**
```python
class TaskOutcome:
    # ... existing fields ...
    
    def finalize(self, usage_tracker: ContextUsageTracker) -> None:
        """Collect tracked context usage."""
        self.context_usages = usage_tracker.get_usage_records()
    
    def persist(self, learning_queue: LearningQueue) -> None:
        """Convert outcome to evaluations, append to Tier 2 queue."""
        for usage in self.context_usages:
            evaluation = self._create_evaluation_from_usage(usage)
            
            # Append to Tier 2
            record = evaluation.to_learning_queue_record()
            learning_queue.append_record(record)
```

**Flow:**
```
Task execution:
  1. TaskEngine loads Tier 3 cache
  2. starts ContextUsageTracker
  3. Agent uses context, tracker logs
  
Task completion:
  4. Agent reports outcome
  5. Outcome.finalize() collects tracked usage
  6. Outcome.persist() converts to evaluations
  7. Evaluations appended to Tier 2 queue
  8. Next aggregation reads Tier 2 → updates Tier 3
  9. Next session loads improved Tier 3
```

---

## Phase 4: Automation & Operations (1.5 days, Week 6 Fri)

### 4.1 Create Aggregation Cron Job

**File:** `~/.corvin/cron/cel-aggregation.sh` (or systemd timer)

**Script:**
```bash
#!/bin/bash
set -e

TENANT_ID="${CORVIN_TENANT_ID:-_default}"
TIMESTAMP=$(date -u +%Y%m%d%H%M)

echo "[$(date -u)] Starting CEL aggregation for tenant: $TENANT_ID"

cd /home/shumway/projects/CorvinOS

# Run aggregator
python -m operator.context_engineering.profile_aggregator \
    --tenant-id "$TENANT_ID" \
    --timestamp "$TIMESTAMP" \
    --log-level INFO \
    >> ~/.corvin/logs/cel-aggregation.log 2>&1

if [ $? -eq 0 ]; then
    echo "[$(date -u)] CEL aggregation completed successfully"
    exit 0
else
    echo "[$(date -u)] CEL aggregation FAILED"
    exit 1
fi
```

**Cron configuration:**
```
# Run nightly aggregation at 2:00 UTC
0 2 * * * /home/shumway/.corvin/cron/cel-aggregation.sh
```

**Or systemd timer:**
```
# ~/.corvin/systemd/cel-aggregation.timer
[Unit]
Description=CEL Profile Aggregation
After=network.target

[Timer]
OnCalendar=*-*-* 02:00:00
Persistent=true

[Install]
WantedBy=timers.target

---

# ~/.corvin/systemd/cel-aggregation.service
[Unit]
Description=CEL Profile Aggregation Job

[Service]
Type=oneshot
ExecStart=/home/shumway/.corvin/cron/cel-aggregation.sh
StandardOutput=journal
StandardError=journal
```

**Tests:**
- `test_cron_job_execution.py` — Script runs without error
- `test_aggregation_idempotent.py` — Running twice produces same result

---

### 4.2 Create Monitoring & Alerting

**File:** `/home/shumway/projects/CorvinOS/operator/monitoring/cel_monitoring.py`

**Checks:**
```python
class CELMonitoring:
    def check_aggregation_freshness(self) -> Dict[str, Any]:
        """Alert if profiles >24h old."""
        profile_root = self._get_profile_root()
        metadata = self._load_metadata(profile_root)
        
        last_computed = datetime.fromisoformat(metadata["computed_at"])
        age_hours = (datetime.now() - last_computed).total_seconds() / 3600
        
        return {
            "status": "OK" if age_hours < 24 else "ALERT",
            "age_hours": age_hours,
            "last_aggregation": last_computed,
        }
    
    def check_queue_growth(self) -> Dict[str, Any]:
        """Monitor queue size for anomalies."""
        queue = LearningQueue()
        count = queue.get_record_count()
        
        # Estimate: ~3 records per task, ~100 tasks/day
        expected_daily = 300
        
        return {
            "total_records": count,
            "expected_daily": expected_daily,
            "anomaly": count > expected_daily * 10,  # Alert if 10× normal
        }
    
    def check_profile_validity(self) -> Dict[str, Any]:
        """Validate profiles are parseable, contain expected fields."""
        pass
    
    def report_health(self) -> None:
        """Log all checks."""
        checks = {
            "aggregation_freshness": self.check_aggregation_freshness(),
            "queue_growth": self.check_queue_growth(),
            "profile_validity": self.check_profile_validity(),
        }
        
        # Log to monitoring system (Prometheus, etc.)
        # Alert if any check is ALERT
        return checks
```

**Alert Rules:**
```yaml
# prometheus/rules/cel-learning.yml
groups:
  - name: cel-learning
    interval: 5m
    rules:
      - alert: CELProfilesStale
        expr: (time() - cel_last_aggregation_timestamp) > 86400
        for: 1h
        annotations:
          summary: "CEL profiles not updated in >24h"
          runbook: "Check cel-aggregation cron job status"
      
      - alert: CELQueueGrowthAnomaly
        expr: rate(cel_queue_records_total[1d]) > 10 * 300
        for: 30m
        annotations:
          summary: "CEL queue growing 10× normal rate"
          runbook: "Check for feedback loop explosion"
```

---

### 4.3 Create GC & Retention Policy

**File:** `/home/shumway/projects/CorvinOS/operator/context_engineering/profile_gc.py`

**Policy:**
```python
class ProfileGarbageCollector:
    RETENTION_POLICY = {
        "max_versions_per_profile": 12,
        "min_age_for_archive": timedelta(days=30),
        "archive_format": "tar.gz",
        "archive_location": "~/.corvin/archive/profiles/",
        "min_age_for_deletion": timedelta(days=365),
    }
    
    def run(self) -> Dict[str, int]:
        """Clean up old profile versions."""
        profile_root = self._get_profile_root()
        
        # For each profile type (tenant-baseline, user-*, patterns-*)
        for profile_file in profile_root.glob("*.json"):
            versions = self._find_versions(profile_file)
            
            # Keep only most recent N
            to_delete = versions[self.RETENTION_POLICY["max_versions_per_profile"]:]
            for old_version in to_delete:
                age = datetime.now() - old_version.stat().st_mtime
                if age > self.RETENTION_POLICY["min_age_for_deletion"]:
                    old_version.unlink()  # Delete
                elif age > self.RETENTION_POLICY["min_age_for_archive"]:
                    self._archive(old_version)  # Compress & move
        
        return {
            "deleted": len(to_delete_deleted),
            "archived": len(to_delete_archived),
        }
```

**Schedule:**
```
# Weekly GC
0 3 * * 0 /home/shumway/.corvin/cron/cel-gc.sh
```

---

## Phase 5: Integration Testing (1.5 days, Week 6 + early Week 7)

### 5.1 End-to-End Test: Full Loop

**File:** `/home/shumway/projects/CorvinOS/operator/context_engineering/tests/test_tier_integration_e2e.py`

**Test Scenario:**
```python
def test_full_loop_e2e():
    """Session → usage tracking → queue → aggregation → reload."""
    
    # Setup
    with tempfile.TemporaryDirectory() as tmpdir:
        queue_root = Path(tmpdir) / "queue"
        profile_root = Path(tmpdir) / "profiles"
        
        # Phase 1: Simulate first session
        # - Load (empty) Tier 3
        # - Execute task 1-5
        # - Collect 5 tasks × 3 evaluations = 15 records
        # - Append to Tier 2 queue
        
        queue = LearningQueue(queue_root)
        for i in range(15):
            record = create_test_record(i)
            queue.append_record(record)
        
        assert queue.get_record_count() == 15
        
        # Phase 2: Run aggregation
        aggregator = ProfileAggregator(queue_root, profile_root)
        metadata = aggregator.run()
        
        assert metadata["profiles_written"] >= 2  # >=tenant-baseline + patterns
        assert (profile_root / "tenant-baseline.json").exists()  # Symlink
        
        # Phase 3: Load new profiles
        cache = TenantLearningCache()
        profiles = cache.load_profiles()
        
        assert len(profiles) > 0
        assert "baseline" in profiles
        
        # Phase 4: Verify confidence changed
        old_score = 0.70  # Default
        new_score = cache.get("adr", "adr-0269")
        assert new_score != old_score  # Learning happened
```

**Test Cases:**
- ✅ Empty queue at start
- ✅ Records append correctly
- ✅ Aggregation computes profiles
- ✅ Symlinks updated
- ✅ Cache loads new profiles
- ✅ Confidence scores changed via Bayesian update
- ✅ Concurrent sessions don't corrupt queue
- ✅ Profiles remain consistent across reload

---

### 5.2 Compliance Test: GDPR Audit Trail

**File:** `/home/shumway/projects/CorvinOS/operator/context_engineering/tests/test_compliance_audit.py`

**Test:**
```python
def test_gdpr_audit_trail():
    """Verify Tier 2 queue provides GDPR-compliant audit trail."""
    
    queue = LearningQueue()
    
    # Scenario: User "alice" executes 2 tasks
    records = [
        LearningQueueRecord(
            context_id="adr-0269",
            user_id="alice",
            task_id="task-1",
            timestamp=datetime(2026, 8, 7, 10, 0),
            ...
        ),
        ...
    ]
    
    for record in records:
        queue.append_record(record)
    
    # Audit query: What did we know about user "alice" on 2026-08-07?
    alice_records = queue.read_all_records()
    alice_records = [r for r in alice_records if r.user_id == "alice"]
    
    # Verify:
    # 1. Records are immutable (can't modify historical feedback)
    # 2. Timestamps clear (when feedback was collected)
    # 3. User consent can be queried (user_id matches consent log)
    # 4. No undisclosed behavior profiling (only outcome-focused, not behavior)
    
    assert len(alice_records) == len(records)
    assert all(r.user_id == "alice" for r in alice_records)
    assert all(isinstance(r.timestamp, datetime) for r in alice_records)
```

---

## Phase 6: Measurement & Calibration (1.5 days, Week 7)

### 6.1 Measurement Suite

**File:** `/home/shumway/projects/CorvinOS/operator/context_engineering/measurement/cel_measurement.py`

**Key Metrics:**

```python
class CELMeasurement:
    def measure_calibration(self) -> Dict:
        """Calibration: if we say 85%, do 85% succeed?"""
        profiles = load_profiles()
        outcomes = load_all_outcomes()
        
        # Bucket outcomes by predicted confidence
        buckets = defaultdict(list)
        for outcome in outcomes:
            confidence = profiles.get(outcome.context_id, 0.70)
            confidence_bucket = round(confidence * 10) / 10  # 0.7, 0.8, 0.9
            buckets[confidence_bucket].append(outcome.success)
        
        # Calculate actual success per bucket
        calibration = {}
        for bucket, successes in buckets.items():
            actual = sum(successes) / len(successes)
            error = abs(bucket - actual)
            calibration[bucket] = {
                "predicted": bucket,
                "actual": actual,
                "error": error,
                "count": len(successes),
            }
        
        avg_error = mean(c["error"] for c in calibration.values())
        return {
            "calibration": calibration,
            "avg_error": avg_error,
            "target_error": 0.05,
            "status": "OK" if avg_error < 0.05 else "NEEDS_TUNING",
        }
    
    def measure_learning_curve(self) -> Dict:
        """How fast do scores stabilize?"""
        # Track one context (e.g., adr-0269)
        # Measure score after 1, 2, 5, 10, 20 tasks
        # Compute variance at each point
        # Target: <10 tasks to stabilize
        pass
    
    def measure_pattern_accuracy(self) -> Dict:
        """Do patterns match manual observation?"""
        # Have human rate top 5 patterns
        # Check agreement
        pass
    
    def measure_adoption(self) -> Dict:
        """Do sessions use recommended context?"""
        # Count: how often recommended items are actually used
        # Target: 90%
        pass
```

**Target Metrics:**
| Metric | Target | Acceptable |
|--------|--------|------------|
| Calibration error | ±5% | ±10% |
| Learning stabilization | <10 tasks | <20 tasks |
| Pattern accuracy | >80% | >70% |
| Context adoption | >90% | >80% |

---

## Deployment Checklist

### Pre-Deployment
- [ ] All tests passing (unit + integration + e2e)
- [ ] Performance baseline established (aggregation time, memory usage)
- [ ] GDPR/compliance review approved
- [ ] Documentation complete (schema, policies, recovery)
- [ ] Monitoring/alerting configured
- [ ] Cron/systemd timer tested
- [ ] GC policy tested

### Deployment
- [ ] Create Tier 2 queue directory
- [ ] Create Tier 3 profiles directory
- [ ] Initialize _metadata.json
- [ ] Deploy aggregator.py
- [ ] Deploy monitoring
- [ ] Enable cron job / systemd timer
- [ ] Set up backup (includes learning-queue + profiles)

### Post-Deployment
- [ ] Monitor aggregation for 1 week
- [ ] Verify profiles updating daily
- [ ] Verify alerts firing correctly
- [ ] Measure calibration (target: ±5%)
- [ ] Measure learning curve (target: <10 tasks)
- [ ] Adjust tuning parameters if needed

---

## Rollback Plan

**If profiles corrupt or drift wrong:**

1. **Immediate:** Symlink to previous version
   ```bash
   cd ~/.corvin/tenants/_default/profiles
   ln -sf tenant-baseline.v{previous}.json tenant-baseline.json
   ```

2. **Verify:** Load old profiles, check confidence scores

3. **Regenerate:** Run aggregator on old queue
   ```bash
   python aggregator.py --backfill --to-version {previous}
   ```

4. **Root cause:** Debug aggregator (Bayesian update logic?)

---

## Timeline Summary

| Phase | Duration | Tasks |
|-------|----------|-------|
| **P1: Tier 2 Queue** | 3 days | learning_queue.py, metadata, ADR-0271 integration |
| **P2: Tier 3 Profiles** | 4 days | aggregator.py, Bayesian updates, pattern discovery |
| **P3: TaskEngine Integration** | 2 days | Load Tier 3, cache, usage tracking |
| **P4: Automation** | 1.5 days | Cron job, monitoring, GC |
| **P5: Testing** | 1.5 days | E2E tests, compliance audit |
| **P6: Measurement** | 1.5 days | Calibration, learning curve, metrics |
| **Total** | ~2 weeks | Week 5–6 (after CEL Phase 4 Week 4) |

---

## Success Criteria

1. **Tier 2 queue operational** — All outcomes appended immutably
2. **Tier 3 profiles generated** — Daily aggregation produces versioned profiles
3. **Tier 1 cache working** — Sessions load profiles, confidence <10ms per lookup
4. **Feedback loop closed** — Task outcome → queue → aggregation → improved scores
5. **Tenant identity emerging** — By Week 6, profiles show specialization
6. **Compliant** — GDPR audit trail verified
7. **Monitored** — Alerts firing, freshness checked daily
8. **Measured** — Calibration ±5%, learning curve <10 tasks

---

**Next Step:** After CEL Phase 4 Week 4, begin Phase 1 (Tier 2 queue infrastructure).

