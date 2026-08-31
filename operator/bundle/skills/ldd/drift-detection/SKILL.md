---
name: drift-detection
description: Use periodically (weekly, at release candidates, before major version bumps) to scan a codebase for cumulative drift that no per-commit gate catches. Complements docs-as-definition-of-done, which prevents drift from being introduced per commit; this skill surfaces drift that already happened despite per-commit gates.
---

# Drift-Detection

## Overview

**Drift is divergence on a timescale longer than one work session.** Twenty individually-reasonable commits can compose into a system whose mental model no longer matches the code. Per-commit gates (`docs-as-definition-of-done`) cannot catch it because no single commit violates a rule — the violation is the **cumulative pattern**.

**Core principle:** drift accumulates silently. Detecting it requires a periodic full-system scan, not per-edit vigilance. The scan produces a finite list of drift indicators; each becomes a candidate for immediate fix or a `method-evolution` step.

See [`../../docs/ldd/convergence.md`](../../docs/ldd/convergence.md) §4 for the drift taxonomy.

## When to Use

Invoke on a **periodic cadence**, not per-commit:

- Weekly on active projects (bundled with release-candidate review)
- Before a major version bump or breaking-API release
- After onboarding/offboarding a contributor (someone leaves; check for orphaned mental models)
- After a long sprint of many small commits in one area
- Before a documentation overhaul (know what's wrong before you rewrite)
- After a refactor that touched >20 files

Do **not** use:

- On every commit (that's `docs-as-definition-of-done`'s job; this skill is slower and more expensive)
- On small projects with few contributors (drift is a large-codebase / many-contributor pathology)
- During active development (it generates noise, you'll ignore it; run it at stable points)

## The Seven Drift Indicators

Each is concrete and checkable. Score each as `found` / `not found`; the report is the union of all findings.

### 1. Identifier drift

**Symptom:** the same concept named two or three different ways in different modules.

**Detection:** grep for known concept synonyms: `user_id|userId|uid|user`; `request_id|reqId|rid`; `total|sum|aggregate`. Flag any where 2+ forms exist in the same codebase.

**Example:** `user_id` in the DB layer, `userId` in the API response types, `uid` in the logging code. Same thing, three names.

### 2. Contract drift

**Symptom:** the same logical interface has subtly different shapes at different call sites.

**Detection:** for a function/method called from ≥ 3 places, compare the argument lists / types / return handling. Flag inconsistencies.

**Example:** `authenticate(user, password)` in one module, `authenticate(username=user, password=password, otp=None)` in another, `authenticate({...credentials})` in a third. Drift happened when optional params were added without backporting call sites.

### 3. Layer drift

**Symptom:** files that used to live in one architectural layer now import from two. The module-import graph has grown cycles or illegal cross-layer edges.

**Detection:** generate the import graph. Compare against the architecture doc's intended layer structure. Flag edges that cross documented layer boundaries or form cycles.

**Example:** `domain/user.py` started importing from `persistence/postgres.py` to avoid a conversion layer. Three weeks later, the domain layer depends on Postgres, silently.

### 4. Doc-model drift

**Symptom:** README architecture diagrams / concept descriptions / module listings describe a system that no longer exists.

**Detection:** re-generate the module dependency graph. Diff against the documented architecture. Flag any mismatch.

**Example:** README shows 4 services; the repo contains 7. README describes a message queue; there isn't one anymore. The 4-service / no-queue narrative was true 6 months ago.

### 5. Rubric drift

**Symptom:** evaluation rubrics / test assertions have been edited without a logged `Δloss_method` or justification.

**Detection:** `git log` on rubric-carrying files (`evaluation.md`, `tests/fixtures/*/rubric.md`, top-level test-expectation files). Every change should have a commit message explaining why AND a measured impact. Flag edits without.

**Example:** `rubric.md` used to require "all 5 layers named." It now requires "at least 3 layers named." No commit message explains the relaxation.

### 6. Test/spec drift

**Symptom:** tests (behavior) and specs/docs (promises) have diverged. Specs assert things tests never check, or tests enforce behaviors specs don't mention.

**Detection:** enumerate documented promises (spec-level invariants, API contract guarantees). For each, grep tests. Flag promises without tests and tests without spec-level justification.

**Example:** Spec says "idempotent-by-order-id." No test verifies idempotency. Tests exhaustively check rate-limiting that the spec never mentions.

### 7. Defaults drift

**Symptom:** the "default value" of a configuration key is stated differently in README, in code, in tests, in migrations.

**Detection:** for each documented default, grep all surfaces (`README`, source, tests, migrations, example configs). Flag any where values differ.

**Example:** README says default timeout is 30s. `config.py` has `DEFAULT_TIMEOUT = 60`. Test fixture uses 45. Migration sets 30. Four answers.

## The scan — what it produces

A drift scan produces one artifact: a **drift report** with one row per indicator found. Not a "health score." Not a green checkmark. A list of specific, fixable issues.

```markdown
# Drift scan — 2026-05-04

## Identifier drift
- `user_id` (38 occurrences) vs `userId` (12 occurrences). Modules using `userId`: api/, frontend/.

## Contract drift
- `parse_date(s)` in utils/dates.py vs `parse_date(s, fmt=None, tz=None)` in integrations/google/ — second form unused by utils callers.

## Layer drift
- `domain/user.py:12` imports `persistence.postgres.Session` (crosses documented boundary).

## Doc-model drift
- README describes 4-service architecture (§2); repo contains 7 services (services/).

## Rubric drift
- `evaluation.md` commit 0ed546c edited Δloss_bundle target from 3.0 to 2.0 without recorded reason.

## Test/spec drift
- `spec/api.md` promises idempotency on POST /charges; no test covers a duplicate charge.

## Defaults drift
- `DEFAULT_TIMEOUT`: README=30, code=60, test=45, migration=30.
```

Each row is either (a) immediately fixable — handle like any bug, invoke `root-cause-by-layer` and fix; or (b) a pattern suggesting the method itself allowed the drift — candidate for `method-evolution`.

## Red Flags — STOP, the scan isn't working

- "The scan returned nothing" → either your project is tiny, your indicators are wrong, or you're scanning stale state
- "We'll fix these later" → unfixed drift compounds; schedule the fixes
- "The scan takes an hour, skip this cycle" → you saved an hour and lost the drift signal; next week's drift will include more
- "One indicator is too strict" → raise it in `method-evolution`, don't silently drop the indicator
- Reading the scan without acting on any row → you've turned the scan into theater

## Automation surface (optional, Claude-Code-targeted)

`scripts/drift-scan.py` in the `scripts/` directory runs the seven indicators as a single pass. Requires `git`, `grep`, and a Python 3.10+ environment. Not required for the skill itself — you can run the seven indicators manually. Provided for Claude-Code users who want to cron it.

## Relation to other skills

- **`docs-as-definition-of-done`** prevents drift per-commit. This skill detects drift that accumulated anyway.
- **`root-cause-by-layer`** fixes each drift indicator (each indicator is a bug).
- **`method-evolution`** handles patterns in the scan — if indicators 3, 5, 6 all fire repeatedly, the *method* that allows this is the target of evolution.
- **`loop-driven-engineering`** dispatches this skill at release-candidate time or on a scheduled cadence.

## How to Apply — checklist

1. **Pick the scan cadence.** Weekly is aggressive; biweekly is realistic for most projects.
2. **Run the seven indicators.** Manually or via `scripts/drift-scan.py`.
3. **Read every row of the report.** Not just the count.
4. **Triage.** Immediate fix (single instance) vs method-evolution (pattern across multiple indicators).
5. **Schedule the fixes.** Small issues: in the current sprint. Large issues: next sprint, with `loop-driven-engineering` discipline.
6. **Log the scan.** Even if the report is short. Scan history is outer-loop data.

## Common Rationalizations

| Excuse | Reality |
|---|---|
| "We don't have drift, we review carefully" | Careful review prevents *new* drift; it does not detect *existing* drift. |
| "The scan is too slow" | Then automate a subset on every CI run; do the full scan monthly. |
| "Most findings are cosmetic" | Cosmetic drift is how non-cosmetic drift hides. |
| "Small team, we know the codebase" | Today you do. The next contributor / the next LLM session does not. |
| "The scan finds too much to fix" | Prioritize. A finding is not a commitment to fix today; it's an entry in the queue. |

## Real-World Cue

If your project has >3 months of active development and you've never run a drift scan, your first scan will find things. That is not an indictment — it is the starting state. The scan's value is the trend over time: does the drift backlog shrink, stabilize, or grow? Growing drift is a method-evolution signal.
