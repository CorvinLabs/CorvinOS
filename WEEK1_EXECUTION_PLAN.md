# L5 Autonomous Deployment — Week 1 Execution Plan

**Goal:** Deploy L5 to staging, integrate a real Skill, collect 100+ feedback cycles, verify learning improves metrics.

**Timeline:** 7 days (Day 1-7)

---

## Phase 1a: Staging Infrastructure (Days 1-2)

### Infrastructure Setup
- [x] L5 feedback stability gate exists (core/skills/feedback_stability.py)
- [x] Operator approval gate exists (OperatorApprovalGate)
- [x] REST API routes exist (core/gateway/routes/approval_routes.py)
- [ ] **TODO:** Deploy to staging environment with separate config
- [ ] **TODO:** Set up monitoring (Prometheus metrics, Grafana dashboard)
- [ ] **TODO:** Create staging audit backend
- [ ] **TODO:** Wire learning backend to approval gate

### Success Criteria
- Staging L5 listens on separate port (8766 instead of 8765)
- Monitoring dashboard live (Grafana)
- Audit trail empty and ready
- Ready for Skill integration

---

## Phase 1b: Real Skill Integration (Days 2-3)

### Skill Selection
- Choose: `os.delegation_router` (routing engine that makes classification decisions)
- Rationale: High-volume decisions, real user impact, good learning potential

### Integration Tasks
- [ ] Wire Skill → Feedback Loop
  - Skill emits confidence scores
  - Learning system processes feedback
  - Feedback flows to FeedbackStabilityGate
  - Gate forwards to OperatorApprovalGate if drift detected
- [ ] Create decision record schema
- [ ] Implement feedback collection endpoint
- [ ] Create staging operator account (for manual approvals)

### Success Criteria
- 10 test decisions flow through full pipeline
- Audit shows all events logged
- Operator can approve/reject decisions
- No drops in feedback pipeline

---

## Phase 1c: Collect 100 Feedback Cycles (Days 3-7)

### Feedback Collection Strategy
1. **Days 3-4:** Synthetic load (simulated decisions)
   - Generate 20 decisions/hour
   - Mix of high-confidence (80%+) and low-confidence (20-40%)
   - Operators manually respond (approve/reject randomized)
   
2. **Days 5-6:** Real load (actual Skill decisions)
   - Route real routing tasks through Skill
   - Collect operator feedback on each
   - Target: 30+ real decisions/day
   
3. **Day 7:** Final 30 cycles + verification
   - Ensure 100+ total cycles collected
   - Verify learning convergence

### Metrics to Track
- Decisions processed: target 100+
- Operator approval rate: baseline measurement
- Confidence score distribution: track before/after
- Learning convergence: does threshold stabilize?

### Success Criteria
- 100+ feedback cycles completed
- All audited + hash-chained
- No failed approvals
- Learning engine converged (threshold stable within ±0.02)

---

## Phase 1d: Verify Learning Improved Metrics (Day 7)

### Metrics to Measure
1. **Auto-Approval Rate (%)** 
   - Before: % of decisions auto-approved (no operator needed)
   - After: should increase as learning identifies safe patterns
   - Target: +10% improvement
   
2. **Operator Rejection Rate (%)**
   - Before: % of operator approvals that should have been rejected
   - After: should decrease (learning removes bad high-confidence decisions)
   - Target: -10% improvement
   
3. **Learning Convergence**
   - Track confidence_threshold over 100 cycles
   - Should stabilize (no oscillation)
   - Success: ±0.02 standard deviation in final 10 cycles

4. **Audit Chain Integrity**
   - Every decision logged: ✅
   - Hash chain unbroken: ✅
   - Tenant isolation: ✅

### Reporting
- Generate Week 1 report with metrics
- Visualize threshold convergence (graph)
- Document any issues found

---

## Tests to Create & Run

### Test Files
- `tests/test_l5_week1_staging_deployment.py` (40+ tests)
  - Infrastructure alive
  - Skill-L5 wiring functional
  - Feedback collection working
  - Metrics collection accurate
  
- `tests/test_l5_week1_learning_convergence.py` (30+ tests)
  - Learning engine processes feedback
  - Threshold updates correctly
  - Confidence grows with consistency
  - Convergence detected after N cycles
  
- `tests/test_l5_week1_audit_integrity.py` (20+ tests)
  - Every decision audited
  - Hash chain validates
  - Tenant isolation verified
  - No event loss

### Test Execution
```bash
# Run all Week 1 tests
pytest tests/test_l5_week1_*.py -v --tb=short

# Run with coverage
pytest tests/test_l5_week1_*.py --cov=core.skills --cov=core.learning --cov=core.gateway

# Continuous monitoring (during feedback collection)
pytest tests/test_l5_week1_audit_integrity.py -v --interval=5m
```

---

## Deliverables (End of Week 1)

### Code
- `core/l5_staging/` — Staging-specific L5 config
- `core/l5_staging/monitoring.py` — Prometheus metrics exporter
- `core/l5_staging/feedback_collector.py` — Feedback collection endpoint
- `tests/test_l5_week1_*.py` — All test files (90+ tests)

### Reports
- `WEEK1_REPORT.md` — Metrics, convergence graphs, issues found
- `WEEK1_METRICS.json` — Raw metrics (for Week 2 comparison)
- `WEEK1_AUDIT_SAMPLE.jsonl` — Sample of 10 audit events

### Infrastructure
- Staging L5 deployed and running
- Monitoring dashboard live
- 100+ feedback cycles collected
- Learning system verified working

### Success Criteria
All of:
- ✅ Staging infrastructure alive
- ✅ Skill integration functional
- ✅ 100+ feedback cycles collected
- ✅ Learning improved metrics ≥10%
- ✅ All tests green
- ✅ Week 1 report delivered

---

## Rollback Plan (If Issues Found)

If at any point:
- Audit chain breaks → revert to snapshot, investigate
- Learning diverges → disable learning, investigate
- Skill crashes → revert Skill, run diagnostics
- Operator confusion → update training, retry

---

## Next Steps (Week 2 Planning)

If Week 1 succeeds:
- 10 real operators invited to beta
- Thresholds tuned from real load
- Training materials refined
- Ready for Week 2 operator beta

---

**Start Date:** 2026-09-05  
**Target Completion:** 2026-09-12  
**Owner:** Claude Code (autonomous)  
