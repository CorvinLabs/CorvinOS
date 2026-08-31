---
name: using-ldd
description: Use at the start of any conversation, and whenever the user mentions LDD, loss-driven development, loss, gradient, SGD on code, drift, refinement loop, outer loop, inner loop, method evolution, or says "LDD:" / "apply LDD". Establishes how to dispatch the ten LDD skills and when each fires. Must be the first skill considered in any coding task where LDD is available.
---

# Using LDD — the bundle entry-point

## Overview

Loss-Driven Development (LDD) is a ten-skill bundle that treats code changes as SGD steps and forbids overfitting to the current test. This entry-skill exists so you (the agent) know **when to reach for which LDD skill** without the user having to name each one by hand.

**Core principle:** if the user prefixes their message with `LDD:` or mentions any of the trigger phrases below, LDD discipline is explicitly requested. Match their intent to the right sub-skill and announce which one you are applying.

## Trigger phrases (user → skill mapping)

When the user's message contains any of these patterns, invoke the paired skill.

| User signal | Invoke |
|---|---|
| `LDD:` prefix, "apply LDD", "use LDD" | Enter full LDD mode — default entry = `loop-driven-engineering` unless a more specific sub-skill matches below |
| "failing test", "CI is red", "flaky", "one-off failure", "intermittent" | First `reproducibility-first`, then `root-cause-by-layer` if the failure is real |
| "bug", "error", "exception", "unexpected behavior", "why is this breaking" | `root-cause-by-layer` |
| "I've tried this 3 times", "keeps failing the same way", five fix-commits in one area | `loss-backprop-lens` (local-minimum trap) |
| "this fix works but I'm not sure it generalizes", "will this break sibling tests" | `loss-backprop-lens` (generalization check) |
| "I'm mid-debug", "is the fix done yet", before closing any fix-loop | `e2e-driven-iteration` |
| "should we ship this", "is this the right approach", design trade-off | `dialectical-reasoning` |
| "this doc/diff/output is okay but could be better", "polish this" | `iterative-refinement` |
| "same thing keeps happening across tasks", "the skill itself might be wrong" | `method-evolution` |
| "is this codebase healthy", release-candidate review, weekly check | `drift-detection` |
| "about to commit", "ready to merge", "declaring this done" | `docs-as-definition-of-done` |

## The "LDD:" buzzword

Users who want **guaranteed** LDD activation (no reliance on auto-triggering) can prefix their message with `LDD:`. When you see this prefix:

1. Load `loop-driven-engineering` as the orchestrating skill
2. Before any code, announce in one sentence which sub-skill you are reaching for and why
3. Apply that sub-skill's discipline literally, not "in spirit"
4. Report which rubric items you satisfied at the end of the task

Example:

> User: `LDD: the checkout test is failing and I need to ship in an hour`
> You: "Invoking `reproducibility-first` first to check whether this is a real gradient or noise. [runs check] Confirmed reproducible — invoking `root-cause-by-layer` next to diagnose at layer 4/5 before editing."

## Announcing skill invocation

Every time you invoke an LDD skill — whether auto-triggered or via `LDD:` — **say which skill you are invoking, in one sentence, before applying it**. This is non-negotiable; it lets the user verify which discipline is in effect and override you if you picked wrong.

Format: `*Invoking <skill-name>*: <one-line reason>.`

Example: `*Invoking root-cause-by-layer*: the symptom is a contract-violation `TypeError`; walking the 5-layer ladder before proposing a fix.`

## The three loops — which one is active

LDD distinguishes three optimization loops. Name which one you're in before iterating:

| Loop | You edit | When |
|---|---|---|
| **Inner** | Code | Ordinary bug / feature / refactor |
| **Refinement** (y-axis) | A deliverable (doc, diff, design) | "Good enough, not great" — polish |
| **Outer** (θ-axis) | A skill / rubric | Same rubric violation across ≥3 tasks |

If you cannot name which loop is active, stop and ask. Running the wrong loop wastes budget and can regress the artifact.

## Minimum compliance behaviors

Even without an explicit `LDD:` prefix, if the bundle is installed you are expected to:

1. **Never ship a symptom patch** (`try/except`, `hasattr`-shim, retry loop, `xfail`) without first walking `root-cause-by-layer` to layers 4–5.
2. **Never update code based on a single failed run** without invoking `reproducibility-first`.
3. **Never declare "done"** without running `docs-as-definition-of-done` against the touched files.
4. **Never give a non-trivial recommendation** without running `dialectical-reasoning` (thesis → antithesis → synthesis).
5. **Never exceed K_MAX=5 iterations** silently — escalate per `loop-driven-engineering` § Escalation.

## When LDD is not applicable

Skip the bundle for:
- Trivial edits (rename a variable, fix a typo, delete dead code you already understand)
- Pure lookups (what does this function do — read it and answer)
- Pure factual questions with no code change involved

The bundle is for **multi-step engineering under uncertainty**, not for every keystroke.

## Discovery order for a new session

At session start, if LDD is installed:
1. Read this skill's description.
2. Note that the other nine are available.
3. Apply the trigger-phrase table above to the user's first message.
4. If the first message doesn't clearly trigger any specific skill, proceed normally — LDD discipline activates when a trigger appears later.

## Compatibility with `superpowers`

If the `superpowers` plugin is also installed:
- `loop-driven-engineering` will dispatch to `superpowers:brainstorming` / `writing-plans` / `test-driven-development` / `verification-before-completion` / `requesting-code-review` at the moments noted in its dispatch table.
- `superpowers:systematic-debugging` overlaps with `root-cause-by-layer`: prefer `root-cause-by-layer` when you want the explicit 5-layer discipline, `systematic-debugging` for broader investigation framing.

## Signal that LDD is working

A session with LDD correctly applied shows:
- At least one `*Invoking <skill>*:` announcement per non-trivial task
- Commit messages that name layers / structural origins / Δloss when relevant
- No symptom-patch constructs shipped under pressure
- Doc updates in the same logical commit as behavior changes

If none of these appear in a long session, LDD is installed but dormant — re-check the trigger-phrase table, or ask the user to prefix `LDD:` explicitly.
