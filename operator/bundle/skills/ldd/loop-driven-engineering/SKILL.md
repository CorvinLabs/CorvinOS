---
name: loop-driven-engineering
description: Use at the start of any non-trivial engineering task (feature, bugfix touching more than one file, refactor with observable behavior change, incident response). Orchestrates the three LDD loops — inner (code), refinement (deliverable), outer (method) — with hard iteration budgets, dispatches the specialist skills at the right moments, and forbids declaring "done" without a synced doc-level mental model.
---

# Loop-Driven-Engineering

## Overview

Engineering is **three loops**, not one. You plan, you try, you measure, you diagnose, you try again — on the **code axis** (inner loop), the **deliverable axis** (refinement), and the **method axis** (outer loop). The thing that separates good engineering from flailing is choosing the right loop for the current loss, respecting each loop's budget, and not smuggling one loop's edits into another's.

**Core principle:** the loop closes when applicable gates are green **and** the doc-level mental model is current. Not when one test passes. Not when "it looks right." Not when the LLM sounds confident.

This skill is a **flexible pattern**, not a rigid procedure. Use judgment; adapt the structure to the task. But the budget, the escalation rule, and the loop-separation rule are hard.

See [`../../docs/ldd/convergence.md`](../../docs/ldd/convergence.md) for the three-loop model in full, and [`../../diagrams/three-loops.svg`](../../diagrams/three-loops.svg) for the picture.

## When to Use

Invoke at the **start** of any non-trivial engineering task:

- Feature touching ≥2 files or ≥1 public surface
- Bugfix where the cause is not yet identified
- Refactor with observable behavior change (even if spec-preserving)
- Incident response
- Plan-driven work (a multi-step design under time pressure)
- Any work where you are tempted to "just start coding"

Do **not** use for:
- Trivial edits (rename a variable, fix a typo, delete dead code)
- Pure lookups or explanations
- Tasks already framed by a written plan you're executing verbatim

## The Three Loops

LDD has three loops, each on a different parameter axis. Pick **one** at the start; running multiple simultaneously produces unstable gradients.

### Inner Loop (θ = code, the default)

```
read context (CLAUDE.md / README / relevant docs — only what the task needs)
  ↓
plan (dialectical-reasoning → concrete next step)
  ↓
loop(k ≤ K_MAX = 5):
    observe failing signal
    ↓
    reproducibility-first (is this real signal or noise?)
    ↓
    e2e-driven-iteration: run E2E → measure loss_k → diagnose → fix → rerun
    ↓
    if green AND task-complete: break
    if failed: invoke root-cause-by-layer + loss-backprop-lens, next iteration
    ↓
    if k == K_MAX: STOP — escalate with the required shape, do not grind on
  ↓
close: docs-as-definition-of-done → commit
```

### Refinement Loop (θ = deliverable)

Use when a deliverable exists, is "good enough but not great," and re-running from scratch would waste what worked. Dispatches `iterative-refinement`. Budget halves per iteration; stops on regression, plateau, or wall-time.

### Outer Loop (θ = skills / rubrics / prompts)

Use when the same rubric violation recurs across 3+ distinct tasks. Dispatches `method-evolution`. Requires a task suite and `Δloss_method` measurement; rollback on regression.

**Hard rule:** if you can't say which loop you're in, stop. The wrong loop doesn't just waste budget — it can regress the artifact it was meant to improve.

## The Budget: K_MAX = 5

**Five iterations per task, default.** If the loop has not closed after five, stop, write what failed and why, and escalate (to the user, to a colleague, to a different skill).

Why five: below that, you haven't explored; above it, you're flailing. Each extra iteration beyond five is a ~linear increase in sunk-cost bias and a ~geometric decrease in probability the next iteration will converge without a structural rethink.

**Hard rule:** when k hits K_MAX, produce:
1. Summary of what was tried each iteration
2. The specific gate / test / symptom that kept failing
3. The causal story at layer 4–5 (via root-cause-by-layer)
4. A proposed architectural step (via loss-backprop-lens: "the learning rate needs to be bigger")
5. Explicit ask for redirection — do **not** silently try a 6th iteration

## The Test Pyramid (cheap → expensive)

Run gates in order of cost. Only escalate when the current tier is green.

| Tier | Examples | When to run |
|---|---|---|
| 0. **Local thought** | Read the code you're changing; grep for callers; skim docs | Always, before any edit |
| 1. **Schema / lint / type** | `ruff`, `tsc --noEmit`, JSON / YAML schema validation | After every code edit |
| 2. **Unit tests** | `pytest tests/unit`, `npm run test:unit` | After tier-1 green |
| 3. **Integration** | Hits a DB / filesystem / fake HTTP server | After tier-2 green, for changes crossing module boundaries |
| 4. **E2E** | Real external services, full-stack, long runtime | Only when tier-3 green AND the change warrants it (behavior change, pre-release, public-surface delta) |
| 5. **Live / production-like** | Canary, shadow traffic, staged rollout | Only after tier-4, only for high-blast-radius changes |

**Anti-pattern:** running E2E to catch a typo. It would have been caught by tier 1 in 2 seconds. Know your pyramid.

**Anti-pattern:** skipping tiers 1–3 because "E2E will catch everything." It won't, and when it fails you will have no idea at which layer it broke.

## Sub-Skill Dispatch

This is a composition skill. Dispatch the right sub-skill at the right moment. Entries from **this plugin** are always available. Entries from other plugins (shown as *external*) are optional — if not installed, apply the principle inline.

| Moment in the loop | Sub-skill | Source | Why |
|---|---|---|---|
| Observing a failing signal, before any edit | `reproducibility-first` | this plugin | One sample is noise, not gradient |
| Inside a fix-loop, every iteration | `e2e-driven-iteration` | this plugin | Measure loss each iteration, don't guess |
| Debugging a failing gate / test / run | `root-cause-by-layer` | this plugin | Find the structural origin, not the surface symptom |
| Deciding edit size / one-off vs recurring | `loss-backprop-lens` | this plugin | Step size must match the loss pattern |
| Any non-trivial recommendation, plan, trade-off | `dialectical-reasoning` | this plugin | Force the counter-case before shipping an opinion |
| Deliverable is good-enough but not great | `iterative-refinement` | this plugin | Polish with a real gradient (y-axis) |
| Same rubric violation in 3+ tasks | `method-evolution` | this plugin | Evolve the skill itself (θ-axis) |
| Release-candidate / weekly / before version bump | `drift-detection` | this plugin | Find cumulative drift that per-commit gates missed |
| Before committing / pushing / declaring done | `docs-as-definition-of-done` | this plugin | Sync docs to current behavior |
| Open-ended problem framing | `brainstorming` | external (superpowers) | Explore before committing |
| Writing a multi-step plan | `writing-plans` | external (superpowers) | Externalize the plan |
| Red-green discipline while coding | `test-driven-development` | external (superpowers) | Test before implementation |
| Before declaring "success" / "it's fixed" | `verification-before-completion` | external (superpowers) | Evidence before assertion |
| Before merging | `requesting-code-review` | external (superpowers) | Second set of eyes |

You are **not** required to invoke all of them on every task. Pick what the loop actually needs at this step. But you **are** required to invoke the right one at the right moment — skipping `reproducibility-first` on a flake, or skipping `docs-as-definition-of-done` on a behavior change, is a process failure. When an external sub-skill is not available, apply its principle inline (draft the plan in the conversation rather than loading a plan-writing skill).

## Red Flags — STOP, the loop is broken

- "I've tried this same fix 3 times, let me try a 4th variant" → local-minimum trap; invoke `loss-backprop-lens`, consider architectural step
- "The same pattern keeps happening across different tasks" → outer-loop signal; invoke `method-evolution`
- "I'll run E2E to see what breaks" (skipping tiers 1–3) → pyramid violation
- "I'll just commit and iterate in follow-ups" → budget discipline failure; write a plan
- "It failed / passed once, retry" → invoke `reproducibility-first`; one sample is not a gradient
- "This is iteration 6, one more should do it" → K_MAX violation; stop and escalate
- "Docs are fine for now, behavior change is subtle" → invoke `docs-as-definition-of-done`
- "I'll regenerate the doc from scratch instead of refining" → you conflated refinement with re-planning; invoke `iterative-refinement`
- "I'll edit the skill to match what just happened" → potential moving-target loss; invoke `method-evolution` with proper measurement
- "I don't need a plan, it's a small change" → "small" is the canonical rationalization for no-plan
- Same gate rejecting with the same reason ≥ 2 iterations → stagnation; abort, diagnose, restart with a fix at the structural origin
- No full drift scan in months on an active project → silent accumulation; invoke `drift-detection`

## Escalation on Non-Convergence

When you can't close the loop, the productive response is **not** to try harder — it's to communicate. The right escalation has:

- **What was tried** — concrete, per iteration.
- **What kept failing** — the gate, test, or symptom, verbatim.
- **The layer-4/5 diagnosis** (via root-cause-by-layer).
- **The step-size recommendation** (via loss-backprop-lens): is this a local-fix problem that needs fresh eyes, or a structural problem that needs a different design?
- **The explicit ask** — what input do you need to proceed?

A well-escalated non-convergence beats a silently-limping sixth iteration every time.

## How to Apply — checklist

1. **Context read** — only what the task needs. If the project has a `CLAUDE.md` / `AGENTS.md`, load it. Load the relevant docs. Do **not** read the whole repo.
2. **Plan** — one paragraph, run it through `dialectical-reasoning` if non-trivial. For multi-step work, write it out (use a plan-writing skill if available, otherwise draft the plan in the conversation).
3. **Enter loop.** k = 0, K_MAX = 5.
4. **For each iteration:** smallest coherent code change → fast gates → diagnose failures via sub-skills → decide step size.
5. **Close** — run docs-as-definition-of-done, verify completion with a verification skill if available, commit as one logical unit.
6. **If K_MAX hit** — escalate with the checklist above.

## Counter-Examples — when this skill is overkill

- You're renaming a local variable. Just rename it.
- You're fixing a typo in a README. Fix it, commit.
- You're answering a factual lookup question. Read and answer.

The skill is for **multi-step engineering under uncertainty**, not for every interaction.

## Real-World Shape

Good loop (feature: add `--dry-run` to `myapp deploy`):
1. Read `cli.py`, `deploy.py`, relevant README sections. (1 min)
2. Plan: dialectical pass — "thesis: add a flag, gate execution on it; antithesis: dry-run could leak partial state if deploy has side effects in plan-phase; synthesis: separate `plan()` from `execute()`, flag only skips `execute()`." (3 min)
3. k=1: refactor `plan()`/`execute()` split, unit tests for both. Tier-1+2 green. (15 min)
4. k=2: add flag, test dry-run path. Tier-1+2+3 green. (10 min)
5. Close: `docs-as-definition-of-done` — README L82 safety note, cli-reference table, user-guide preview section. (5 min)
6. Commit as one logical unit. Done in 2 iterations, budget to spare.

Bad loop (same task, no discipline):
1. Start coding the flag. k=1: works, but broke side-effect ordering. k=2: patch the ordering, broke a different test. k=3: patch that, broke idempotency. k=4: flaky. k=5: ship with retry. k=6: "one more fix." k=7: push through freeze window with TODO on README. Two weeks later, incident.

## Related / Composed

**In this plugin (always available):**
- **`reproducibility-first`** — gate before any gradient use
- **`root-cause-by-layer`** — the 5-layer debugging ladder
- **`loss-backprop-lens`** — edit-size calibration and signal-vs-noise
- **`e2e-driven-iteration`** — measure-every-iteration inner-loop rhythm
- **`dialectical-reasoning`** — planning and recommendations
- **`iterative-refinement`** — y-axis (deliverable) optimization
- **`method-evolution`** — θ-axis (skill / rubric) outer-loop optimization
- **`drift-detection`** — periodic full-repo scan for cumulative drift
- **`docs-as-definition-of-done`** — the close of the loop

**Optional companions** (Claude Code `superpowers` plugin, Codex built-ins, or equivalent):
- **`brainstorming`** — open-ended problem framing
- **`writing-plans`** — externalized multi-step plans
- **`test-driven-development`** — red-green discipline when writing code
- **`verification-before-completion`** — evidence before success claims
- **`systematic-debugging`** — overlaps with `root-cause-by-layer`; prefer the 5-layer ladder for the explicit discipline, the broader investigation framing when you need to *find* the bug rather than diagnose one already localized
