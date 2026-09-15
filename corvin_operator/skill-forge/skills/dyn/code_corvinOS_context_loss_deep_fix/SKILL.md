---
name: code_corvinOS_context_loss_deep_fix
description: When a CorvinOS task is about lost context / forgotten state / a component that looks healthy but does nothing, apply the pointer-vs-content root fix on the first pass instead of another one-off patch: push the load-bearing CONTENT (not a pointer/flag), make its absence LOUD and verified, and anchor+re-inject it so it survives truncation.
---

# CorvinOS context-loss — the deep fix (pointer→content, silent→loud)

Fires on any task about **lost context, forgotten state, or a component that looks healthy but
delivers nothing** (session memory, delivery pollers, long-conversation drift, /status signals).
Across ≥3 incidents this was ONE recurring structural pattern — apply the root fix on the first
pass, not a fourth one-off patch.

## The pattern (greppable)
> CorvinOS carries a **POINTER / PROXY** to load-bearing info — a memory TITLE, a health FLAG, a
> conversation that will be TRUNCATED — instead of the info itself, and the pointer's
> failure-to-resolve is **SILENT**. So the agent/operator believes the context is present when it is
> not.

Three confirmed incarnations:
- **Memory recall** injected memory *titles*, not bodies → model was told THAT a memory exists,
  never WHAT it said. Tool-disabled measurement: titles = **0.00** correct vs content = **0.833**
  (EXP-001, ADR-0396).
- **Delivery poller** exposed a healthy-looking `/status` flag (`poller_stalled_s: 0`), not the real
  wedged state → 5 replies sat 90 min undelivered with no log (incident 2026-07-27).
- **Long sessions** reference established constraints/IDs/decisions that silently fall out of the
  window.

## The three-move fix (apply in order)

1. **Push the CONTENT, not the pointer.** If the system "remembers"/"references" something, inject
   the actual load-bearing content (the fact, the real state), not a title/flag. Measured lift:
   0.00 → 0.833. In CEL: `cel_brief_includes_content` (ADR-0396) renders memory bodies.

2. **Make absence LOUD and VERIFIED — never silent.** A missing/failed reference must produce a
   signal a watchdog reads (a positive counter, a log), and a test must prove the signal goes
   POSITIVE when it should. A `>= 0` assertion is worthless — it passes a mutation that silences the
   signal (the exact blindspot). Prove it with a mutation test: silence the signal → the test must
   fail. Applies the `measure-the-sum-not-the-backend` rule to state signals.

3. **Anchor + re-inject so it survives truncation.** Load-bearing facts (constraints, IDs,
   decisions) must be persisted and RE-PUSHED each turn (as content), so conversation truncation
   cannot drop them. Prefer minimal, targeted re-injection: the smallest relevant content beats a
   big cluttered brief (CpT: oracle > full brief; selective injection −30% tokens at held
   correctness, ADR-0394).

## Decision priority
Robustness over expedience: at each choice prefer the option that **fails loudly/predictably** over
one that silently wedges. Validate with e2e-wiring-proof (drive the real entry point) and a mutation
test (prove the guard catches the regression it exists for). Declare done only after proving the
failure mode cannot silently recur.

## Anti-patterns (the shallow patches this replaces)
- Injecting a memory *title* / a *pointer* and trusting the agent to pull the content (it may not; a
  tool-less or isolated turn cannot).
- A `/status` field or return value asserted only `>= 0` / `is not None` — proves nothing about
  whether it fires.
- Summarizing/pruning old turns without keeping the load-bearing facts re-injected.
- Fixing THIS incident's symptom without naming the pointer-vs-content root, guaranteeing a 4th
  incarnation.

## Where CorvinOS already implements each move
- Move 1: `operator/context_engineering/pipeline.py::render_brief_to_text(include_content=)` +
  flag `cel_brief_includes_content` (ADR-0396).
- Move 2: `operator/bridges/shared/js/outbox.js` `precheck_stalled_s` + its mutation-proven test.
- Move 3: selective injection `min_relevance` (ADR-0394); attention-budget (ADR-0319/0273); a
  session load-bearing-fact anchor is the open gap to build next.
