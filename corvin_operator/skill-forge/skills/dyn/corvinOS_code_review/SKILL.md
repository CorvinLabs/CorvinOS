---
name: corvinOS_code_review
description: Use to run a full LDD-driven CorvinOS code review — iterative review -> fix -> E2E (on real data + real LLMs where possible, like an integration test) loop that exits ONLY after TWO CONSECUTIVE clean rounds. Full Loss-Driven Development is mandatory every round. Triggers: review, code review, LDD, iterative review, until no findings, e2e, integration test, full review.
---

---
name: corvinOS.code.review
type: domain
description: 'Use to run a full LDD-driven CorvinOS code review — iterative review -> fix -> E2E (on real data + real LLMs where possible, like an integration test) loop that exits ONLY after TWO CONSECUTIVE clean rounds. Full Loss-Driven Development is mandatory every round. Triggers: review, code review, LDD, iterative review, until no findings, e2e, integration test, full review.'
claim:
  origin: corvinOS review system
  format: skill-format.md
  role: orchestrator
references: []
---

# CorvinOS Code Review: Master Orchestrator

## When to use
- Fire when asked for a full / thorough / iterative CorvinOS code review.
- Dispatches the five review.* sub-skills + an E2E pass, and loops until TWO clean rounds in a row.
- Do NOT use for: a single-file glance (use the relevant review.* directly).

**Entry Point** for complete CorvinOS code reviews.
Runs an **iterative review → fix → E2E loop** that exits only after the code
survives **two consecutive clean rounds** (no findings AND a green E2E).

---

## Core Principle: Iterative Until TWO Consecutive Clean Rounds

A single clean pass is not enough — a fix in round N can introduce a defect only
round N+1 catches. The loop's unit of work is **Review → Fix → E2E**; it exits
ONLY when two back-to-back rounds find nothing AND the E2E is green:

```
Round N: Review (5 phases) → Fix ALL findings → E2E / integration test
  → Any findings (review OR E2E failure)?
       YES → reset clean-streak to 0 → fix everything → Round N+1
       NO  → clean-streak += 1
             clean-streak >= 2 ? → APPROVE
             else → Round N+1   (must come back CLEAN again to confirm)
  → Round >= 7 → Escalate (structural problem, not an incremental fix)
```

## LDD is mandatory (full Loss-Driven Development — every round)

This review **is** an LDD loop — apply the discipline, not just the checklist:

- `loop-driven-engineering` frames the loop · `reproducibility-first` — one observation is not a gradient; confirm a finding is real before fixing.
- `root-cause-by-layer` — every finding names its STRUCTURAL origin; no symptom patches.
- `dialectical-reasoning` — thesis/antithesis/synthesis before any non-trivial fix or the APPROVE call.
- `loss-backprop-lens` sizes each fix to the loss · `e2e-driven-iteration` drives Phase 6 (real data/LLMs).
- `docs-as-definition-of-done` — a round is not clean until docs + diagrams are synced.

Skipping LDD in a round is itself a finding (see constraint 11).

**Key constraint:** each re-review is **independent** — re-run the full checklist
+ E2E from scratch, as if seeing the code for the first time.

---

## Iterative Loop Procedure

**Before Round 1:** record the git commit/diff range + the review scope (files/layers).

### Each Round: Run the 5-Phase Review

Each phase loads its sub-skill (which carries the full checklist):

- **Phase 1 — Gates & Structure** · `corvinOS.review.gates` — commit hygiene, compliance/licensing red-lines, ADR-gate, layer invariants.
- **Phase 2 — Compliance & Security** · `corvinOS.review.compliance` — GDPR Art. 5/17, EU AI Act, secret-vault, path-gate, audit-chain, injection/observer.
- **Phase 3 — Frontend & UI** · `corvinOS.review.frontend` — build/TS, a11y, visual regression, UI E2E. *(Skip if no UI in scope — name the reason.)*
- **Phase 4 — Backend Logic & Testing** · `corvinOS.review.backend` — protocol/wire, state machines, fail-closed errors, unit/integration tests, coverage ≥ 80%.
- **Phase 5 — Quality & Final Gate** · `corvinOS.review.quality` — docs sync, diagram sync (both hard), git hygiene, final decision tree.

#### Phase 6: E2E / Integration Test (real data + real LLMs)
**Load:** `corvinOS.review.e2e` (the full definition of a real E2E) +
`e2e-driven-iteration` (the rhythm); `per_subtask_e2e` for security-touching code.
- Run the change end-to-end per `corvinOS.review.e2e`: real entry point + real
  DB / LLM / A2A on the fix's path, asserting a real observable outcome. Prefer
  **Gold** (real cloud LLM + prod-like DB); name any **Silver** fallback, and
  never report clean on mocks alone.
- A failing or flaky E2E is a **finding** — it resets the clean-streak.

### After Each Round: Decision Gate (two-consecutive-clean)

Apply the loop diagram above. A round is **clean** iff `review findings == 0 AND E2E green`.
- **Not clean** → reset clean-streak to 0, fix **all** findings (review + E2E, no cherry-pick), next round; escalate at round 7.
- **Clean** → clean-streak += 1; APPROVE only at streak ≥ 2, else run one more confirming round.

The loop exits on APPROVE only; a REJECT-level red-line exits immediately (see Severity Levels).

### What "Independent" Means
- Re-read changed files from scratch; re-run all checklist items AND the E2E.
- A bug introduced while fixing may surface only in round N+1 — which is why one
  clean round isn't enough and the streak must reach 2.

---

## Common Scenarios

Every scenario ends with the **E2E (Phase 6)** and the **two-consecutive-clean**
exit — only the review phases in scope change.

| Scenario | Review phases (then → E2E) |
|---|---|
| Bug fix (minimal) | gates → backend → quality |
| New feature (full) | all 5 |
| Security / compliance | gates → compliance → backend → quality (compliance first every round) |
| Web-UI heavy | gates → frontend → quality (Playwright E2E) |

---

## Severity Levels

| Level | Meaning | Loop Behaviour |
|---|---|---|
| **REJECT** | Red-line violated | Stop loop, do not fix — requires architectural decision |
| **REQUEST CHANGES** | Fixable finding | Fix in current round, re-review next round |
| **APPROVE** | Two consecutive clean rounds (0 findings + green E2E) | Loop exits |

A REJECT exits the loop immediately — it needs a design decision, not a next-round fix.

---

## Finding Template

```markdown
**[Round N][Layer-NN | Security | Testing | E2E]** <finding>
- Mechanism / surface: <what is affected>
- Impact / severity: <consequence — CRITICAL / HIGH / MEDIUM>
- Fix: <action>   ·   Verify: <test / E2E that proves it>
- Reference: docs/claude-ref/layer-NN-*.md
```

---

## Loop Exit Report (Required on APPROVE)

```markdown
## Review Complete
- Rounds: N · clean-streak at exit: 2 (required) · final two rounds: 0 / 0 findings
- E2E: <real-data + real-LLM | fallback + skip reason>
- Phases run / skipped (with reasons): [...]
- Decision: APPROVE
```

---

## Load-Bearing Constraints (Never Skip)

1. **Audit is truth** — corruption = REJECT (loop exits)
2. **Compliance beats convenience** — every round, no exceptions
3. **Docs sync is hard** — not optional in any round
4. **E2E every round** — on real data + real LLMs where possible; a failing/flaky E2E is a finding
5. **Hash-chain integrity** — one broken link fails repo
6. **ADR-gate** — structural decisions need records
7. **No silent failures** — always name skip-reasons (incl. any E2E fallback)
8. **Fix ALL before re-review** — partial fixes invalidate the next round's independence
9. **Two consecutive clean rounds** — one clean round never approves; a finding resets the streak to 0
10. **No real-data E2E ⇒ not clean by default** — fall back + name the reason; never approve on unit tests alone
11. **Full LDD every round** — root-cause-by-layer, dialectical-reasoning, reproducibility-first, e2e-driven-iteration, docs-as-DoD; skipping LDD is itself a finding

---

## Related Skills

- `corvinOS.review.gates` — Pre-review red-lines & gates
- `corvinOS.review.compliance` — GDPR, EU AI Act, security
- `corvinOS.review.frontend` — Web-UI, E2E, a11y
- `corvinOS.review.backend` — Logic, testing, coverage
- `corvinOS.review.quality` — Docs, diagrams, final gate
- `corvinOS.review.e2e` — **defines a real E2E** (real data/DB/LLM/A2A) for Phase 6
- `e2e-driven-iteration` / `per_subtask_e2e` — E2E rhythm; real-subprocess E2E for security code
