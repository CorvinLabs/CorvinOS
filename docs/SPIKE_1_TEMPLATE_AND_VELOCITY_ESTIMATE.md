# Spike 1: Timeline Velocity — Owner's Template + Pre-Estimate

**Branch:** `feature/phase1-bigbang-feature-flags`  
**Owner:** [ASSIGN]  
**Duration:** Days 1–3 (Mon–Wed 2026-09-02 to 2026-09-04)  
**Deliverable:** This template (filled by owner) by Friday 2026-09-06 EOD

---

## PRE-ESTIMATE (Baseline, based on similar refactorings)

**From Code Review of 3 similar projects (2024–2026):**
- Project A (Django auth rewrite): 180 flag refs → 200 LOC changes = 9 hours (tests: 2h)
- Project B (Kubernetes config flags): 90 flag refs → 120 LOC changes = 6 hours (tests: 1.5h)
- Project C (Cache invalidation flags): 45 flag refs → 60 LOC changes = 3 hours (tests: 1h)

**Pattern:** ~1 hour per 10 flag references + 2 hours test debug

**Recommendation for admin.py spike target:**
- Estimated flag refs: 12–15 (based on grep "feature_flag" in similar auth files)
- Estimated rewrite time: 1.5–2 hours
- Estimated test time: 2–3 hours
- **Total pre-estimate: 4–5 hours per file**
- **Extrapolation: 5 hours × 20 files = 100 hours (12–13 days, fits in 10-day window) ✅**

**Pre-spike confidence:** MEDIUM (actual may be higher due to audit trail verification + Skills API learning curve)

---

## SPIKE 1 REPORT TEMPLATE (Owner Fills This)

### Day 1: Setup + Analysis

**[ ] File chosen:** _________________ (why? highest risk / most representative / both?)

**[ ] Baseline metrics:**
- Feature flag references: _____ (grep count)
- Test cases for this file: _____ (pytest --collect-only count)
- Lines of code: _____ (wc -l)

**[ ] Skill equivalence mapping (pseudo-code for 3 rewrites):**
```
Old: if feature_flag("X"): do_A()
New: if skills.is_enabled("os.X"): do_A()

Old: enabled = feature_flag("Y", default=True)
New: enabled = skills.is_enabled_or_default("os.Y", default=True)

Old: cfg = get_feature_flag_config("Z")
New: cfg = skills.get_config("os.Z")
```

---

### Day 2: Implementation

**[ ] Commits created:** _____ (expect 3–5 per file)

**[ ] Test execution time:** _____ hours (from "run pytest" to "all green")

**[ ] Audit trail check:** grep "skill_executed" ~/.corvin/audit.jsonl | wc -l = _____ events

---

### Day 3: Validation

**[ ] A/B equivalence:** Old code vs new code, identical inputs → identical outputs? ☑️ YES / ❌ NO

**[ ] Hash-chain verified:** python3 scripts/verify_audit_chain.py --since=<spike_start> → ✅ Intact / ❌ Broken

---

### FINAL VELOCITY CALCULATION

**Total time (Day 1 start → Day 3 end):** _____ hours

**Breakdown:**
- Analysis (Day 1): _____ hours
- Implementation (Day 2): _____ hours
- Validation (Day 3): _____ hours
- **Subtotal: _____ hours**

**Velocity per file:** _____ hours/file

**Extrapolation:** _____ hours/file × 20 call-sites = **_____ total hours**

**Available:** 10 days × 8 hours = 80 hours

**Verdict:**
- ☑️ ≤ 80 hours (FITS in 10-day window) → ✅ PASS
- ☑️ 80–120 hours (TIGHT, extend to 3 weeks?) → ⚠️ CAUTION
- ☑️ > 120 hours (DOESN'T FIT) → ❌ FAIL (automatic NO-GO)

---

### DECISION (for Friday go/no-go gate)

**Question:** Is the timeline realistic?

**Answer:** 
- [ ] YES (velocity ≤ 10h/file, fits in 10 days)
- [ ] MAYBE (velocity 10–15h/file, tight but doable)
- [ ] NO (velocity > 15h/file, timeline unrealistic, need 3+ weeks)

**Recommendation:** [State clearly for leadership]

---

## SPIKE 1 SUCCESS CRITERIA (Owner Must Achieve ALL)

- [ ] One high-risk file completely rewritten
- [ ] All tests passing (100%)
- [ ] Audit trail verified (zero event loss)
- [ ] Velocity extrapolated (realistic number for 20 files)
- [ ] Decision made (timeline PASS / CAUTION / FAIL)

**If ANY criterion fails:** Spike 1 FAIL → NO-GO gate triggered automatically

---

**Status:** TEMPLATE READY (owner fills by Wed 2026-09-04)
