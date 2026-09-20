---
id: CONCEPT-0005
title: A probe that cannot reproduce the defect is not proof
status: ACTIVE
created: 2026-09-20
skills: [assistant_corvinOS_discriminating_probe]
relates_to: [CONCEPT-0004]
paths:
  - corvin_operator/voice/scripts/say.py
  - core/console/corvin_console/routes/voice.py
  - tests/test_voice_subprocess_encoding.py
  - tests/test_voice_tts_budget.py
docs:
  - docs/claude-ref/layer-voice-ldd.md
---

# CONCEPT-0005 — A probe that cannot reproduce the defect is not proof

**Note on location:** this belongs in `Corvin-ADR/concepts/`. That repo does not
exist on the Windows box this was written on, so it lands in the documented
`CorvinOS/docs/concepts/` fallback. Move it when the two are on one machine.

## The situation this is for

A bug is reported. You fix something, you probe the endpoint, the probe is green,
you report "fixed". The user comes back with *"geht immer noch nicht"* — and the
second time you look, the endpoint really is broken, in a way the probe could
never have shown.

This is not carelessness about *running* a verification. It is a verification
that is **structurally incapable of failing** for the defect in question. It is
the single most expensive failure shape in this repo, because a green probe
actively ends the investigation.

## The measured instance (2026-09-20, console TTS)

"Kein edge-TTS im Chat, Voice-Summary geht nicht." The probe used all session:

```bash
curl -X POST .../v1/console/voice/tts -d '{"text":"Die Sprachausgabe funktioniert.","lang":"de"}'
```

Plain German, 31 characters. It returned 200 with real MP3. It was green while
the feature was broken by **two independent defects**, and it could not have
caught either:

| Defect | Why the probe was blind to it |
|---|---|
| `text=True` without `encoding=` → cp1252 encode of stdin raises in subprocess's writer thread → child never sees EOF → parent burns its whole timeout (154 s live) | The payload is **encodable in cp1252**. Nothing to raise. Real chat replies open with U+1F44B. |
| Flat 10 s per-provider cap vs synthesis cost proportional to text length (measured ~6.5 s/1000 chars, varying ~4× run to run) | The payload is **31 chars**. Never near the cap. Real summaries are ~1000 chars. |

Both dimensions the probe held constant — character repertoire and length — were
the two the defects lived on. And because `/voice/tts` answers **204 on any
failure** by design (the "voice is off" reply), a failing turn was also
indistinguishable from a working one at the HTTP level.

The fix for defect 1 was shipped, reported, and the user reported the bug again.
That round trip is the cost of a non-discriminating probe.

## The method

Before treating a probe as evidence, ask **what dimension does the defect live
on, and does my payload vary along it?** Concretely:

1. **Name the defect's axis.** Encoding → character repertoire. Timeout → size
   and repetition. Concurrency → parallelism. Auth → identity. Caching → a
   marker string only the new code contains.
2. **Move the payload to the hostile end of that axis**, not the convenient end.
   For this path that means the emoji the live system actually sends (U+1F44B),
   at the length the live system actually produces (~1000 chars) — not
   ASCII-and-short because it is easy to type in a shell.
3. **Repeat when the failure is stochastic.** Defect 2 varied ~4× for
   byte-identical input; it failed roughly every other turn. One green run was
   never evidence. Three consecutive runs were used for the final proof.
4. **Prove the probe can go red.** Write the positive control: assert the OLD
   value would have failed (`provider_timeout_for("x"*1000) > 10.0`), run the
   scanner against known-bad source, force a non-UTF-8 locale so a UTF-8 CI box
   cannot make the assertion vacuous. A check that cannot distinguish "clean"
   from "never looked" is not a check.
5. **Bake the discriminating payload into a guard**, so the next agent inherits
   the hostile input instead of re-deriving it. `_HOSTILE` in
   `tests/test_voice_subprocess_encoding.py` documents *per character* why it is
   in the string; `_MEASURED_WORST` in `tests/test_voice_tts_budget.py` asserts
   against real measured seconds, not against the formula's own output.
6. **When the interface degrades silently, get the reason out of band first.**
   `X-Corvin-Voice-Reason` plus the WARNING in `~/.corvin/logs/console.log` is
   what separated "certificate failure" from "TimeoutError" and turned a guess
   into a measurement. Find that channel before forming a hypothesis.

## Why this keeps paying off

The shape recurs across unrelated subsystems in this repo:

- **Console frontend.** "Build succeeded" proves new code was added, never that
  old code was removed. A bundle scan for a removed string passes both when the
  string is gone and when the crawl never reached the chunk it lived in — which
  read as green for a check with zero reach (`tests/e2e/test_engine_config_real_data_e2e.py`,
  2026-09-15). The fix is the same: assert a marker only the NEW code contains,
  *before* the absence check.
- **The German/ADR scanner** resolves `src/` relative to `web-next/`; run from
  the repo root it sees zero files and reports success. Same failure: a zero that
  means "never looked".
- **Reachability.** A unit test proves a function returns the right value *when
  called*; it says nothing about whether anything calls it. `e2e-wiring-proof`
  exists because "tested" and "reachable" were repeatedly conflated.
- **Auth fixtures.** On a Bedrock box `CLAUDE_CODE_USE_BEDROCK=1` is already in
  every inherited environment, so a naive "no credentials" fixture is a false
  green (`test_summarize.py`).

Each of those was found separately. They are one concept.

## Alternatives considered

- **"Just write more tests."** Volume does not help: the ASCII curl could have
  been run a thousand times. The axis, not the count, is what was wrong.
- **"Always use production-realistic payloads."** Too vague to act on, and often
  impossible (you cannot replay a real user's session). Naming the defect's axis
  is the operable version, and it is cheap — one sentence before probing.
- **"Trust the unit tests instead of live probes."** Both defects had passing
  neighbours. Unit tests share the same blindness when the fixture is convenient;
  in defect 1's case the two halves of the bug even *cancelled* under a pure echo
  (cp1252 decodes an unmapped byte to a surrogate and re-encodes it back), so a
  round-trip test was green for years.
- **"Remove the silent degradation so failures are loud."** Tempting, and wrong
  here: the 204 is a deliberate UX contract (voice genuinely can be off) and
  changing it is a separate behavioural decision. Surfacing the *reason* out of
  band gets the diagnostic benefit without touching the contract.

## When NOT to use this

- **Pure refactors with unchanged behaviour.** There is no defect axis to vary;
  the existing suite passing is the point.
- **A defect you can reproduce deterministically in one line.** If the bug fires
  on every input, the convenient payload *is* discriminating. Don't manufacture
  a hostile fixture to prove something a trivial call already proves.
- **Exploratory probing.** Early on, a cheap ASCII call is a perfectly good way
  to learn that the endpoint exists and answers. The rule binds when you are
  about to call something **fixed** — that is the moment the probe becomes
  evidence and has to be able to fail.
- **When the hostile payload would cost real money or side effects.** Then say
  explicitly which axis is unverified rather than substituting a green probe for
  it; an named gap beats a false proof.

## Operator Notes

_(append-only; AI amendments never edit or remove anything under this heading)_
