"""ADR-0222 Phase 2 — the TDE decision gate.

The whole premise of the "token-saving amplifier" is a HYPOTHESIS: that splitting
a task into a Haiku swarm net-saves tokens at the user's model quality. Phase 1
(F1-F4) fixed the measurement so the loss log finally reflects the real
counterfactual. Phase 2 asks the load-bearing question:

    On ANY task band, does per-step TDE net-save tokens at held user-model
    quality — AND beat the simpler whole-task-tier baseline (F5)?

If NO on every band, the premise is falsified and the V-track (token-savings play)
stops; TDE stays only as a latency/parallelism feature for genuine fan-out.

── Honesty invariant (the load-bearing rule of this module) ─────────────────────
This gate must NEVER rubber-stamp. Two structural guards:

  1. It refuses to emit a *decisive* verdict (TDE_WINS / TIER_WINS / NO_SAVINGS)
     unless the evidence is MEASURED and clears the minimum sample size. On thin
     or assumption-sourced data it returns INSUFFICIENT_DATA (or, for explicitly
     labelled assumption input, a verdict tagged data_source="assumptions" — a
     PREDICTION, never a measurement).
  2. Every `TdeVerdict` carries `data_source` ∈ {"measured", "assumptions"}. A
     caller that ships a routing change must assert data_source == "measured".

── What "build Phase 2 with assumptions" means here ─────────────────────────────
We do NOT have the measurement week's real traffic yet. So the *thresholds* below
are provisional ASSUMPTIONS (documented, re-calibratable), and
`synthetic_evidence_from_assumptions()` encodes our current best hypothesis about
the numbers as a runnable prediction. The gate LOGIC is real and unit-tested; the
INPUT is labelled assumptions until real data replaces it. Nothing here fabricates
a measured result.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional

# ── Provisional assumptions (Phase 2, 2026-07-25) ────────────────────────────
# EVERY constant here is a GUESS to be re-calibrated from the measurement week.
# They are gathered in one dataclass so a future calibration is a single diff and
# the ADR can point at exactly what was assumed.


@dataclass(frozen=True)
class GateAssumptions:
    """Provisional decision thresholds. Re-calibrate from real data (ADR-0222)."""

    # Max acceptable quality loss vs the user-model DIRECT turn, as a fraction
    # (0.05 = 5% semantic loss ≈ 95% equivalence). Mirrors ADR-0219's
    # QUALITY_THRESHOLD. ASSUMPTION: 5% is "held quality". Likely too loose for
    # code, too tight for chat — split per band once measured.
    quality_floor_loss: float = 0.05

    # Minimum NET token savings (fraction) TDE must clear to justify its
    # orchestration + latency + operational complexity. ASSUMPTION: below ~15%
    # net, the added moving parts are not worth it. Pure guess.
    min_net_savings: float = 0.15

    # TDE must beat the whole-task-tier baseline (F5) by at least this margin,
    # else prefer the simpler tier routing. ASSUMPTION: 5 percentage points of
    # extra savings to justify decomposition over one-call tiering.
    min_margin_over_tier: float = 0.05

    # Minimum MEASURED sample count per band before a decisive verdict. Thin
    # evidence must return INSUFFICIENT_DATA, never a verdict. ASSUMPTION: 30.
    min_samples_per_band: int = 30


DEFAULT_ASSUMPTIONS = GateAssumptions()

Verdict = Literal[
    "TDE_WINS",          # net-saves ≥ margin at held quality AND beats tier baseline
    "TIER_WINS",         # whole-task tier achieves the savings; TDE doesn't beat it
    "NO_SAVINGS",        # nothing beats the direct turn on this band → premise false here
    "INSUFFICIENT_DATA",  # not enough MEASURED evidence to decide
]


@dataclass
class BandEvidence:
    """Aggregated per-band numbers the gate compares.

    All token figures are AVERAGE total tokens per task on this band; all loss
    figures are AVERAGE fractional quality loss vs the user-model direct turn
    (0.0 = identical, 1.0 = unrelated). The direct turn is the reference, so its
    loss is 0 by definition and only its token cost is carried.
    """

    band: str                         # e.g. "trivial" | "moderate" | "complex"
    direct_tokens: float              # avg tokens of one direct user-model turn
    tde_tokens: float                 # avg tokens of the TDE decomposition
    tde_loss: float                   # avg TDE quality loss vs direct
    tier_tokens: float                # avg tokens of the whole-task-tier baseline (F5)
    tier_loss: float                  # avg tier-baseline quality loss vs direct
    n_measured: int                   # MEASURED samples backing these averages
    data_source: Literal["measured", "assumptions"] = "measured"


@dataclass
class TdeVerdict:
    band: str
    verdict: Verdict
    data_source: Literal["measured", "assumptions"]
    tde_net_savings: Optional[float] = None    # (direct - tde) / direct
    tier_net_savings: Optional[float] = None    # (direct - tier) / direct
    reason: str = ""
    detail: dict[str, Any] = field(default_factory=dict)


def evaluate_band(ev: BandEvidence, a: GateAssumptions = DEFAULT_ASSUMPTIONS) -> TdeVerdict:
    """Decide the verdict for ONE band. Pure function.

    Order of checks (each is a structural guard, not a heuristic):
      1. sample-size gate → INSUFFICIENT_DATA
      2. quality gate     → if TDE breaks the quality floor it cannot win on tokens
      3. tier comparison  → TDE must beat both direct AND the tier baseline
    """
    # Guard 1: never decide on thin evidence.
    if ev.n_measured < a.min_samples_per_band:
        return TdeVerdict(
            band=ev.band, verdict="INSUFFICIENT_DATA", data_source=ev.data_source,
            reason=(f"only {ev.n_measured} measured samples "
                    f"(need {a.min_samples_per_band})"),
        )

    # Avoid divide-by-zero on a degenerate band.
    if ev.direct_tokens <= 0:
        return TdeVerdict(
            band=ev.band, verdict="INSUFFICIENT_DATA", data_source=ev.data_source,
            reason="direct_tokens is 0 — cannot compute savings",
        )

    tde_net = (ev.direct_tokens - ev.tde_tokens) / ev.direct_tokens
    tier_net = (ev.direct_tokens - ev.tier_tokens) / ev.direct_tokens
    detail = {
        "tde_loss": ev.tde_loss, "tier_loss": ev.tier_loss,
        "quality_floor_loss": a.quality_floor_loss,
        "min_net_savings": a.min_net_savings,
        "min_margin_over_tier": a.min_margin_over_tier,
    }

    # Guard 2: TDE at unacceptable quality can never "win", regardless of tokens.
    tde_quality_ok = ev.tde_loss <= a.quality_floor_loss
    tier_quality_ok = ev.tier_loss <= a.quality_floor_loss

    # Does the simpler tier baseline already achieve real savings at held quality?
    tier_saves = tier_quality_ok and tier_net >= a.min_net_savings

    # Does TDE achieve real savings at held quality?
    tde_saves = tde_quality_ok and tde_net >= a.min_net_savings

    if not tde_saves and not tier_saves:
        return TdeVerdict(
            band=ev.band, verdict="NO_SAVINGS", data_source=ev.data_source,
            tde_net_savings=tde_net, tier_net_savings=tier_net,
            reason=("neither TDE nor whole-task-tier net-saves at held quality "
                    f"(tde_net={tde_net:.1%}, tier_net={tier_net:.1%})"),
            detail=detail,
        )

    # TDE must beat the tier baseline by the margin to justify decomposition.
    beats_tier = tde_saves and (tde_net - tier_net) >= a.min_margin_over_tier
    if beats_tier:
        return TdeVerdict(
            band=ev.band, verdict="TDE_WINS", data_source=ev.data_source,
            tde_net_savings=tde_net, tier_net_savings=tier_net,
            reason=(f"TDE net-saves {tde_net:.1%} at {ev.tde_loss:.1%} loss and "
                    f"beats tier ({tier_net:.1%}) by ≥{a.min_margin_over_tier:.0%}"),
            detail=detail,
        )

    return TdeVerdict(
        band=ev.band, verdict="TIER_WINS", data_source=ev.data_source,
        tde_net_savings=tde_net, tier_net_savings=tier_net,
        reason=("whole-task tiering achieves the savings; per-step TDE does not "
                "beat it by the margin — prefer the simpler path"),
        detail=detail,
    )


def evaluate_tde_verdict(
    bands: list[BandEvidence], a: GateAssumptions = DEFAULT_ASSUMPTIONS
) -> dict[str, Any]:
    """Roll per-band verdicts into an overall decision.

    Overall premise holds (`amplifier_survives=True`) iff TDE_WINS on at least one
    band on MEASURED data. A win on assumption-sourced data is a PREDICTION only
    and never sets amplifier_survives — the caller must re-run on measured data.
    """
    per_band = [evaluate_band(ev, a) for ev in bands]
    measured_wins = [
        v for v in per_band
        if v.verdict == "TDE_WINS" and v.data_source == "measured"
    ]
    predicted_wins = [
        v for v in per_band
        if v.verdict == "TDE_WINS" and v.data_source == "assumptions"
    ]
    any_measured = any(v.data_source == "measured"
                       and v.verdict != "INSUFFICIENT_DATA" for v in per_band)
    return {
        "per_band": per_band,
        "amplifier_survives": bool(measured_wins),
        "winning_bands": [v.band for v in measured_wins],
        "predicted_winning_bands": [v.band for v in predicted_wins],
        "decided_on_measured_data": any_measured,
        "assumptions": a,
    }


def synthetic_evidence_from_assumptions() -> list[BandEvidence]:
    """ADR-0222 Phase 2 — our current HYPOTHESIS encoded as runnable evidence.

    These numbers are NOT measured. They encode the Phase-0 finding (93-99% of a
    worker's token cost is context/prompt, cold per parallel worker) into a
    prediction: per-step TDE pays the big context tax N times (once per Haiku
    worker) while a direct turn / whole-task-tier pays it once warm, so TDE is
    predicted NET-NEGATIVE on small/moderate tasks and, at best, marginal on
    high-fan-out complex tasks where genuinely independent branches amortise it.

    Every field is data_source="assumptions" so the gate can only ever return a
    PREDICTION from this input — see the honesty invariant. Replace with
    `aggregate_measured_evidence()` output once the measurement week has data.
    """
    n = DEFAULT_ASSUMPTIONS.min_samples_per_band  # clear the sample gate so the
    #                                               PREDICTION (not INSUFFICIENT)
    #                                               is what surfaces
    return [
        # Trivial: one cheap turn is already tiny; splitting adds pure overhead.
        BandEvidence(band="trivial", direct_tokens=3000, tde_tokens=6500,
                     tde_loss=0.06, tier_tokens=2600, tier_loss=0.02,
                     n_measured=n, data_source="assumptions"),
        # Moderate: context tax paid per worker dominates; TDE loses to a warm turn.
        BandEvidence(band="moderate", direct_tokens=12000, tde_tokens=21000,
                     tde_loss=0.08, tier_tokens=9000, tier_loss=0.04,
                     n_measured=n, data_source="assumptions"),
        # Complex high-fan-out: independent branches amortise the tax somewhat, but
        # still not past the tier baseline under our assumptions.
        BandEvidence(band="complex", direct_tokens=40000, tde_tokens=34000,
                     tde_loss=0.07, tier_tokens=30000, tier_loss=0.05,
                     n_measured=n, data_source="assumptions"),
    ]
