# L5 Operator Guide — Complete Reference

**Version:** 1.0 (Phase 6)  
**Last Updated:** 2026-09-04  
**ADR:** ADR-0589

## Table of Contents

1. Overview & Quick Start
2. The 5-Gate Workflow  
3. Approval Decision Making
4. SLA & Performance Monitoring
5. Troubleshooting & Recovery
6. Advanced Tuning
7. FAQ

## 1. Overview & Quick Start

### What is L5?

L5 is CorvinOS's automated decision approval system. It:
- ✅ Auto-approves 60% of safe changes (Smooth gate)
- ✅ Routes 30% for operator review (Operator gate)
- ✅ Validates 100% for correctness (Quality gate)
- ✅ Detects conflicts across skills (Conflict gate)
- ✅ Holds 24h for safety monitoring (Hold gate)

### Your Role

As an L5 operator, you:
1. **Approve/Reject** low-confidence changes (k=2 gate, ~5min SLA)
2. **Monitor** configurations during 24h hold periods
3. **Escalate** when conflicts arise between skills
4. **Tune** confidence thresholds based on results
5. **Learn** from revokes to improve skill tuning

### First Day Checklist

- [ ] Read "The 5-Gate Workflow" section
- [ ] Watch the L5 training video (~10min)
- [ ] Take the interactive tutorial (all 8 steps)
- [ ] Get dashboard access (panel: `/app/l5-metrics-monitor`)
- [ ] Review current approval queue

## 2. The 5-Gate Workflow

### Gate k=1: Smooth (Auto-Approval)

**Trigger:** Config change with confidence > THRESHOLD (default 95%)  
**Action:** Auto-approve (zero operator involvement)  
**Time:** < 1 second

**Decision tree:**
```
Skill proposes change (confidence: C%)
  ├─ C > 95% → AUTO-APPROVE (Smooth gate)
  ├─ 70-95% → SEND TO k=2 (Operator gate)
  └─ < 70% → REJECT (too uncertain)
```

**Your role:** Monitor auto-approval rate (should be 55-65%)

---

### Gate k=2: Operator (Manual Approval)

**Trigger:** Config change with 70% < confidence < 95%  
**Action:** Manual operator decision (you decide)  
**Time:** 5 minutes SLA

**What you see:**
- Skill ID + metric name
- Proposed value (delta from current)
- Confidence score + reasoning
- Previous config value
- Audit trail + related decisions

**Your decision:**
1. **APPROVE** → Passes to k=3 (Quality) gate
2. **REJECT** → Blocks change; skill learns why
3. **HOLD** → Temporarily pending (rare)

**SLA:** Decide within 5 minutes  
**Best practice:** Batch 10-15 approvals per session

---

### Gate k=3: Quality (Validation)

**Trigger:** Change after k=2 approval  
**Action:** Automatic validation (syntax, types, constraints)  
**Time:** < 100ms

**Validates:**
- ✓ Syntax correctness (JSON, YAML valid)
- ✓ Type correctness (string vs. number vs. bool)
- ✓ Range correctness (value in [min, max])
- ✓ Constraint correctness (dependent params consistent)

**On rejection:**
- Change blocked (never reaches production)
- Skill receives feedback
- Skill proposes different value
- Change re-enters at k=1

**Your role:** If you see many rejects on one skill → contact skill owner

---

### Gate k=4: Conflict (Cross-Skill Coordination)

**Trigger:** Multiple skills propose incompatible changes simultaneously  
**Action:** Detect conflict, propose resolution, escalate if needed  
**Time:** 2 minutes SLA (faster than operator approval)

**Example conflicts:**
- Skill A: Increase timeout → Skill B: Decrease connections → Resource deadlock
- Skill A: Enable caching → Skill B: Disable caching → Contradiction

**Resolution strategy:**
1. Rank by importance (criticality, user impact)
2. Approve highest-rank change
3. Reject lower-rank change
4. Lower skill re-proposes with adjustment

**Your role:** Only escalates when two equal-priority skills disagree → you pick winner

---

### Gate k=5: Hold (Safety Holdover)

**Trigger:** Change approved by k=1-k=4  
**Action:** Deploy with 24h hold before permanent lock  
**Time:** 24 hours (configurable)

**Timeline:**
```
T+0m:  Change deployed (with hold flag)
T+0-24h: MONITORING WINDOW
          → Watch metrics
          → Check logs
          → Monitor user reports
T+24h: Hold expires
       → If all green: Change locked permanent
       → If issues found: REVOKE (automatic rollback)
```

**Your role:** Monitor during hold; early revoke if issues emerge

---

## 3. Approval Decision Making

### Evaluating an Approval Request

**Checklist:**
1. ✓ Read the skill's reasoning (always)
2. ✓ Check the magnitude (small → safer)
3. ✓ Compare to previous value (was old value reasonable?)
4. ✓ Review confidence score (how sure is the skill?)
5. ✓ Check historical context (similar decisions in past?)

### Decision Framework

| Confidence | Skill History | Decision | Rationale |
|-----------|--------------|----------|-----------|
| 85%+ | Good | APPROVE | High confidence + proven skill |
| 80-85% | Good | APPROVE | Good track record |
| 75-80% | Good | EVALUATE | Borderline; check context |
| < 75% | Any | REJECT | Too uncertain; wait for more data |
| 85%+ | Bad | EVALUATE | Good confidence but skill unreliable |
| Any | New | EVALUATE | Unknown skill; more caution needed |

### Risky Patterns to Reject

❌ Reject if you see:
- Timeout changes > 50% delta
- Connection limits 10x change
- Configuration values outside historical range
- Changes contradicting previous rejections
- Cascading changes on same metric (k=2 rejects, try again)

### Safe to Approve

✅ Approve if:
- Magnitude < 10% of current value
- Confidence > 85%
- Skill has good track record (< 5% revoke rate)
- Similar decision approved successfully before
- No conflicting changes in queue

---

## 4. SLA & Performance Monitoring

### Key Metrics (Dashboard)

**Real-time:**
- ✓ Gate latency (p50/p95/p99 per gate)
- ✓ Operator latency (avg approval decision time)
- ✓ Auto-approval rate (% of changes auto-approved)
- ✓ Rejection rate (% rejected by operator)
- ✓ Revoke rate (% revoked during hold)

**SLA Thresholds:**
| Metric | Target | Status | Action |
|--------|--------|--------|--------|
| Gate p99 latency | ≤ 10s | HEALTHY | Monitor |
| Operator latency | ≤ 5min (300s) | OK/CRITICAL | Add operators if exceeded |
| Auto-approval rate | 55-65% | OK/LOW/HIGH | Tune threshold |
| Rejection rate | 10-20% | OK | Expected; indicates review |
| Revoke rate | < 3% | OK/HIGH | Investigate if > 5% |

### Monitoring During Hold Period

**Metrics to watch:**
- Latency spike (>10% vs. baseline)
- Error rate increase (>5% new errors)
- CPU/memory spike (>20% vs. baseline)
- User complaints (tickets, forum mentions)
- Related config changes (other skills changing same param)

**Decision tree for issues during hold:**

```
Metrics show anomaly
  ├─ Anomaly started with this change? 
  │  ├─ YES + impact high (>25%) → EARLY REVOKE
  │  ├─ YES + impact low (<5%) → WAIT 15min, re-evaluate
  │  └─ NO → Different cause; investigate separately
  └─ Anomaly predates this change? → Not this change's fault
```

---

## 5. Troubleshooting & Recovery

### Issue: Queue Backing Up (100+ pending)

**Cause:** Operator load high OR Smooth threshold too conservative  
**SLA:** Clear within 15-30min

**Recovery steps:**
1. Increase Smooth threshold (95% → 97%)
   - More auto-approvals, less operator load
   - Monitor next hour for increased revoke rate
2. Check staffing (need more operators?)
3. Batch approve low-risk items (< 5% delta)

---

### Issue: High Revoke Rate (> 5%)

**Cause:** Confidence threshold too low OR Quality validation too permissive  
**SLA:** Resolve within 1-3 days

**Recovery steps:**
1. Lower Smooth threshold (95% → 90%)
   - Route more to operator review
   - Fewer false auto-approvals
2. Strengthen Quality validation (contact validation team)
3. Increase hold period (24h → 48h) temporarily
4. Review revoked changes → look for patterns

---

### Issue: Operator Latency High (> 5min)

**Cause:** Queue overloaded OR slow decision process  
**SLA:** Resolve within 1-7 days

**Recovery steps:**
1. Increase Smooth threshold (less manual work)
2. Hire/train more operators
3. Improve approval UI (faster decision interface)
4. Prioritize CRITICAL approvals

---

### Emergency: Complete System Failure

**SLA:** Restore within 30min - 2 hours

**Recovery steps:**
1. Switch to MANUAL MODE (all changes require approval)
2. Page L5 maintainers (urgent escalation)
3. Audit recent changes (what broke?)
4. Restore from last known good config

---

## 6. Advanced Tuning

### Adjusting Smooth Threshold

**Current default:** 95% confidence

**When to raise (fewer auto-approvals):**
- High stability requirement (SLA-critical services)
- New skill (needs longer observation)
- Post-incident (overly cautious period)

**When to lower (more auto-approvals):**
- High throughput required (growth phase)
- Stable skill (excellent track record)
- Low-risk metrics (cosmetic configs)

**Process:**
1. Change threshold by small increments (±2%)
2. Monitor revoke rate next 24h
3. If revoke rate > 5%, revert
4. Document reason for change

---

### Setting Up Alerts

**Recommended alerts:**
- Operator latency > 6 minutes (warn)
- Operator latency > 10 minutes (critical)
- Revoke rate > 5% (warn)
- Queue depth > 50 (warn)
- Gate latency p99 > 15s (critical)

---

## 7. FAQ

**Q: How long does approval take?**  
A: k=2 operator approval has 5-min SLA. Total k=1→k=5: ~30 seconds + 24h hold period.

**Q: What if I forget to monitor during hold?**  
A: Auto-monitoring tracks key metrics. Worst case, change locks in after 24h. Not ideal but safe.

**Q: Can I force-approve a Quality gate reject?**  
A: No. Quality rejects are hard blocks; override breaks guarantees.

**Q: What if a skill disagrees with my rejection?**  
A: Skill learns from your decision. Document your reasoning; it feeds learning.

**Q: How often should I review thresholds?**  
A: Weekly during ramp-up. Monthly when stable. Quarterly to optimize.

**Q: Who do I contact for help?**  
A: Check escalation path in your org docs. L5 Slack channel for quick questions.

---

**Last updated:** 2026-09-04  
**ADR reference:** ADR-0589: L5 Operator Training & Support
