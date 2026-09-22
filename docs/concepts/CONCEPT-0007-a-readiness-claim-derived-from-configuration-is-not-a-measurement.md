---
id: CONCEPT-0007
title: A readiness claim derived from configuration is not a measurement
status: ACTIVE
created: 2026-09-22
skills: []  # none — skill minting is license-gated off on this box, see below
relates_to: [CONCEPT-0005, CONCEPT-0006]
paths:
  - core/console/corvin_console/routes/voice.py
  - corvin_operator/voice/scripts/say.py
  - core/console/tests/test_voice_tts_openai_verdict.py
docs:
  - docs/claude-ref/layer-voice-ldd.md
---

# CONCEPT-0007 — A readiness claim derived from configuration is not a measurement

**Note on location:** this belongs in `Corvin-ADR/concepts/`. That repo does not
exist on the Windows box this was written on, so it lands in the documented
`CorvinOS/docs/concepts/` fallback. Move it when the two are on one machine.

**Note on the companion skill:** the same limitation as [[CONCEPT-0006]] —
`SkillRegistry.create` is denied at `tier=free` on this install (ADR-0156
`forge.create`), which is why no skills exist here at all. The intended skill is
`assistant.corvinOS_readiness_is_measured_not_configured` (`learned-experience`,
project scope, body = the Method section); mint it on a licensed install with the
one bootstrap grade at ≤0.3 and fill in `skills:`.

## The situation this is for

A panel, a health endpoint or a gate publishes a boolean called `ready`,
`available`, `ok`, `healthy`, `enabled`. Nobody measured the thing. The boolean
is computed from **proxies for** the thing: the key is resolvable, the package
imports, the config file parses, the binary is on PATH, the model file exists.
Every proxy is true. The thing does not work.

The cost is not the wrong pixel. It is that the wrong pixel **redirects the
investigation**. An operator who reads `openai: ready` and hears edge speaking
concludes the *selection logic* is broken, and so does the agent they ask. Hours
go into routing, priorities, pins and feature flags — into the one layer that was
working perfectly — because the cheapest observable in the system asserted
something it had no way to know.

The rule: **a claim about whether something works may only be published by
whoever actually tried it.** Everything else publishes what it really knows —
"configured", "installed", "present" — or publishes nothing.

## The measured instance (2026-09-22, "wieso wird OpenAI TTS nicht verwendet")

The console spoke with edge on every turn although a valid OpenAI key was
configured and OpenAI is tier 1 of the auto-chain. The real cause was two network
hops away from anything Corvin owns: `api.openai.com` is refused with **HTTP 403**
by the corporate egress proxy (`Server: Zscaler/6.2`, category "Generative AI and
ML Applications"), so the request never reaches OpenAI and the key had never been
validated by the install at all. The fallback chain then did exactly its job.

Everything Corvin *showed* pointed elsewhere:

- `GET /v1/console/voice/status` reported
  `openai: {"ready": true, "detail": "ready"}`. Its source,
  `say.py::provider_status()`, is documented as *"Cheap introspection only —
  NEVER synthesizes audio"*. It is correct about what it inspects and it is
  structurally blind to an egress block. Nothing in the payload said so.
- The one log line that named the reason was emitted **once per process**:
  `_openai_tts_warned_once` demotes it to DEBUG afterwards — right, or it would
  log on every turn — leaving a bare `httpx ... 403`. By the time anyone looked,
  the explanation had been printed hours earlier and discarded.
- `piper: ready=false` was the *only* honest row on the panel, and it was honest
  for the trivial reason: a missing model file is visible to introspection.

Three defects, one cause: **the outcome of the attempt was logged and thrown
away**, so nothing downstream could read it. Once recorded (one frozen verdict
struct, written on both success and failure), the same observation fixed all
three — the status row stopped claiming `ready`, the reason stayed retrievable
after the log went quiet, and `say.py` stopped repeating a round-trip the parent
had just measured, which alone was costing **2.6 s of every TTS call** (5.71 s →
3.14 s; the doc's estimate of "~0.1 s, the proxy rejects immediately" was wrong
by 25×, itself an unmeasured claim).

The tell, in hindsight, was there from the first minute: the header said
`x-corvin-tts-provider: say.py:edge` — a **measured** observable, emitted by the
tier that actually spoke — while a **derived** observable said openai was ready.
Two observables disagreed, and one of them had touched the network.

## The method

1. **When an observable contradicts behaviour, believe the behaviour.** Behaviour
   is the measurement. Find every observable that disagrees and audit its
   derivation before you touch the logic it seems to indict.
2. **Ask what each boolean is computed FROM, not what it is named.** Read the
   producer. `ready` produced by `key is not None and importlib.util.find_spec()`
   is a configuration claim with a readiness name. This one question is the whole
   concept; everything else follows from the answer.
3. **Sort the proxies by what they cannot see.** Config-derived checks are blind
   to exactly the interesting failures: egress policy, credential validity, quota,
   TLS interception, DNS, a peer that 200s with garbage. If the reported failure
   mode lives in that blind spot, you have found the lie.
4. **Repair by recording the outcome, not by probing from the reader.** Making
   the status route synthesize would put a blocking network call on a page poll,
   re-measure what the synthesis path already knows, and break the
   cheap-introspection contract. The synthesis path already has the answer — the
   bug is that it discards it.
5. **Store the observation where every re-deriving consumer can read it.** The
   payoff scales with the number of consumers that were each guessing separately.
   Here: a panel that guessed, a log that forgot, and a subprocess that repeated
   the work. One struct, three fixes.
6. **Expire it, and let a success clear it.** An observation that never expires
   is a permanent pin wearing an observation's name, and it converts "the panel
   lies about working" into "the panel lies about being broken" — with the
   preferred tier held off after the network was fixed. Bound the TTL in a test.
7. **Only a definitive outcome may override a claim.** `401/403/404` settles the
   question; `429` and a dropped connection do not. Suppressing a working paid
   tier on one blip is a worse bug than the one being fixed.
8. **No evidence means no claim — in both directions.** With nothing observed,
   pass the row through untouched. The repair is to stop asserting success
   without proof, never to start asserting failure without proof.
9. **Keep the honest sub-facts visible.** `key_configured` stayed `true` in the
   failure row, because the key IS configured — and that contradiction is
   precisely the thing the operator needs to see to reach the right conclusion.
10. **Publish only content-free evidence.** Host, HTTP status, exception class,
    deployment name, clock time. Never the key, never the spoken text, never
    `str(e)` — an SDK exception can embed the request payload, which here is the
    sentence being read aloud.

## Why it keeps paying off

The same shape, repeatedly, in this repo — and CLAUDE.md already encodes several
instances of it without naming the class:

- **`quality.tsx`** built a seven-day trend from `Math.random()` and `runAllGates`
  returned `ok: true, "Gate run initiated"` for a run that never started
  (ADR-0763). A claim with no measurement behind it, rendered as a measurement.
- **`engine.span.*` without `model_id`** (ADR-0759): the console showed
  "0 priced worker runs" on an install that delegates — 267 runs over 11 days,
  every one discarded by a reader. The panel's number was derived from a source
  that could not see the runs.
- **The German/ADR scanner** that resolved `src/` relative to the wrong directory,
  saw zero files, and reported success — which is why CLAUDE.md now demands a
  positive control for any zero-findings sweep. A sweep that reached nothing and
  a sweep that found nothing produce the same green.
- **[[CONCEPT-0005]]**, the immediate sibling: there, the *probe* could not
  reproduce the defect. Here, the *status field* could not observe it. Same
  failure of reach, one on the verification side, one on the reporting side.

And the boundary with [[CONCEPT-0006]] is exact, which is what makes them a pair
rather than a duplicate. CONCEPT-0006 is for a field that is **stored** and drifts
because a writer never wrote; its own "When NOT to use this" says *"when the field
really is derived on read, drift is impossible and a stuck value means the
derivation is wrong."* That excluded case is this concept. Both start from "a
displayed value disagrees with reality"; step 2 of each is what tells them apart,
so read them together and answer **stored or derived** first.

## Alternatives considered

- **Probe the endpoint from `/voice/status`.** The obvious fix, and the one that
  looks most honest. Rejected: it puts a network round-trip (here ~2.6 s, and a
  hang if the proxy black-holes instead of refusing) on a page poll that the
  frontend repeats, it re-measures what the synthesis path already knows, it
  breaks `provider_status()`'s documented no-synthesis contract, and on a metered
  provider it bills for rendering a settings page.
- **Drop the row to `ready: false` whenever a key cannot be *validated*.** Turns
  every unconfigured-but-working local setup into a red panel and requires a
  validation call per provider. It also still cannot see an egress block for a
  provider whose validation endpoint happens to be allowed.
- **Rename the field to `configured` and show nothing about readiness.** Honest
  and cheap, and it was seriously considered. Rejected because the operator's
  question is literally "can this speak?" — answering a different question
  accurately still leaves them to discover the block by experiment, which is the
  cost this whole pass exists to remove. (Do take this option when no code path
  in the system ever measures the thing.)
- **Pin the tier off once a 403 is seen.** Saves the same round-trip and is
  simpler. Rejected: it makes recovery require a restart or a setting, on a box
  where the fix (a granted network exception) arrives silently and from outside.
  The TTL exists precisely to refuse this.
- **Only fix the log line** (log at WARNING every time, or once per hour).
  Addresses the symptom that wasted the time, leaves the panel lying and the
  round-trip doubled. Worth noting it was the cheapest option and would have
  looked like a fix.

## When NOT to use this

- **When nothing in the system ever measures the property.** Then there is no
  outcome to record, and the honest repair is step "rename the field" above —
  publish `configured`/`installed` and stop implying more.
- **When the proxy IS the property.** `model_present` really is a file-existence
  question; `package_installed` really is an import question. A row that only
  claims what it inspected is correct and needs nothing.
- **When the measurement is destructive, billable or slow enough to matter, and
  no ordinary code path produces it anyway.** Recording an outcome is free
  because something else was already paying for it. Manufacturing the outcome to
  populate a panel is a different, worse trade.
- **When the value is already derived on read from ground truth** — a count
  folded from the audit chain, for instance. Then it cannot be stale in this way,
  and a wrong number means the derivation is wrong ([[CONCEPT-0006]]'s inverse
  boundary).
- **When the failure is not definitive.** Do not let this concept talk you into
  marking a tier dead from a timeout or a 429. See step 7; the whole mechanism is
  worthless if it can be tripped by a blip.

## Operator Notes

_(append-only; AI amendments never edit or remove anything under this heading)_
