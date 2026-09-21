# Phase 3b Optimization Plan — ComplexityJudge Tuning

**Status:** PLANNING ONLY — no execution. Written 2026-09-20 as part of the
Phase 3 review; gated on the blockers listed in the review report before
Phase 3 itself is actually live (see status report delivered alongside this
file). Do not start Phase 3b work until Phase 3 has real production data.

## Baseline (from `PRODUCTION_DEPLOYMENT_FINAL_REPORT.md`, verified against
committed code in `core/skills/os_skills/intelligent_router.py`)

- Judge override threshold: 0.75 (lowered from 0.9 in the fix that produced
  commit 5630ce42)
- Signal weighting: token-tier signals dominant, `judge_weight` default 0.20
  (ComplexityJudge is signal 3 of 3)
- Token savings: 32.2% (vs. 37.8% in Phase 2 — intentional trade for quality)
- Quality: 93.5% (vs. 84.8% Phase 2 baseline)
- Edge case ("kurz aber komplex") detection: 78% (vs. 20% Phase 2)

## Planned Work (begin only after ≥1 week of real production data)

1. **Threshold sweep.** Test judge confidence threshold at 0.70 and 0.80
   against production routing logs (not synthetic benchmarks) to find the
   point that maximizes edge-case recall without materially increasing
   Sonnet/Opus overrides on genuinely simple requests. Current 0.75 was
   chosen from the adversarial-review fix, not from a swept optimum.
2. **Re-weight the 3 signals.** Current split is dominated by the two
   token-based signals with ComplexityJudge at ~20%. Evaluate 50/25/25 (or
   similar) once there's enough labeled production traffic to measure
   whether the judge signal is being under- or over-weighted relative to
   its actual precision.
3. **Expand the edge-case dataset.** Mine real production "kurz aber
   komplex" misses (cases routed to Haiku/Sonnet that should have gone
   higher, discovered via user complaints, quality regressions, or manual
   audit) and add them to the test fixture set that
   `tests/test_complexity_judge.py` exercises.
4. **Recalibrate ComplexityJudge heuristics** using the above production
   feedback rather than the original synthetic benchmark set.

## Timeline

Begin after 1 week of real production routing data — meaning after Phase 3
is actually deployed, wired to a real config schema, and generating traffic.
Not before.

## Success Criteria

- Edge case detection: 78% → target 90%+
- No regression in the 93.5% quality figure
- No regression in token savings below ~30%

## Explicit Precondition (do not skip)

This plan assumes Phase 3 is live and generating real routing decisions.
As of this writing it is **not**: `core/skills/os_skills/complexity_judge.py`
(the module Phase 3 routing imports) is untracked in git, there is no
`routing_strategy` / `judge_enabled` key in any tenant config file in this
repo, and the test runner (`pytest`) is not installed in this environment.
See the Phase 3 rollout status report for the full list of blockers found
during review. Phase 3b tuning against production data is meaningless until
those are resolved and Phase 3 has actually shipped.
