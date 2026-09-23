# RISK ASSESSMENT FRAMEWORK — Phase 1–10 Adversarial Review

**Created:** 2026-09-23, 13:00 UTC  
**Status:** ✅ COMPLETE

---

## SEVERITY DEFINITIONS

### CRITICAL (Security Breach / Data Loss / Compliance Violation / Boot Failure)

**Definition:** A defect that could result in:
- Unauthorized data access or loss
- Compliance violation (GDPR Art. 32 breach)
- Audit trail corruption
- Cross-tenant leakage
- Boot/startup failure
- Auth bypass
- Privilege escalation
- Consent gate bypass
- Audit trail tampering

**Examples:**
- SQL injection in audit query
- Consent gate not enforced on API route
- Audit chain hash mismatch (corruption)
- Plugin sandbox escape
- Cross-tenant context leakage
- Unchecked user input allowing RCE

**Response:** Fix immediately, blocking all other work. 0 CRITICAL findings required for production.

**Estimated Count:** 15–30 CRITICAL findings across Phase 1–10

---

### HIGH (Functional Defect / Safety Mechanism Missing)

**Definition:** A defect that could result in:
- Functional degradation (feature not working as intended)
- Performance regression (>50% latency increase)
- Incomplete safety mechanism (e.g., consent TTL not enforced)
- Missing monitoring/alerting
- Incomplete test coverage for critical path
- Layer violation (calling up the stack)
- Circular dependency
- Undefined error handling

**Examples:**
- Consent TTL not actually expiring records
- Missing E2E test for new API route
- Audit event emission missing from critical path
- Plugin disable not actually preventing plugin execution
- Data classification missing for new data source
- No runbook for common failure mode

**Response:** Fix in current phase, but can defer non-blocking items to Phase 11 if low impact. Target: ≤12 HIGH findings.

**Estimated Count:** 8–20 HIGH findings

---

### MEDIUM (Code Quality / Missing Documentation / Optimization Opportunity)

**Definition:** A defect that could result in:
- Code maintainability issue
- Missing documentation
- Test code quality (e.g., overly mocked)
- Inefficient algorithm
- Unused code path
- Duplicate code (DRY violation)
- Missing error message clarity
- Incomplete ADR documentation

**Examples:**
- Missing docstring on public function
- Inefficient SQL query (N+1 problem)
- Duplicate validation logic in two modules
- Unclear error message (needs improvement)
- Test using too many mocks (not integration-like)
- ADR exists but compliance mapping missing

**Response:** Defer to Phase 11 or handle opportunistically. Target: 33 MEDIUM findings.

**Estimated Count:** 25–50 MEDIUM findings

---

### LOW (Cosmetic / Nice-to-Have Improvement)

**Definition:** A defect that could result in:
- Cosmetic issue (formatting, naming)
- Comment clarity improvement
- Optional feature enhancement
- Nice-to-have optimization
- Typo in error message
- Unused import
- Code style violation

**Examples:**
- Typo in error message
- Unused import statement
- Inconsistent naming (camelCase vs snake_case)
- Missing comma in comment
- Could add caching for 5% speedup
- Function name could be clearer

**Response:** Document for reference, fix opportunistically. Target: 51 LOW findings.

**Estimated Count:** 40–100 LOW findings

---

## SEVERITY-BY-COMPONENT MATRIX

This matrix shows expected severity distribution for each subsystem:

| Subsystem | CRITICAL | HIGH | MEDIUM | LOW | Total | Audit Priority |
|---|---|---|---|---|---|---|
| Audit Chain | 2–4 | 1–2 | 2–3 | 1–2 | 6–11 | 🔴 FIRST |
| Consent Gates | 2–4 | 1–3 | 2–3 | 1–2 | 6–12 | 🔴 FIRST |
| Auth/Security | 2–3 | 2–3 | 3–4 | 2–3 | 9–13 | 🔴 FIRST |
| Plugin System | 1–3 | 2–4 | 3–5 | 2–4 | 8–16 | 🟠 SECOND |
| Worker Engine | 1–2 | 2–3 | 3–4 | 2–3 | 8–12 | 🟠 SECOND |
| Data Residency | 1–2 | 2–3 | 2–3 | 1–2 | 6–10 | 🟠 SECOND |
| Console (2,800 files) | 1–3 | 3–5 | 5–8 | 5–10 | 14–26 | 🟠 SECOND |
| Bridges (A2A) | 1–2 | 1–2 | 2–3 | 1–2 | 5–9 | 🟠 SECOND |
| Voice System | 0–1 | 1–2 | 2–3 | 1–2 | 4–8 | 🟡 THIRD |
| Learning | 0–1 | 1–2 | 2–3 | 1–2 | 4–8 | 🟡 THIRD |
| Observability | 0 | 0–1 | 2–3 | 3–5 | 5–9 | 🟡 THIRD |
| **TOTALS** | **15–30** | **20–36** | **34–50** | **23–44** | **92–160** | |

**Target State (0 CRITICAL, ≤12 HIGH):**
- All CRITICAL (15–30) → 0
- All HIGH (20–36) → ≤12 (6 fixed, 6 deferred)
- MEDIUM → 33 (documented)
- LOW → 51 (documented)

---

## PRIORITY QUEUE ALGORITHM

When multiple findings exist, fix in this order:

### Phase 1: CRITICAL (All must be fixed)
1. **CRITICAL + Audit chain** — Boot failure risk
2. **CRITICAL + Consent gates** — GDPR Art. 6 violation
3. **CRITICAL + Auth bypass** — Security breach
4. **CRITICAL + Data leak** — Privacy breach
5. **CRITICAL + Compliance** — Regulatory violation

### Phase 2: HIGH (Fix or document deferred)
6. **HIGH + Plugin isolation** — Attack surface
7. **HIGH + Test coverage** — Blind spot
8. **HIGH + Worker safety** — Model selection risk
9. **HIGH + Error handling** — Fail-closed enforcement
10. **HIGH + Data residency** — Regional compliance

### Phase 3: MEDIUM + LOW (Defer to Phase 11)
11. **MEDIUM findings** — Nice-to-have improvements
12. **LOW findings** — Cosmetic changes

---

## FINDING TRIAGE TEMPLATE

Every finding, when identified, gets this data:

```markdown
## Finding [ID]

### Metadata
- **Severity:** CRITICAL | HIGH | MEDIUM | LOW
- **Subsystem:** [Audit|Consent|Auth|Plugin|...]
- **Component:** [specific file/function]
- **Discovery Date:** [YYYY-MM-DD]
- **Related ADR:** [ADR-XXXX or NONE]

### Description
[1–2 sentence summary of the defect]

### Root Cause
[Why does this exist? Historical context?]

### Evidence
[Code snippet or example demonstrating the issue]

### Impact
[Consequence if not fixed: security breach, compliance violation, functional defect, etc.]

### GDPR/EU AI Act Mapping
[Which articles does this violate, if any?]

### Remediation Sketch
[How would you fix this? (high level)]

### Priority vs Other Findings
[Relative ranking: fix before X, after Y]

### Assigned To
[Team member or stream responsible]

### Status
[NEW → IN_PROGRESS → FIXED → VERIFIED → CLOSED]
```

---

## PRIORITY LABELS

Use these labels when triaging findings:

| Label | Meaning | Example |
|---|---|---|
| `blocker-production` | Blocks production release | Auth bypass |
| `blocker-phase11-kickoff` | Blocks Phase 11 start | Missing runbook |
| `must-fix-now` | Fix in this phase | Consent gate bug |
| `defer-to-phase11` | Document for Phase 11 | Optimization |
| `depends-on-X` | Needs another fix first | Needs auth fix |
| `high-complexity` | 3+ days to fix | Plugin system refactor |
| `high-risk-fix` | Risky to fix (needs testing) | Audit chain change |
| `gated-by-X` | Waiting on another team | Waiting for security review |

---

## REMEDIATION WORKFLOW

For each finding:

1. **Triage** — Assign severity + priority
2. **Design** — Create remediation sketch
3. **Code** — Implement fix (with tests)
4. **Test** — Verify fix + no regressions
5. **Verify** — Re-audit to confirm fix
6. **Document** — Update ADR/runbook if needed
7. **Close** — Mark as VERIFIED

**Timeline:** 1–3 days per CRITICAL, 1–2 days per HIGH

---

## METRICS FOR MEASURING SUCCESS

Track these throughout the 12-week review:

### Count Metrics
```
CRITICAL findings by subsystem:
  Audit: ___ → 0
  Consent: ___ → 0
  Auth: ___ → 0
  Plugin: ___ → 0
  ... (total: ___ → 0)

HIGH findings by subsystem:
  Plugin: ___ → ≤2
  Worker: ___ → ≤2
  Data Residency: ___ → ≤2
  Console: ___ → ≤3
  Bridges: ___ → ≤2
  ... (total: ___ → ≤12)

MEDIUM findings: ___ (target: 33)
LOW findings: ___ (target: 51)
```

### Quality Metrics
```
Code coverage: 65% → 90%+
Tests passing: ___ / 275+ (target: 100%)
Audit chain verified: YES/NO (target: YES)
Consent gates tested: YES/NO (target: YES)
E2E tests for all entry points: YES/NO (target: YES)
Runbooks complete: ___ / ___ (target: 10+ critical)
SLOs defined: ___ / 36 layers (target: all)
```

### Time Metrics
```
Time to fix CRITICAL findings: ___ days (target: <10 days)
Time to fix HIGH findings: ___ days (target: <20 days)
Time to re-audit: ___ days per dimension (target: <3 days)
```

---

## GO/NO-GO CRITERIA FOR PHASE 11

Phase 10 (comprehensive audit) is complete when:

- ✅ CRITICAL findings: **0** (down from 15–30)
- ✅ HIGH findings: **≤12** (down from 20–36)
- ✅ All 5 dimension re-audits: **PASSED**
- ✅ Code coverage: **>90%** (up from 65%)
- ✅ Tests passing: **100%** (all 275+ tests)
- ✅ Runbooks written: **10+ critical paths**
- ✅ SLOs defined: **all 36 layers**

**Only if ALL criteria met:** GO FOR PHASE 11 KICKOFF (Dec 1)

---

## RISK TOLERANCE

| Risk Level | CRITICAL | HIGH | MEDIUM | LOW |
|---|---|---|---|---|
| **Zero Risk (Ideal)** | 0 | 0 | 0 | 0 |
| **Acceptable for Prod** | 0 | ≤3 | unlimited | unlimited |
| **Acceptable for Beta** | ≤1 | ≤5 | unlimited | unlimited |
| **Unacceptable** | >1 | >12 | — | — |

**CorvinOS Target:** Acceptable for Production (0 CRITICAL, ≤3 HIGH)

---

## NEXT STEPS

1. ✅ Create risk assessment framework (THIS DOCUMENT)
2. ⏳ Create review checklists (NEXT)
3. ⏳ Begin Phase 1–2 dimension reviews (Week 2)

---

**Phase 0 Progress: 2/3 complete**  
**Next Update:** After checklists created + first review dimension started

