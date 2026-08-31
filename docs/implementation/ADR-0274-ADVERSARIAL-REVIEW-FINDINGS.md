# ADR-0274 Adversarial Review: Critical Findings & Amendments

**Date:** 2026-08-07  
**Reviewers:** System Architecture Critic + Learning System LLM Perspective  
**Status:** Proposed Amendments to ADR-0274 before implementation

---

## Executive Summary

**The three-tier architecture is sound.** But four critical execution gaps and missing learning infrastructure will cause production failures.

**Verdict:** Implement MUST-FIX items (C1–C4, H1–H3) before Week 6 deployment. Architecture remains viable with these additions.

---

## CRITICAL GAPS (System breaks without fixes)

### C1: Tier 2 Queue Corruption — No Recovery Specification

**Problem:** Plan says "validate checksums (fail fast if corrupted)" but not what happens when they fail.

**Consequence:** Silent data loss cascades into Tier 3. Profiles degrade without operator knowledge.

**Fix:**
```python
# Explicit corruption handling
def read_all_records(self, skip_corrupt=True):
    """Read with corruption detection: if corrupt, log + skip + alert."""
    for line in file:
        try:
            record = json.loads(line)
            if not self._verify_checksum(record):
                logger.warning(f"Corrupted: {record['task_id']}")
                metrics.increment("queue.record_corrupted")
                if skip_corrupt:
                    continue  # Fail-safe: skip, don't stop
        except Exception as e:
            logger.error(f"Parse failed: {e}")
            metrics.increment("queue.record_parse_error")
            # Continue based on policy
```

**Complexity:** Low (1–2 days)  
**Priority:** CRITICAL — Deploy before Week 6

---

### C2: Aggregation ↔ Active Sessions — Race Condition

**Problem:** Aggregator runs 2am–5am. Session starts 2:30am. What version do they load? What records does aggregator see?

**Consequence:** 
- Sessions load profiles from arbitrary point in aggregation pipeline
- New feedback might be skipped or double-counted
- Profiles become inconsistent

**Fix (Concurrency Model):**
```
Explicit contract:
- Aggregator runs nightly 2:00–3:00 UTC
- Sessions may append during aggregation
- Aggregator reads queue files PRESENT AT 2:00 (not new appends after)
- Records appended after 2:00 processed in NEXT nightly run
- Consequence: max 24h stale profiles, guaranteed

Implementation:
- Add file locking to queue appends (write-lock)
- Aggregator acquires read-lock on queue files at start
- Checkpointing: resume from last-processed record if crash
```

**Complexity:** Medium (3–4 days)  
**Priority:** CRITICAL — Deploy before Week 6

---

### C3: Symlink Update Not Atomic

**Problem:** Aggregator writes `v202608071800.json`, then does `ln -sf v202608071800.json tenant-baseline.json`. Between write and ln, session loads broken symlink.

**Consequence:** Broken symlinks during aggregation; sessions fail to load profiles; cascade to crashes.

**Fix:**
```python
def write_profiles(self, version: str, data: Dict) -> None:
    """Atomic profile switching via temp symlink."""
    new_file = self.profile_root / f"tenant-baseline.v{version}.json"
    with open(new_file, 'w') as f:
        json.dump(data, f)
    
    # Atomic symlink switch (POSIX atomic rename)
    temp_link = self.profile_root / "tenant-baseline.json.tmp"
    if temp_link.exists():
        temp_link.unlink()
    
    os.symlink(f"tenant-baseline.v{version}.json", temp_link)
    os.rename(temp_link, self.profile_root / "tenant-baseline.json")
    # Now: symlink points to new version atomically
```

**Complexity:** Low (1 day)  
**Priority:** CRITICAL — Deploy before Week 6

---

### C4: Closed Feedback Loop — Danger Zones Not Enforced

**Problem:** Profiles mark `"danger_zones": ["skipping tests when urgent (70% fail)"]` but agent can still do it anyway.

**Consequence:** Learning is passive observation, not active control. Same mistakes repeat. System learns but doesn't improve.

**Fix:**
```python
# TaskEngine: use danger zones to GUIDE, not just log
class DangerZoneGuard:
    def should_use_context(self, context_id: str, conditions: Dict) -> Tuple[bool, Optional[str]]:
        """Check if context is in danger zone for these conditions."""
        profile = self.profiles.get(user_id)
        
        for danger_zone in profile.get("danger_zones", []):
            if self._matches_pattern(danger_zone, conditions):
                # Block this context in these conditions
                logger.warning(f"Danger zone: {context_id} blocked in {conditions}")
                metrics.increment("danger_zone.blocked")
                return False, f"Danger zone: {danger_zone}"
        
        return True, None

# In routing:
brief = engine.route_task(task)
for context in brief.recommended_context:
    allowed, reason = danger_guard.should_use_context(
        context.id,
        {"task_type": task.type, "urgency": task.urgency}
    )
    if not allowed:
        brief.remove_context(context.id)
```

**Complexity:** Medium (2–3 days)  
**Priority:** CRITICAL — Deploy before Week 6

---

## HIGH-PRIORITY GAPS (Degrade reliability, learning, or fairness)

### H1: Contradictory Feedback Thrashes Confidence

**Problem:** Context helps 70% of tasks, hurts 30%. Bayesian update treats all signals equally → score thrashes wildly.

**Fix (Task-Type Bucketing):**
```python
@dataclass
class ContextScoreByTaskType:
    """Separate scores per task type to detect contradictions."""
    ml_tasks: float = 0.70
    devops_tasks: float = 0.70
    data_tasks: float = 0.70
    
    def contradiction_score(self) -> float:
        """High if scores diverge across types."""
        scores = [self.ml_tasks, self.devops_tasks, self.data_tasks]
        return np.std(scores)  # High = contradiction

# Aggregator detects: if contradiction_score > 0.20, mark "conditional"
# Emit warning to operator
```

**Complexity:** Medium (3–4 days)  
**Priority:** HIGH — Deploy before Week 6

---

### H2: Staleness Window (24h) — No On-Demand Refresh

**Problem:** Profiles refresh nightly only. If critical context changes, 24h delay before it's reflected.

**Fix:**
```bash
# New CLI command
corvin learning aggregate --now

# Also auto-trigger if:
# - freshness < 24h AND queue_records > 500
# - i.e., significant new data, aggregate early
```

**Complexity:** Low (1–2 days)  
**Priority:** HIGH — Deploy before Week 6

---

### H3: Aggregation Failure = Silent Slow Degradation

**Problem:** Aggregator crashes at 3:45am. Alert fires. Nobody looks at 2am logs. Profiles stale for days.

**Fix:**
```bash
#!/bin/bash
# cel-aggregation.sh with timeout + fallback

TIMEOUT_SECONDS=1800  # 30 minutes max

timeout $TIMEOUT_SECONDS python -m operator.context_engineering.profile_aggregator \
    --tenant-id "$TENANT_ID" \
    >> ~/.corvin/logs/cel-aggregation.log 2>&1

EXIT_CODE=$?

if [ $EXIT_CODE -eq 124 ]; then
    # Timeout: aggregation took >30 min
    echo "[$(date -u)] ALERT: Aggregation timeout"
    /usr/local/bin/alert-operator "CEL aggregation timeout"
    exit 1
elif [ $EXIT_CODE -ne 0 ]; then
    echo "[$(date -u)] ALERT: Aggregation failed"
    /usr/local/bin/alert-operator "CEL aggregation failed"
    exit 1
fi
```

**Complexity:** Low (1–2 days)  
**Priority:** HIGH — Deploy before Week 6

---

## LEARNING INFRASTRUCTURE GAPS (What the LLM needs)

### L1: Ground-Truth Feedback Source Specification (CRITICAL)

**What I need:** Clear definition of how feedback is marked and by whom.

**Current gap:** Plan says `"impact": "CRITICAL"` but not:
- Who decides? (Human? Automated?)
- Frequency? (Every task? Sampling?)
- Noise model? (What's the error rate?)

**Why:** If feedback is 30% noisy, I'll learn wrong patterns. I need to know the signal-to-noise ratio.

**Fix:** Document feedback source in ADR:
```
Feedback collection strategy:
- Agent auto-detects success/failure (structured outcome)
- Human reviews controversial outcomes (sampled)
- Impact assigned via rubric: CRITICAL/helpful/neutral/harmful
- Noise estimate: <5% based on calibration tests
```

---

### L2: Confidence Intervals, Not Point Scores (HIGH)

**What I need:** Uncertainty bands with each score.

**Current gap:** Profile says `"adr-0269": 0.92` but is that ±0.05 or ±0.20?

**Why:** 
- If ±0.05, I should trust it (95% likely 0.87–0.97)
- If ±0.20, I should allocate 50% attention and keep alternatives
- Without this, I can't allocate attention correctly

**Fix:**
```json
"adr-0269": {
    "mean": 0.92,
    "std_dev": 0.08,
    "samples": 310,
    "confidence_interval_95": [0.77, 1.00],
    "reliability": "HIGH"  // std_dev < 0.10
}
```

**Complexity:** Medium (2–3 days)  
**Priority:** HIGH — Deploy before Week 7

---

### L3: Conditional Patterns, Not Isolated Scores (HIGH)

**What I need:** Interactions. "Use A+B together (0.95 success) but NOT A+C (0.20 success)".

**Current gap:** Patterns show simple combos but don't show:
- What if task is urgent? Success drops to 0.60?
- What if user is rigorous? Does it stay 0.95?
- What if it's DevOps vs. ML?

**Why:** Single scores are misleading. Context interactions are the real story.

**Fix:**
```python
# Discover conditional patterns
patterns = [
    {
        "combo": ["adr-0269", "skill-e2e"],
        "success_rate": 0.95,
        "conditions": {
            "task_type": "ml",  # Only works for ML
            "urgency": "normal"  # Not when urgent
        },
        "notes": "Critical for ML; risky when fast-tracked"
    }
]
```

**Complexity:** Medium (3–4 days)  
**Priority:** HIGH — Deploy before Week 7

---

### L4: Contradiction Handling Strategy (HIGH)

**What I need:** "This context helps 70% of tasks, hurts 30%. What's the pattern?"

**Current gap:** No detection of contradictions. Bayesian update just averages (thrashing).

**Why:** To know when NOT to use a context.

**Fix:** Explicit strategy + detection (tied to H1 above).

---

### L5: Explainability (Decision Provenance) (MEDIUM)

**What I need:** "Score went from 0.85 → 0.90. Why?"

**Current gap:** Profile shows final score, no trace.

**Why:** Without explanation, I can't debug if I'm using context wrong.

**Fix:**
```json
"adr-0269": {
    "score": 0.92,
    "history": [
        {"timestamp": "2026-08-07T10:00Z", "old": 0.85, "new": 0.88, "delta": 0.03, "reason": "1 CRITICAL outcome"},
        {"timestamp": "2026-08-07T14:00Z", "old": 0.88, "new": 0.90, "delta": 0.02, "reason": "2 helpful outcomes"}
    ]
}
```

**Complexity:** Low (1–2 days)  
**Priority:** MEDIUM — Deploy before Week 7

---

### L6–L12: Additional LLM Needs (MEDIUM Priority)

**L6. Multi-Context Conditional Logic**  
Need anti-patterns explicitly marked. ("Avoid A+C combo")

**L7. Per-Prediction Accuracy Feedback**  
"You estimated 0.92, actual was 0.78. Error = 0.14." → Calibration metrics.

**L8. Override Capability**  
"I disagree with my learned preference. Override for this task."

**L9. Temporal Continuity Semantics**  
Clear model: learning shared across sessions? Or per-session?

**L10. Freshness Awareness in Cache**  
Cache has no timestamp. Don't know if 1h or 20h old.

**L11. User Consent Renewal**  
Before applying learned preferences, ask: "Does this match how you see yourself?"

**L12. Warm-Up Signals**  
"This context is new, first 10 tasks are calibration."

---

## DESIGN ASSUMPTIONS UNDER-SPECIFIED

### D1: Multi-Tenant Not Addressed

**Problem:** Entire design assumes `_default` tenant only. Paths hardcoded: `~/.corvin/tenants/_default/profiles/`

**Impact:** Multi-tenant deployments conflict; can't isolate learning per tenant.

**Fix:** Parameterize tenant in all paths. Per-tenant aggregation jobs.

**Complexity:** Low (1–2 days)  
**Priority:** MEDIUM — Needed for enterprise use cases

---

### D2: Cold-Start Defaults Arbitrary

**Problem:** Default score 0.70 is hardcoded, unjustified.

**Fix:** Infer prior from related contexts. If ADR-0269 is related to ADR-0268 (both context engineering), use ADR-0268's score as prior.

**Complexity:** Low (1–2 days)  
**Priority:** MEDIUM — Deploy before Week 7

---

### D3: GC Policy Ambiguous (Version Retention)

**Problem:** `max_versions: 12` AND `min_age: 30d` conflict. What if 15 versions exist and oldest is 25d old?

**Fix:**
```python
PROFILE_GC_POLICY = {
    "keep_all_if_under_30_days": True,  # Apply first
    "keep_min_N_versions": 12,          # Apply second
    "delete_if_over_365_days": True,    # Apply third
}
```

**Complexity:** Low (1–2 days)  
**Priority:** LOW — Documentation only, behavior inherent

---

## RANKED FIX PRIORITY

### **MUST FIX BEFORE WEEK 6 DEPLOYMENT**
1. **C1: Atomic Queue + Recovery** — Corruption detection + fail-safe
2. **C2: Concurrency Model** — Aggregation ↔ Sessions contract + locking
3. **C3: Atomic Symlink** — POSIX atomic rename
4. **C4: Closed Feedback Loop** — Danger zones enforced
5. **H1: Contradiction Detection** — Task-type bucketing
6. **H2: On-Demand Aggregation** — `corvin learning aggregate --now`
7. **H3: Aggregation Timeout** — Bounded latency + fallback

### **SHOULD FIX BEFORE MEASUREMENT PHASE (WEEK 7)**
8. **H4: Confidence Intervals** — Epistemological correctness
9. **H5: Contradiction Strategy** — Explicit policy
10. **L5: Decision Provenance** — Score history logged
11. **M1: Calibrated Defaults** — Prior inference

### **NICE-TO-HAVE (After MVP)**
12. **D1: Multi-Tenant** — Enterprise support
13. **L6–L12: Advanced LLM Features** — Conditional patterns, overrides, consent

---

## RECOMMENDATION

**Proceed with ADR-0274 implementation.** The three-tier architecture is fundamentally sound. But implement all **MUST-FIX** items (C1–C4, H1–H3) before Week 6 deployment. Address **SHOULD-FIX** items during Week 7 measurement phase.

**Do NOT deploy without:**
- ✅ C1–C4 implemented
- ✅ H1–H3 implemented
- ✅ All tests passing (including concurrency tests)
- ✅ Runbook for aggregation failure documented

**The LLM perspective revealed:** Learning infrastructure is incomplete. Scores alone are insufficient. System needs uncertainty quantification, contradiction detection, and decision provenance. Recommend addressing L4–L5 before using tenant-learning in production.

