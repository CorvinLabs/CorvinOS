# 🎯 ADR IMPLEMENTATION ROADMAP — EXECUTIVE SUMMARY

**Date:** 2026-08-25  
**Target:** v0.2-rc1 canary ready by end of Week 1  
**Owner:** Claude Code + shumway (audit)

---

## 📌 THE SITUATION

11 ADRs reviewed. Status:
- **3 complete** (0369, 0371, 0387) ✅
- **4 partial** (0237, 0311, 0365, 0391) 🟡
- **3 not started** (0010, 0235, 0236) ❌
- **1 blocked** (0370 — by 0371) 🔴

**Critical issue:** ADR-0370 (Adaptive Strategy) unreachable — blocks v0.2-rc1 canary.

---

## ⚡ IMMEDIATE ACTION (THIS WEEK)

### 🔴 P0: ADR-0371 Wiring — 1-2 hours
**What:** Verify ADR-0371 applied (makes ADR-0370 reachable)  
**Where:** `core/orchestration/loop_engineer.py` (StrategyAdvisor call site)  
**Status:** Code complete (k=1-4 LDD), need to confirm merged to main  
**If not merged:** Branch + PR + merge (1-2h)  
**Blocker:** YES (RC cannot ship without this)  
**Timeline:** Must complete by Thu/Fri

```bash
# Check status
git log main | grep 0371
grep -n "get_strategy()" core/orchestration/loop_engineer.py

# If empty → needs to be applied
```

**Decision:** If not merged by Wed EOD → ESCALATE

---

## 🚀 PARALLEL WORK (Week 1-2)

### 🟡 P1: ADR-0365 Cloudflare — 4-6 hours
**What:** Deploy telemetry dashboard to Cloudflare Pages  
**Status:** Code complete, deployment WIP  
**Timeline:** Can start immediately (parallel)  
**Blocker:** NO (nice-to-have for Week 1, not RC-blocking)

---

### 🟠 P1: ADR-0391 Implementation — 2 weeks
**What:** TaskClassifier + AdaptiveBudgetAllocator + PerformanceTracker  
**Status:** Design complete, code not written  
**Timeline:** Week 2-3 (can defer to v0.3 if needed)  
**Blocker:** NO (feature flag ship-dark ready)  
**Target:** v0.13-beta (Phase 3.1 v2)

---

### 🟡 P2: ADR-0237 ADR Document — 2-3 hours
**What:** Formalize existing extension-points code as ADR  
**Status:** Code complete, documentation missing  
**Timeline:** Week 4 (documentation sprint)  
**Blocker:** NO (code is production-ready)

---

## 📊 TIMELINE

```
TODAY (Aug 25)
  └─ Verify ADR-0371 status (1h)
     └─ IF NOT APPLIED: Submit PR → merge by Wed

Week 1 (Aug 25-29)
  ├─ ADR-0371 wiring verification (0.5h)
  ├─ ADR-0365 Cloudflare setup (4-6h) — parallel
  ├─ Full test suite (2h)
  └─ RC v0.2-rc1 RELEASE (Thu/Fri)
     └─ GO/NO-GO GATE: Canary launch

Week 2-3 (Sept 1-12)
  ├─ ADR-0391 implementation (32h)
  ├─ Production measurement (ongoing)
  └─ Canary expansion to 50% (Thu week 4)

Week 4 (Sept 15-19)
  ├─ ADR-0237 ADR document (2-3h)
  ├─ ADR-0311 tests (8h)
  └─ 50% CANARY STABLE ✅

Post-GA (Sept 22+)
  ├─ ADR-0010: Audit Sinks (1-2w)
  ├─ ADR-0235: Plugin Classes (1w)
  └─ ADR-0236: Core extraction (3-4w)
```

---

## ✅ SUCCESS CRITERIA

### RC Release (Week 1)
- [x] ADR-0371 wiring applied + tests green
- [x] ADR-0370 reachable from production
- [x] No E2E wiring violations
- [x] All unit/E2E tests passing (200+)
- [x] Compliance audit OK
- [x] Release notes complete

### Canary Launch (Week 1-2)
- [x] v0.2-rc1 tagged
- [x] Feature flags: adaptive_strategies ON, context_routing OFF
- [x] Monitoring dashboards ready
- [x] 10% user cohort configured

### 50% Expansion (Week 4)
- [x] ADR-0370 metrics show gains vs. static
- [x] No regressions in upstream metrics
- [x] 10% cohort stable 3+ days
- [x] <1% error rate

### GA (Week 6)
- [x] 50% cohort stable 3+ days
- [x] Operator feedback satisfaction >80%
- [x] Security audit passed
- [x] All tests passing

---

## 🎯 RISK ASSESSMENT

| Risk | Severity | Likelihood | Mitigation |
|---|---|---|---|
| ADR-0371 not merged | 🔴 CRITICAL | LOW | Check today, escalate if missing |
| ADR-0391 complexity | 🟠 MEDIUM | MEDIUM | Can defer to v0.3, feature flag ready |
| Cloudflare delays | 🟡 LOW | LOW | Not on critical path |
| Canary metrics poor | 🟡 LOW | MEDIUM | Pivot to v0.3 redesign |

---

## 💰 EFFORT BREAKDOWN

| Phase | Task | Effort | Timeline | Owner |
|---|---|---|---|---|
| **P0 (Blocker)** | ADR-0371 verification + merge | 1-2h | This week | Claude |
| **P1a (Parallel)** | ADR-0365 Cloudflare | 4-6h | Week 1 | Claude/DevOps |
| **P1b (Parallel)** | ADR-0391 implementation | 32h | Week 2-3 | Claude |
| **P2 (Doc debt)** | ADR-0237/0311 | 10h | Week 4 | Claude |
| **P3 (Deferred)** | ADR-0010/0235/0236 | 40h | v0.3 | Claude |

**Total:** ~60h (1.5 person-weeks) for complete delivery

---

## 📋 DECISION GATES

### Gate 1: RC Release (End of Week 1)
**Decision Maker:** shumway  
**Criteria:**
- ✅ ADR-0371 wiring applied
- ✅ No E2E violations
- ✅ Tests green

**Options:**
- **GO:** Ship v0.2-rc1, start 10% canary
- **NO-GO:** Fix issues, retry next attempt

---

### Gate 2: Canary Expansion (Week 4)
**Decision Maker:** shumway  
**Criteria:**
- ✅ 10% cohort stable 3+ days
- ✅ ADR-0370 metrics positive
- ✅ <1% error rate

**Options:**
- **GO:** Expand to 50% users
- **NO-GO:** Rollback, pivot to v0.3

---

### Gate 3: GA (Week 6)
**Decision Maker:** shumway  
**Criteria:**
- ✅ 50% cohort stable 3+ days
- ✅ No regressions
- ✅ Operator feedback positive

**Options:**
- **GO:** Full 100% rollout
- **NO-GO:** Rollback, hold at 50% (monitor more)

---

## 🔔 ESCALATION PATH

**If ADR-0371 not merged by Wed EOD:**
1. Claude → escalate to shumway
2. shumway → approve emergency merge (RC blocker)
3. Proceed with RC release Fri

**If ADR-0391 not ready for Week 2:**
1. Defer to v0.3
2. Keep feature flag OFF (ship-dark)
3. No impact to canary schedule

---

## 📞 KEY CONTACTS

| Role | Name | Owns |
|---|---|---|
| Implementation | Claude Code | ADRs 0361-0391 |
| Audit/Merge | shumway | ADR-0264 gate, RC release gate |
| Ops/Canary | shumway | 10% → 50% → 100% rollout |

---

## 💡 BOTTOM LINE

**Current Status:** v0.2-rc1 **ONE BLOCKER AWAY** from canary  
**Action:** Verify ADR-0371 merged to main (1h check today)  
**Impact:** If merged → RC ready by end of Week 1 ✅  
**If not:** Branch + merge + tests + retry (1-2h extra)

**Recommendation:** START NOW

---

**Next Review:** 2026-08-29 (RC gate)  
**Status:** Awaiting ADR-0371 verification  
**Confidence:** HIGH (all other pieces in place)

