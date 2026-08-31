---
name: root-cause-by-layer
description: Use when encountering a bug, failing test, or unexpected behavior — especially under time pressure where try/except, retry loops, xfail, type-tolerance shims, "I'll clean up later," or compat-for-both-shapes feel like the fastest path. Forbids symptom patches until structural and conceptual origins are named.
---

# Root-Cause-by-Layer

## Overview

A bug is a **symptom**. The disease almost always lives at a layer boundary, a contract violation, or a conceptual mismatch — rarely on the line that threw the exception. Walk the 5-layer ladder before proposing any fix.

**Core principle:** A change that turns the symptom green without a causal story at layers 3–5 is a **suppression, not a cure.** It re-emerges in a different guise within a few iterations.

**Violating the letter of the layered analysis is violating the spirit.** "I know the cause already, I'll skip writing it out" is the most common skip — write it down anyway. The discipline is the externalization.

## When to Use

Any of these triggers means invoke this skill **before** proposing code:

- A test is red or flaky and you want to ship a fix
- An exception, type error, assertion failure, or unexpected output
- A CI gate is rejecting and you're considering widening the rule
- An LLM/tool returned a malformed result and you're considering a retry/regex
- You catch yourself typing `try:`, `hasattr(`, `@pytest.mark.skip`, `# TODO clean up`, or a retry loop as the primary fix
- Deadline / sunk-cost pressure is telling you "just get it green"

Do **not** use for: cosmetic edits, rename refactors, adding new features with no failing signal. This skill is for *diagnosing*, not *building*.

## The Five Layers

Apply in order. You do **not** have a root cause until you can answer **both 4 and 5** out loud.

1. **Symptom** — The exact error, failed assertion, rejected output, wrong number. Copy the raw text. Do not paraphrase.
2. **Mechanism** — Which code path produced the symptom? Name the function, branch, and state that led to it.
3. **Contract** — Which input/output shape, type, invariant, or documented promise did the mechanism violate? **Whose** contract — the caller's or the callee's? Is the contract explicit (signature/docstring/schema) or only implicit?
4. **Structural origin** — Which architectural layer is this in (domain, integration, transport, persistence, UI, config, …)? Is the bug *inside* that layer, or is it a **leak across** a boundary?
5. **Conceptual origin** — Which design concept is being misapplied: separation of concerns, ownership, single source of truth, explicit-over-implicit, data-flow direction, invariant location?

Only once 4 and 5 are named is a fix admissible. The admissible fix is **at the layer you named** — not above, not below.

## Red Flags — STOP, you are about to symptom-patch

These phrases, appearing in your own thinking or answer, mean you skipped layers 3–5:

- "Just make it tolerant to both shapes"
- "Add a `try/except` so it doesn't blow up"
- "I'll clean up later / mark as tech debt / file a ticket"
- "It's a smaller change than fixing it properly"
- "The other callers still rely on the old shape" *(have you grepped?)*
- "Under time pressure this is good enough"
- "Widen the regex / threshold / whitelist"
- "Retry loop will handle the flakiness"
- "`@pytest.mark.skip` / `xfail` for now"
- "Hardcode the value that works"
- "Cache so we don't hit the broken path"
- "LLM was probably bad, just retry"

When one fires: stop, re-enter at layer 2, walk up.

## Anti-Pattern Catalog

| Anti-pattern | Why wrong | Do instead |
|---|---|---|
| `try/except` around the failing call | Hides contract violation; corrupted state flows downstream | Find the caller that violates the input contract; fix at that boundary |
| `hasattr`-or-subscript / duck-typing shim | Normalizes two implicit contracts into one ambiguous one | Pick ONE explicit type; convert at the boundary |
| Widening regex / threshold / whitelist until test passes | Treats the test as the spec; real spec is the contract | Ask what the rule *intends*; honor that |
| Retry loop around a flaky step | Normalizes non-determinism | Trace non-determinism to its source, remove it there |
| Hardcoding a "works for now" value | Freezes one case, breaks for the next | Derive the value from the contract that should produce it |
| `@pytest.mark.skip` / `xfail` to unblock | Converts a loss signal into silence | Fix root cause; if the test is wrong, fix the test **with** written justification |
| Patching log/UI so the error doesn't show | Error is still there, just invisible | Surface is a read-only view; fix reality |
| Adding a compat shim "for existing callers" | Usually there are NO existing callers | Grep for callers **first**; fix the single real caller |
| "LLM / network / filesystem was flaky" | Abdicates diagnosis | Capture the exact payload; diagnose which contract its output violates |

## How to apply — checklist

1. Read the symptom **verbatim** and paste it into your answer.
2. For each of layers 2–5, write one sentence. If you can't, read more code — do **not** guess.
3. If a Red Flag fired during step 2, delete your draft fix and restart at layer 2.
4. State the admissible fix at the layer you named in 4/5. Resist the urge to fix one layer higher (too shallow) or one lower (too invasive).
5. Add a regression test **at the layer of the root cause**, not at the layer of the symptom. A diagnosed-as-contract bug gets a contract-level test, not another end-to-end retry.
6. If time pressure is real, the minimal *structural* fix is almost always smaller than the "quick" symptom patch *plus* the follow-up cleanup it promises. Ship the structural fix.

## Common Rationalizations

| Excuse | Reality |
|---|---|
| "Deadline — clean up later" | Tech debt from symptom patches compounds faster than any deadline saves. "Clean up later" almost never happens, and when it does it's 3× the original cost. |
| "Make it tolerant so both shapes work" | Tolerance = two implicit contracts living together. Pick one explicit contract. |
| "Other callers might rely on old shape" | Did you `grep`? 90% of the time there are none — you invented them. |
| "Retry handles the flakiness" | Flakiness has a cause. Retry masks, it does not fix. You will see the same bug next week. |
| "Smaller diff is safer" | A 1-line symptom patch is usually a 3–5 line structural fix. Measure diffs in **future bugs prevented**, not in lines changed today. |
| "The test is wrong" | Sometimes true — but then write the *correct* test before you touch the code. Never delete a failing test without a replacement. |

## Measured failure mode

On the fixture `tests/fixtures/root-cause-by-layer/` (a contract-boundary `TypeError` under 15-minute deadline pressure), a subagent without this skill ships:

```python
email = user.email if hasattr(user, "email") else user["email"]
```

…labeled "tolerant," tagged as an "interim fix to be cleaned up later." Two implicit contracts, one masked boundary, one deferred cleanup that never happens — the canonical symptom patch.

With this skill loaded, the same subagent names layer 4 (domain ↔ integration boundary leak) and layer 5 (implicit-contract violation / separation-of-concerns), then fixes at the boundary by making the transport-layer signature explicit (`email: str`) and moving the extraction to the caller. No shim, no `try/except`, no deferred cleanup.

Δloss ≈ 6 rubric violations removed. Full baseline and GREEN artifacts in the fixture directory.
