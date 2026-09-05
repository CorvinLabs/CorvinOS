# Findings — Run 1 (pilot)

**Date:** 2026-09-04
**Model:** claude-haiku-4-5-20251001 · tool-disabled (`--disallowedTools '*'`, `--max-turns 1`)
**Design:** 14 tasks (8 answerable + 6 trap) × 3 arms (none / pruned / full) × 2 reps = 84 real calls, 584 s.
**Report:** `results/report-2026-09-04_220127.json` · **Raw:** `results/raw-2026-09-04_220127.jsonl`

## Goal
Measure, in one run, whether dropping context to save tokens buys the saving with
hallucination. Arms: `full` (fact present) → `pruned` (fact dropped, topic kept) →
`none` (bare question). All facts are FICTIONAL, so no world-knowledge can leak in.

## Results

| arm | correct (ans.) | halluc (ans.) | abstain (ans.) | halluc (trap) | abstain (trap) | cost/turn | input tok |
|-----|---------------:|--------------:|---------------:|--------------:|---------------:|----------:|----------:|
| none   | 0.125 | 0.063 | 0.938 | 0.083 (1/12) | 0.917 | $0.0265 | 29 260 |
| pruned | 0.125 | 0.000 | 0.938 | 0.000 (0/12) | 1.000 | $0.0263 | 29 290 |
| full   | 1.000 | 0.000 | 0.000 | 0.167 (2/12) | 0.833 | $0.0255 | 29 298 |

Guardrail (overall halluc lift vs none, margin 0.05): all **PASS**.

## Observations (what the data shows)

1. **Full context → perfect correctness.** answerable correctness 0.125 → **1.000**.
   Large, unambiguous effect (the intended H2). The fact is only answerable when
   actually injected.

2. **The model is well-calibrated under missing knowledge.** With the fact dropped
   (`pruned`/`none`) it **abstains ~94 %** of the time instead of bluffing; answerable
   hallucination is ≤ 0.06. Honest "I don't know" is the dominant failure mode, not
   fabrication.

3. **SURPRISE (the interesting signal): authoritative context INCREASES trap
   hallucination.** trap-hallucination none 0.083 → pruned **0.000** → full **0.167**.
   It is NOT the token-saving (pruned) that induces bluffing — pruned was the *safest*
   arm. It is the full, authoritative-sounding context ("Established facts …
   authoritative") that makes the model feel entitled to invent the one missing
   detail. This inverts the naive hypothesis.

4. **Cost/savings axis is DEGENERATE in this pilot — honest null result.** input
   tokens are ~29 290 in every arm; the full-vs-pruned context difference is ~8 tokens
   against a ~29 250-token fixed overhead (the CorvinOS/Claude-Code system prompt:
   cache_creation ~11 100 + cache_read ~18 100). Cost CIs fully overlap
   ($0.0255–0.0265). **No saving was measurable** because (a) the fixed overhead
   dominates and (b) the fictional context blocks are tiny (1–2 sentences). This is a
   real limitation of the toy suite, not evidence of "no savings exist."

5. **Cache confounder confirmed.** rep0 pays cache-creation (~$0.047), rep1 reads it
   back (~$0.005); the run mean sits between. Cost depends heavily on cache state.

## Conclusions

- **H2 (content lifts correctness): CONFIRMED, strong effect** (0.125 → 1.0).
- **Core H0/H1 (savings bought with hallucination): NOT ANSWERABLE here** on the cost
  axis — savings were unmeasurable. But the hallucination axis produced a **PLAUSIBLE,
  not-significant** counter-signal worth pursuing: authority-framing, not pruning,
  drove trap hallucination.
- Nothing except the correctness effect is statistically significant: n = 12 per
  trap cell (2 vs 0 vs 1 events); overall-halluc CI is [0.0, 0.179]. **Pilot signal, not proof.**

## Limitations → v2 fixes

1. **Context too small** → use realistic CEL-sized briefs (memory bodies, ADRs;
   hundreds–thousands of tokens) so the savings axis is non-degenerate.
2. **Fixed overhead swamps the signal** → report *marginal* context tokens
   (arm minus `none`) and/or subtract the system-prompt baseline.
3. **Guardrail on overall halluc HID the trap effect** → gate on `halluc_rate_trap`
   specifically.
4. **n too small** → ≥ 5–10 reps and more trap tasks per domain; pass the
   calibration gate (n ≥ 5, MAE ≤ 0.15) before any metric gates.
5. **Abstention detector is lenient** (hedge-plus-guess counts as safe) → add a
   concrete-claim detector and the `output_judge` LLM cross-check.
6. Single model, single run → repeat on the Opus tier for external validity.

---

# Findings — Run 2 (v2, validated instrument)

**Date:** 2026-09-05
**Model:** claude-haiku-4-5-20251001 · tool-disabled · 20 tasks (10 ans + 10 trap) × 3 arms × 4 reps = 240 calls, 1630 s.
**Report:** `results/report-v2-2026-09-05_000951.json`
**Instrument:** validated in 2 calibration rounds (see PRE-REGISTRATION-v2.md); 0 padding-suspicion
flags across all 80 full-arm answers; aggregation reproducible byte-for-byte.

## Results
| arm | marg. context tok | cold $ | warm $ | correct (ans.) | halluc (ans.) | abstain (ans.) | halluc (trap) |
|-----|---:|---:|---:|---:|---:|---:|---:|
| none   | 0    | 0.0475 | 0.0050 | 0.025 | 0.025 | 0.975 | 0.000 |
| pruned | 282  | 0.0482 | 0.0052 | 0.000 | 0.025 | 0.975 | 0.025 |
| full   | 1536 | 0.0518 | 0.0044 | 1.000 | 0.000 | 0.000 | 0.050 |

## Scorecard vs pre-registration
| # | Prediction | Observed | Verdict |
|---|-----------|----------|---------|
| P1 | marg. full − pruned ≥ 1000 | 1254 | **CONFIRMED** |
| P2 | correct full 0.85–1.0 | 1.00 | **CONFIRMED** |
| P3 | correct pruned 0.05–0.25 | 0.00 | point slightly high, not falsified (< 0.4); direction right |
| P4 | halluc pruned 0.0–0.15 | 0.025 | **CONFIRMED** |
| P5 | abstain pruned ≥ 0.80 | 0.975 | **CONFIRMED** |
| P6 | trap-halluc full 0.10–0.30 | 0.05 | below range (effect weaker) |
| P7 | trap-halluc full − pruned ≥ 0.08 | +0.025, perm p=1.0 | **FALSIFIED** |

## Conclusions
1. **Instrument now measures savings** (P1): pruning drops ~1250 marginal context tokens
   (~82 % of the injected context). The savings axis is no longer degenerate.
2. **The trade-off is cheap ⇄ HONESTLY-IGNORANT, confirmed.** Pruning saved the tokens but
   collapsed correctness 1.00 → 0.00, while the model stayed honest: abstention 0.975,
   hallucination 0.025. The saving is bought with "I don't know," not with bluffing.
3. **The Run-1 authority effect was NOISE (my riskiest prediction, FALSIFIED).** With a
   validated instrument and n=40/arm, trap-hallucination is full 0.05 vs pruned 0.025 vs
   none 0.00 — a +0.025 difference at perm p=1.0. Run 1's 0.167 was 2 events on n=12. No
   significant authority effect survives. Honest self-correction: I was wrong.
4. **Haiku is well-calibrated across all context conditions.** Max hallucination anywhere is
   0.05 (trap+full). Hallucination is NOT the failure mode of token-saving here; correctness
   loss is. The model largely knows what it does not know.
5. Guardrail: all PASS (full trap lift 0.05 = margin 0.05, the edge). Even the sharpened
   trap-axis guardrail does not reject full — the effect is too small.

## Honest limitations
- Single model (Haiku). A weaker/older model may be far less calibrated — the authority
  effect could be real there. External validity requires an Opus-tier and a small-model run.
- Trap events are still rare (0–2 per cell); P7 is powered to detect a large effect, not a
  small one. "No significant effect" ≠ "exactly zero effect."
- Fictional facts remove world-knowledge leak but are easy to flag as absent; real
  ambiguous facts might behave differently.
