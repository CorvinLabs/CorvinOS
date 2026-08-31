---
name: dialectical-reasoning
description: Use when making any non-trivial recommendation, analysis, plan, design decision, code-review note, or architectural trade-off — before presenting to the user or acting. Forces a thesis / antithesis / synthesis pass so first-pass conclusions cannot ship without surviving their strongest counter-case.
---

# Dialectical-Reasoning

## Overview

First-pass reasoning tends to overfit to the framing of the prompt. A proposal that has not survived its own strongest counter-case is a **symptom patch at the reasoning layer** — it will look right and fail in production, code review, or the next conversation.

**Core principle:** Every non-trivial recommendation must go through thesis → antithesis → synthesis **before** it leaves your mouth. The synthesis — not the thesis — is the admissible output.

**Violating the letter of the dialectic is violating the spirit.** "I already know the answer, I'll skip the antithesis" is the standard skip; it is forbidden. The discipline is the **externalization**, not the internalization.

## When to Use

Apply before presenting or acting on any of:

- A recommendation (ship / don't ship, choose X over Y, use library Z)
- A debugging hypothesis ("the bug is in X because Y")
- An architectural trade-off (split the service, add a cache, rewrite in language X)
- A code-review note arguing for a change
- A plan for multi-step work
- A "yes this is fine" sign-off on someone else's proposal

## When Not to Use

- Trivial mechanical tasks (rename a variable, run a test, read a file)
- Pure lookups (what does this function do? read the code)
- Answering a direct factual question where there is no judgment call

The rule: **if a second competent reviewer could disagree with your output, it needs an antithesis first.**

## The Three Moves

### 1. Thesis — state the proposal concretely

- What exactly is claimed?
- What will change?
- What is expected to hold afterwards?
- What is the load-bearing assumption?

State it in one paragraph, in its strongest form. Steel-man it, don't strawman.

### 2. Antithesis — attack it as hard as possible

Switch roles. You are now the hostile reviewer, the future maintainer, the production incident that happens six months in. **Your job is to break the thesis, not to defend it.**

At minimum, attack:

- **Hidden assumptions.** What is the thesis quietly assuming (about scale, about callers, about invariants, about the environment)?
- **Edge cases.** What input / state / timing / concurrency breaks it?
- **Contracts and invariants.** Which written or unwritten contract does it strain?
- **Second-order effects.** What does it make harder later? What does it couple?
- **Alternative framings.** Is the problem itself framed correctly, or is the thesis solving the wrong problem?
- **Asymmetric risks.** What's the cost if you're wrong? Reversible or not?
- **Who disagrees?** Name a reasonable person who would reject this, and why.

A weak antithesis ("I guess it could be slightly slower") is a failed antithesis — the thesis does not count as surviving.

### 3. Synthesis — strictly stronger than the thesis

Reconcile. The synthesis must be one of:

- **More correct** (a sharper version the antithesis forced you to)
- **More narrowly scoped** (the thesis was right for the subset the antithesis didn't break)
- **More honestly hedged** (the thesis is right under stated conditions; those conditions are explicit)
- **Replaced** (the antithesis won; the thesis is discarded)

"The thesis was right" is **only** a valid synthesis after a real antithesis — not as a replacement for one.

## Red Flags — STOP, you skipped the dialectic

These phrases, in your own draft or thinking, mean you stopped at thesis:

- "This is obviously the right call because…"
- "I think we should just…"
- "The clean solution is to…"
- "It's a simple matter of…"
- "Everyone agrees that…" (have you asked a dissenter?)
- "This is a no-brainer"
- "The tradeoff is clearly worth it" (without naming the tradeoff)
- Any recommendation without an explicit "but" / "however" / "on the other hand"
- Any plan with only happy-path assumptions named

When one fires: restart at the antithesis. Find the strongest counter-case before continuing.

## Antithesis Checklist

A good antithesis hits at least 3 of these:

- [ ] Names a load-bearing assumption and questions it
- [ ] Surfaces at least one edge case / failure mode
- [ ] Identifies a contract or invariant under strain
- [ ] Points to a second-order cost (coupling, future work, blast radius)
- [ ] Proposes an alternative framing of the problem
- [ ] Explicitly states: "A reasonable person would reject this because…"

If you can check none of these, your antithesis is cosmetic. Try again.

## Presenting to the User

Show the **synthesis** as the recommendation. Surface the antithesis only when the tension is **load-bearing** — when the user needs to see the rejected alternative to judge the trade-off themselves.

Do not perform the dialectic for its own sake. The output is a sharper decision, not a longer message. A two-sentence synthesis that survived a real antithesis beats a four-paragraph analysis that didn't.

**However:** when the stakes are high or the user is weighing options, showing the structured thesis/antithesis/synthesis makes the reasoning auditable. Know your audience.

## Common Rationalizations

| Excuse | Reality |
|---|---|
| "I already considered the counter-arguments, just didn't write them" | Internalization ≠ discipline. The externalization is the check. Write it. |
| "It's obvious, don't patronize the user" | "Obvious" is the canonical cover for skipping the antithesis. Write the counter anyway. |
| "Antithesis for every trivial decision slows me down" | Yes — that's why the "when not to use" section exists. For non-trivial, skipping is cheaper in your time but more expensive in the user's. |
| "The user wants the answer, not a debate" | The user wants the **right** answer. A sharper synthesis is shorter, not longer. |
| "Devil's advocate feels performative" | Performative antithesis (weak attacks) IS the failure mode. Attack hard. A real antithesis changes the synthesis. |
| "I can hold both sides in my head" | Maybe. But you can't prove you did unless you write them. Reviewers cannot audit a private dialectic. |

## The failure mode this skill prevents

Without an enforced dialectical shape, a response on a high-stakes design question (retries for a flaky API, dedup of user records, service split) tends to come out mono-directional — substantive but one-sided, missing the explicit "thesis / but / therefore" auditability. The hidden assumption goes unchallenged; the reviewer has no way to verify both sides were actually considered.

With this skill: the reply labels thesis, attacks ≥ 3 vectors in the antithesis (hidden assumptions, edge cases, contracts under strain, irreversibility, asymmetric risk), and produces a synthesis that is *strictly stronger* than the thesis — narrower scope, sharper conditions, or reframed problem.

Scenario + rubric + measured behavior: `tests/fixtures/dialectical-reasoning/`.

## Related

- **`root-cause-by-layer`** — the antithesis of a debugging hypothesis is "which layer did you skip?"
- **`loss-backprop-lens`** — the antithesis of a proposed fix is "does this generalize, or only pass the current test?"
- **`docs-as-definition-of-done`** — the antithesis of "done" is "what doc statement is now false?"
