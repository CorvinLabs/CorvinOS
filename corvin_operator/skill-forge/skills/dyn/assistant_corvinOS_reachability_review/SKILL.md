---
name: assistant_corvinOS_reachability_review
description: Reachability as a separate review axis: after adversarial review converges on enforcement, run a round asking whether the code runs at all — driven by invented operator tasks against live code. Extracted from CONCEPT-0008 after six defects (2 HIGH) were found in a surface five enforcement rounds had passed as correct.
---

# Reachability review — does this code actually run?

Distilled from CONCEPT-0008 (`Corvin-ADR/concepts/CONCEPT-0008-reachability-review-axis.md`).
Read the concept for the full evidence, alternatives, and boundaries.

## When this fires

- An adversarial review series just converged to **zero enforcement findings** —
  that convergence is the trigger, not the finish line.
- A phase added a channel, plugin type, provisioning path, or a **second entry
  point** for an existing mechanism.
- An ADR claims something is enforced "by construction".
- A feature was wired into one of two hosts/surfaces (console + bridge,
  gateway + standalone).

## Why enforcement review cannot find this

"Can this guard be defeated?" is answerable inside the file. "Who calls this, from
what real trigger?" is not — reachability lives in the *absence* of a call
elsewhere. The blindness gets worse the better the code is: a channel with a gate,
a rollback path, an audit record and a green test suite READS as load-bearing, so
nobody checks whether anything is on the other end.

## Method

1. **Enumerate public symbols, not entry points.** For each exported name:
   `grep -rn "<name>" --include="*.py" . | grep -v "/tests/\|test_\|__pycache__"`.
   Only its own definition + an `__init__` re-export + a docstring ⇒ candidate.

2. **Split the verdict three ways — they look identical in a grep:**
   - *dead-and-wrong*: a live surface should call it and does not → HIGH, fix wiring.
   - *dead-and-vacuous*: no subject today; the invariant holds only by accident →
     wire it anyway, cheap, turns "safe by coincidence" into "safe by construction".
   - *dead-and-honest*: built ahead of an unbuilt phase, refusal is fail-closed, the
     docstring says "dormant" → leave it; verify the refusal, move on.

3. **Invent a concrete operator task per suspicion and run it against live code
   BEFORE writing any fix.** Not "is X called" but "I open the editor, drag in the
   stage, save, and ask a question in the web-chat — what happens?" One throwaway
   script of pass/fail checks. This also refutes wrong suspicions: resolve the REAL
   object through the REAL resolver — raw config JSON misleads.

4. **Re-run the identical script after the fixes.** N FAIL → 0 FAIL is the evidence.
   A green unit test is not — it was green the whole time.

5. **Check the TEST SUITE's own reachability.** An uncollectable test file is worth
   less than none, because it counts as coverage. Never `--ignore` a collection
   error; repair the import and see what it exposes.

6. **Then refute your own wiring fixes.** New call sites are new attack surface
   (unbounded injected bodies, namespace prefixes that collapse, double validation).

7. **When new code REPLACES old code, prove which module WON — not that one loaded.**
   Every check above answers "yes" when a shadow is in play: `file.tsx` beats
   `file/index.tsx`, a stale `.pyc` beats its source, `operator/` loses to the stdlib
   module. The wrong artifact is a legitimate artifact, so nothing errors. Grep the
   SERVED/INSTALLED artifact for a string that exists only in the new code
   (`console-deploy.sh --marker '<string>'`); a matching build hash proves nothing.
   Delete the replaced thing in the same commit — keeping both is what creates the
   shadow. (CONCEPT-0008 Amendment 2026-08-27; ADR-0431, ADR-0215 F1.)

## Do NOT use

- On a single bug fix or a refactor with existing E2E coverage — the per-commit
  `e2e-wiring-proof` gate covers that; this is the sweep, not the gate.
- Instead of enforcement review. A reachable gate that fails open is worse than an
  unreachable one — run this AFTER, never in place of it.
- To re-litigate an honestly-dormant module with a fail-closed refusal.
