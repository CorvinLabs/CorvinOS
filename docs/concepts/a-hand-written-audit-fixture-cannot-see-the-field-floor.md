# A hand-written audit fixture cannot see the field floor

> **Where this belongs.** Concepts are canonically numbered in the sibling
> `Corvin-ADR/concepts/` repo. That checkout does not exist on this machine, so
> this file lands in the documented fallback (`CorvinOS/docs/concepts/`).
> Renumber it to `CONCEPT-NNNN` when it is moved.
>
> Sibling of [proving-a-routing-decision-reached-the-engine](proving-a-routing-decision-reached-the-engine.md):
> that one is about a decision reaching the engine, this one is about a
> *measurement* reaching the chain.

## The problem this solves

CorvinOS's audit writer applies a **positive, per-event field allowlist**
(ADR-0129 M2, `forge/security_events.py::_EVENT_ALLOWLIST`). Anything not
listed for that event type is moved into `_dropped_fields` — silently, by
design, and correctly: the floor exists so no emitter can leak content or PII
into the chain.

The consequence for anyone *adding* a field is brutal and invisible:

- the emitter is correct,
- the reader is correct,
- the aggregation, the API and the UI are all correct,
- and the number is permanently zero,

because the field never reached disk. There is no error, no warning, and no log
line naming the emitter. `_dropped_fields` is written into the record itself,
where nobody looks unless they already suspect the problem.

**The trap is that the obvious test does not see it.** A fixture that writes the
JSONL by hand — `fh.write(json.dumps({...}))` — bypasses `write_event` and
therefore bypasses the floor. It is the natural way to build a reader test, it
runs fast, it is deterministic, and it is green in exactly the scenario where
production is broken. That is not a hypothetical: five green tests over
hand-written chains covered the Model Cost Optimizer's delegated-worker cost
series while the four token fields both worker emitters pass were being dropped
at write time, for every tenant, permanently (2026-09-15).

## The method

**At least one test per new audit field must write through the real
`write_event` and assert `"_dropped_fields" not in details`.**

Three properties make that one assertion the right one:

1. It is downstream of the whole floor — the per-event allowlist, the global
   forbidden-key list, the oversize-value cap — so it does not need to know
   which of those would have caught the field.
2. It names the failure precisely. Put the dropped list in the assertion
   message and the test output *is* the diagnosis: `field floor dropped
   ['input_tokens', ...]`.
3. It costs nothing. `write_event` takes a path; a `tmp_path` chain is enough.

Two supporting moves:

- **Pair it with a negative control in the same class.** Widening a positive
  allowlist is a compliance-relevant edit, so prove in the same breath that
  content is still refused: write `{"prompt": "...", <new field>: 1}` and assert
  the prompt is in `_dropped_fields` and the new field is not. This is what
  separates "I added a needed metadata key" from "I punched a hole in the
  floor," and it is the evidence a reviewer actually needs.
- **Then run the full path once** — real writer → real reader → real HTTP
  route — so the field is proven to survive *and* to be consumed. The floor
  test localises the failure; the full-path test proves the feature.

Keep the fast hand-written fixtures for the reader's own logic (bucketing,
exclusion rules, multi-day series). They are the right tool for that. They are
simply not evidence about the chain.

## Worked evidence

`acs.engine_completed`, 2026-09-15. `acs_runtime.py` had passed
`input_tokens` / `output_tokens` / `cache_creation_input_tokens` /
`cache_read_input_tokens` since ADR-0696, with the in-code comment "needed for
per-model $ pricing". The per-event allowlist listed only `tokens_used`. Its
sibling `os_turn.completed` has **no** per-event entry, falls through to the
global safe-key list — which already permits all four — and worked; the two
events looked identically wired and behaved oppositely.

Result: `_read_acs_completions` reads only the split keys, so every delegated
worker run priced as $0.00 and `acs_data_available` was structurally `False` —
a state operationally indistinguishable from "no worker has run yet", which is
how it read on the panel for as long as the feature existed. Found by writing
one record through `write_event` at a shell prompt; the reader-level tests were
green the whole time. Negative control after the fix: reverting the allowlist
turns all three new tests red.

## Alternatives considered

- **Grep the allowlist while writing the emitter.** Cheap, worth doing, and not
  sufficient: it catches only the per-event set, and it is a habit rather than a
  gate — it does not fail in CI when the next field is added.
- **Make the floor log or raise on a drop.** Tempting and wrong as stated: the
  writer is deliberately non-raising and content-free, and a log line naming
  dropped keys is a new place for a key name to leak. A test-side assertion gets
  the signal without touching a load-bearing compliance path.
- **Assert the field is present in the live production chain.** Only works
  after real traffic has produced the event, which is precisely what you do not
  have when the feature is new — and on this install the ACS chain file did not
  exist at all.
- **Trust the reader test.** The failure mode this concept is about.

## When NOT to use this

- The field is already carried by a shipping event of the same type in
  production; a chain record proving it is better evidence than a test.
- The event has no per-event allowlist entry AND the key is already on the
  global safe list — the floor test then passes trivially. Adding it is still
  cheap insurance against a later per-event entry, but it is not a finding.
- Non-audit persistence (learning event store, WDAT report, TDE metrics). Those
  have their own schemas and no positive field floor; this concept is
  specifically about the L16 chain.

## Operator Notes

<!-- Append-only. AI amendments never edit or remove anything below this line. -->
