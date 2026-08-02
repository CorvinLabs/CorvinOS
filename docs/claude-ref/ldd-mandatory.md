# LDD (Loss-Driven Development) — MANDATORY at MAXIMUM

**LDD is ALWAYS ON at MAXIMUM depth on CorvinOS.** All 12 layers enabled. This is a hard
project-level constraint that applies to every task, every session.

Configuration: `.corvin/tenants/_default/global/ldd.json` (all layers `true`).
Auto-install hook: `LDD_AUTO_OPTIN=1` in `~/.bashrc` — every new session triggers LDD install.

## The Mandate

**LDD is not optional and not advisory.** It is **always on, at full depth**,
regardless of whether the user typed `LDD:` or not.

Every task — feature, bugfix, refactor, review, test, doc change — runs through the appropriate
LDD skill before any edit is made or any conclusion is stated.

## Mandatory Skill Dispatch

| Task Type | Mandatory Skill | When to Fire |
|---|---|---|
| Non-trivial feature / multi-file change | `loop-driven-engineering` | **BEFORE first edit** |
| Failing test / bug iteration | `e2e-driven-iteration` | **BEFORE each iteration** |
| Recommendation / trade-off / plan | `dialectical-reasoning` | **BEFORE stating conclusion** |
| Bug with unknown root cause | `root-cause-by-layer` | **BEFORE any fix attempt** |
| Any task marked "done" | `docs-as-definition-of-done` | **BEFORE declaring done** |
| Reusable working method discovered/reapplied (non-trivial task) | Concept Gate | **AFTER declaring done, alongside ADR Gate** |
| First observation of a failure | `reproducibility-first` | **BEFORE any diagnosis** |
| Per-subtask E2E for security changes | real subprocess + real filesystem + real bwrap | **MANDATORY, no exception** |

## Hard Rules (Must NOT do)

1. **Don't make ANY non-trivial code edit** without first running the E2E and capturing a loss signal.
   - Non-trivial = touching more than one file, or a single-file change with >3 logical steps
   - Loss signal = test failure, observable behavior gap, error message

2. **Don't declare a task "done"** without running `docs-as-definition-of-done`.
   - This skill verifies that mental model + code + docs + tests align
   - Skip detection: if you haven't run the skill, the task isn't done

3. **Don't patch a symptom** without naming the structural origin via `root-cause-by-layer`.
   - Why did this fail? Which layer? Which invariant was violated?
   - Surface-level fixes cause regressions

4. **Don't present a recommendation** without a dialectical pass.
   - Thesis / antithesis / synthesis — every recommendation must survive its strongest counter-case
   - Unexamined recommendations are overconfident

5. **Don't skip LDD because the task "looks small"** — every skipped LDD step is technical debt.
   - "Small" is a heuristic that fails silently (lots of small skips = big problems)
   - The skill determines task size, not human judgment

6. **Don't downgrade to LDD-off or partial-LDD** via any env var or config flag.
   - No `LDD_DISABLED=1`, no `--skip-ldd`, no "compliance-off mode"
   - LDD-MAX is the only valid state; partial LDD is self-defeating

7. **Don't treat a single observation as a gradient** — always invoke `reproducibility-first` on first failure signal.
   - One test fail ≠ a reliable loss signal
   - Reproducibility-first captures the failure mode before diagnosing it

## Three Inner Loops

LDD structures work into three concurrent loops:

### Inner Loop: Code-to-Loss
- Edit code → run E2E → observe loss (test fail, error, behavior gap)
- Cycle time: ~60 s per iteration
- Mandatory for every edit; can't skip

### Refinement Loop: Loss-to-Deliverable
- Loss identified → root-cause-by-layer → fix → run `docs-as-definition-of-done`
- Cycle time: ~5–30 min per bug/feature
- Mandatory before declaring "done"

### Outer Loop: Method Evolution
- Track patterns (recurring causes, repeated failures, skills that help)
- Cycle time: ~1 week per session or project
- Detect and fix meta-level issues (test infrastructure, doc gaps, process overhead)

## Related

- `/using-ldd` — How to invoke LDD skills (entry-point skill that routes to the 12 layers)
- `/loop-driven-engineering` — Structures non-trivial implementation tasks
- `/e2e-driven-iteration` — Fixes a failing test or closes a loss signal
- `/dialectical-reasoning` — Validates recommendations and trade-offs
- `/root-cause-by-layer` — Finds the structural origin of a bug
- `/docs-as-definition-of-done` — Verifies alignment (mental model + code + docs + tests)
- `/reproducibility-first` — Captures failure mode before diagnosis

## Compliance Note

LDD is also a **compliance mechanism** (ADR-0145): it forces explicitness about why code changes
and what they fix, creating an audit trail that supports GDPR accountability.

→ Full LDD reference: See the skill system itself (all 12 skills are shipped with CorvinOS).
