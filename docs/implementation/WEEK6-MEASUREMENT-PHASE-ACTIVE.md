# Week 6 Measurement Phase ACTIVE

**Status:** 🟢 LIVE  
**Started:** 2026-08-08 17:00 UTC  
**End Date:** 2026-08-17  
**Operator:** Journey Test User (single-operator instance)

---

## Deployment Decision

### Why 100% Direct Rollout (No Canary)
- ✅ Single operator (no multi-user conflicts)
- ✅ Local instance (safe to experiment)
- ✅ Backups secured (rollback available)
- ✅ All tests passing (zero regressions)
- ✅ More data volume (accelerates learning)

**Decision:** Skip canary, deploy 100%, start measurement immediately.

---

## Measurement Phase Status

### All 4 ADR Tracks LIVE

| Track | Target | Status | Data File |
|-------|--------|--------|-----------|
| **ADR-0270** Uncertainty Quantification | ≥100 predictions | 🟢 Recording | predictions.jsonl |
| **ADR-0271** Feedback Loop | ≥100 feedback records | 🟢 Recording | feedback.jsonl |
| **ADR-0272** User Preferences | ≥100 user choices | 🟢 Recording | user_choices.jsonl |
| **ADR-0273** Attention Budget | ≥100 allocations | 🟢 Recording | budget_allocations.jsonl |

### Environment Variables Set
```bash
export CORVIN_MEASUREMENT_TRACK_UNCERTAINTY=true
export CORVIN_MEASUREMENT_TRACK_FEEDBACK=true
export CORVIN_MEASUREMENT_TRACK_PREFERENCES=true
export CORVIN_MEASUREMENT_TRACK_BUDGET=true
export CEL_PHASE4_MEASUREMENT=true
```

### Data Location
```
~/.corvin/measurement/2026-08-08/
  ├── predictions.jsonl       (ADR-0270)
  ├── feedback.jsonl          (ADR-0271)
  ├── user_choices.jsonl      (ADR-0272)
  └── budget_allocations.jsonl (ADR-0273)
```

---

## Daily Targets

| Day | Date | Predictions | Feedback | Choices | Budget | Notes |
|-----|------|-------------|----------|---------|--------|-------|
| 1 | 2026-08-08 | 0/100 | 0/100 | 0/100 | 0/100 | Deployment day (warm-up) |
| 2 | 2026-08-09 | 10/100 | 10/100 | 10/100 | 10/100 | Normal operation |
| 3 | 2026-08-10 | 30/100 | 30/100 | 30/100 | 30/100 | Normal operation |
| 4 | 2026-08-11 | 50/100 | 50/100 | 50/100 | 50/100 | Mid-week review |
| 5 | 2026-08-12 | 70/100 | 70/100 | 70/100 | 70/100 | Normal operation |
| 6 | 2026-08-13 | 85/100 | 85/100 | 85/100 | 85/100 | Normal operation |
| 7 | 2026-08-14 | 100/100 | 100/100 | 100/100 | 100/100 | Target reached |
| 8-10 | 2026-08-15-17 | Overflow | Overflow | Overflow | Overflow | Bonus data collection |

**Success Criteria:** Each track ≥100 records by 2026-08-14.

---

## Monitoring

### Health Check (Run Every Hour)
```bash
bash operator/context_engineering/scripts/health-check.sh --continuous --interval 60
```

### Watch Measurement Files Growing
```bash
watch -n 30 'ls -lh ~/.corvin/measurement/$(date +%Y-%m-%d)/'
wc -l ~/.corvin/measurement/$(date +%Y-%m-%d)/*.jsonl
```

### Check for Errors
```bash
grep -i "error\|critical" ~/.corvin/logs/session.log | tail -20
```

### Verify Guard Blocking
```bash
grep "Guard blocked" ~/.corvin/logs/session.log | wc -l
```

---

## Critical Fixes Deployed (K=5)

All 24 bugs fixed before measurement started:

### Measurement Durability (C1)
- ✅ Data fsync'd after write (prevents crash data loss)
- ✅ Checksum validation on read
- ✅ Atomic file writes (temp→rename)

### Concurrency Safety (C2, M3)
- ✅ Lock coordination for exclusive queue access
- ✅ Thread-safe cache with locking
- ✅ Double-check singleton pattern

### Snapshot Enforcement (H4)
- ✅ Post-window files skipped (prevents inconsistent state)
- ✅ Mtime + size check (clock-skew resistant)
- ✅ Guard danger zones updated

### Profile Cache Optimization (M1)
- ✅ Module-level LRU cache (max 10 profiles)
- ✅ Mtime-based fast path (no repeated disk reads)
- ✅ Automatic eviction on overflow

### Windows Compatibility (M2)
- ✅ Atomic file-copy fallback (when symlink requires admin)
- ✅ Same semantics (current profile is always "latest")

---

## Rollback (If Needed)

Single command restores backup:
```bash
# Stop service
corvin stop

# Restore from backup (timestamp 1786187075)
rm -rf ~/.corvin/tenants/_default/profiles
rm -rf ~/.corvin/tenants/_default/learning-queue
cp -r profiles.backup.1786187075 ~/.corvin/tenants/_default/profiles
cp -r learning-queue.backup.1786187075 ~/.corvin/tenants/_default/learning-queue

# Restart
corvin-serve &
```

---

## Data Analysis (Post-Week-6)

### Expected Results

**ADR-0270 (Uncertainty):**
- Confidence accuracy: ±5% (predictions match outcomes)
- Trend: convergence after ~50 samples

**ADR-0271 (Feedback):**
- Learning rate: ±0.03 delta per feedback
- Bayesian updates: moving average trend upward
- Decay weight applied to >90d-old records

**ADR-0272 (Preferences):**
- User style detected: pragmatic vs. rigorous
- Task preference clustering visible
- Time-available correlation with decision style

**ADR-0273 (Budget):**
- Budget/complexity match: ≥0.80 score
- Allocation patterns emergent
- Mismatch detection working

---

## Next Steps (Post-Measurement)

1. **Analyze data** (2026-08-18)
   - Generate reports per ADR-0270–0273
   - Identify confidence winners/losers
   - Document learning patterns

2. **Phase 2: ADR-0274 Gate**
   - Finalize context selection policy
   - Deploy to production task_engine.py
   - Enable context filtering in all surfaces

3. **Phase 3: Full Integration**
   - Wired into console suggestions
   - Wired into agent context pools
   - Real-time guard enforcement

---

## Contact Points

**Measurement Issues:** WEEK6-MEASUREMENT-PHASE-PLAN.md  
**Incident Response:** ADR-0274-INCIDENT-RESPONSE.md  
**Integration:** ADR-0274-INTEGRATION-GUIDE.md  
**Implementation:** ADR-0274-IMPLEMENTATION-COMPLETE.md

---

**Live Status:** All systems green, measurement collecting, learning cycle initiated.

Last updated: 2026-08-08 17:00 UTC
