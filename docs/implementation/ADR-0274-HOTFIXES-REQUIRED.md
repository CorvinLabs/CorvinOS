# ADR-0274: Hotfixes Required Before Deployment

**TL;DR:** Architecture is sound. 7 critical/high-priority fixes needed before Week 6 deployment. 4 additional fixes before Week 7 measurement.

---

## CRITICAL FIXES (Must implement)

| # | Fix | Impact | Complexity | Status |
|---|-----|--------|-----------|--------|
| **C1** | Queue corruption recovery spec | Data loss prevention | Low (1–2d) | TODO |
| **C2** | Aggregation ↔ Session concurrency model | Consistency guarantee | Medium (3–4d) | TODO |
| **C3** | Atomic symlink switching | Broken symlink prevention | Low (1d) | TODO |
| **C4** | Danger zones enforced (not just logged) | Learning feedback loop closure | Medium (2–3d) | TODO |

---

## HIGH-PRIORITY FIXES (Must implement)

| # | Fix | Impact | Complexity | Status |
|---|-----|--------|-----------|--------|
| **H1** | Contradiction detection (task-type bucketing) | Pattern stability | Medium (3–4d) | TODO |
| **H2** | On-demand aggregation trigger | Staleness mitigation | Low (1–2d) | TODO |
| **H3** | Aggregation timeout + auto-recovery | Silent failure prevention | Low (1–2d) | TODO |

---

## MEDIUM-PRIORITY FIXES (Before measurement, Week 7)

| # | Fix | Impact | Complexity | Status |
|---|-----|--------|-----------|--------|
| **M1** | Confidence intervals in profiles | LLM epistemology | Medium (2–3d) | TODO |
| **M2** | Contradiction handling strategy | Feedback clarity | Medium (2–3d) | TODO |
| **M3** | Decision provenance (score history) | LLM debuggability | Low (1–2d) | TODO |
| **M4** | Calibrated cold-start defaults | New context accuracy | Low (1–2d) | TODO |

---

## CRITICAL FINDINGS

### Data Loss Risk (C1)
```
If Tier 2 queue corrupts:
  ✗ Plan says "validate checksums" but not "fail mode"
  ✗ Silent data loss cascades to Tier 3
  ✓ FIX: Explicit corruption handling (log + skip + alert)
```

### Consistency Risk (C2)
```
Aggregator 2am–5am + Sessions 2:30am:
  ✗ Race condition: which records does aggregator read?
  ✗ Can profiles become inconsistent?
  ✓ FIX: Explicit concurrency contract + file locking
```

### Atomicity Risk (C3)
```
Symlink update between write and ln:
  ✗ Sessions can load broken symlinks
  ✓ FIX: POSIX atomic rename (write → temp → mv)
```

### Learning Effectiveness Risk (C4)
```
Profiles mark danger zones but don't enforce:
  ✗ "skip tests when urgent = 70% fail" → agent still skips tests
  ✗ Learning is passive observation
  ✓ FIX: DangerZoneGuard blocks contexts in danger conditions
```

### Pattern Stability Risk (H1)
```
Context helps 70% of tasks, hurts 30%:
  ✗ Bayesian update thrashes between updates
  ✗ No task-type bucketing
  ✓ FIX: Separate scores per task type; detect contradictions
```

### Staleness Risk (H2)
```
Profiles refresh nightly only:
  ✗ Critical context change → 24h delay before reflected
  ✓ FIX: On-demand aggregation + auto-trigger on signal volume
```

### Silent Failure Risk (H3)
```
Aggregation crashes 3:45am:
  ✗ Alert fires; no auto-recovery; profiles stale for days
  ✓ FIX: Timeout + fallback + operator runbook
```

---

## LLM LEARNING INFRASTRUCTURE GAPS

### Critical (Impacts learning quality)
- **No ground-truth feedback source spec** — Can't know signal-to-noise ratio
- **No confidence intervals** — Can't distinguish reliable vs. unreliable scores
- **No contradiction handling** — Thrashing patterns when feedback diverges

### High (Impacts usability)
- **No conditional patterns** — Can't express "A+B works for ML but not DevOps"
- **No explainability** — Score changes are opaque; can't debug

### Medium (Nice-to-have)
- **No per-prediction feedback** — Can't calibrate own predictions
- **No override mechanism** — Locked into learned behavior

---

## DEPLOYMENT CHECKLIST

### BEFORE Week 6 Deployment
- [ ] C1: Queue corruption handling (detection + recovery)
- [ ] C2: Concurrency model (locking + contract)
- [ ] C3: Atomic symlink switching (POSIX rename)
- [ ] C4: Danger zones enforced (DangerZoneGuard)
- [ ] H1: Task-type bucketing (contradiction detection)
- [ ] H2: On-demand aggregation CLI
- [ ] H3: Aggregation timeout + runbook
- [ ] All tests passing (concurrency tests included)
- [ ] Operator runbook for aggregation failure

### BEFORE Week 7 Measurement
- [ ] M1: Confidence intervals in profiles
- [ ] M2: Contradiction strategy documented
- [ ] M3: Decision provenance (score history)
- [ ] M4: Calibrated defaults (prior inference)

### AFTER MVP (Nice-to-have)
- [ ] Multi-tenant support (parameterized paths)
- [ ] Advanced LLM features (L6–L12)

---

## ESTIMATED ADDITIONAL EFFORT

**MUST-FIX (C1–C4, H1–H3):** ~25–30 days of implementation  
**SHOULD-FIX (M1–M4):** ~8–10 days of implementation  
**NICE-TO-HAVE:** ~10–15 days (optional)

**Recommendation:** Implement MUST-FIX + SHOULD-FIX items. Defer NICE-TO-HAVE to post-MVP.

---

## NEXT STEP

Update ADR-0274 with:
1. Concurrency contract (C2)
2. Atomic update strategy (C3)
3. Contradiction detection rules (H1)
4. Failure recovery procedures (H3)

Proceed with implementation plan incorporating all MUST-FIX items.

