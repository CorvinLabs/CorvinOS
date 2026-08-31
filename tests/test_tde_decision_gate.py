"""ADR-0222 Phase 2 — decision gate + F5 whole-task-tier baseline.

Pure-logic tests (no LLM). Verifies each verdict branch, the honesty invariant
(the gate cannot rubber-stamp on thin or assumption-sourced data), and that the
F5 baseline primitive exists with the right shape.

Run: python3 -m pytest tests/test_tde_decision_gate.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "operator" / "orchestration"))
sys.path.insert(0, str(_REPO / "operator"))
sys.path.insert(0, str(_REPO / "operator" / "bridges" / "shared"))

from tde.decision_gate import (  # noqa: E402
    BandEvidence,
    DEFAULT_ASSUMPTIONS,
    GateAssumptions,
    evaluate_band,
    evaluate_tde_verdict,
    synthetic_evidence_from_assumptions,
)

N = DEFAULT_ASSUMPTIONS.min_samples_per_band


def _ev(**kw):
    base = dict(
        band="moderate", direct_tokens=10000, tde_tokens=5000, tde_loss=0.02,
        tier_tokens=9000, tier_loss=0.02, n_measured=N, data_source="measured",
    )
    base.update(kw)
    return BandEvidence(**base)


# ── verdict branches ─────────────────────────────────────────────────────────

def test_tde_wins_when_it_beats_direct_and_tier_at_quality():
    # TDE saves 50%, tier saves 10% → TDE beats tier by 40pp, quality fine.
    v = evaluate_band(_ev(tde_tokens=5000, tier_tokens=9000))
    assert v.verdict == "TDE_WINS"
    assert v.tde_net_savings == pytest.approx(0.5)


def test_tier_wins_when_tde_does_not_beat_the_tier_margin():
    # Both save, but TDE (16%) barely edges tier (15%) — below the 5pp margin.
    v = evaluate_band(_ev(tde_tokens=8400, tier_tokens=8500))
    assert v.verdict == "TIER_WINS"


def test_no_savings_when_nothing_beats_direct():
    # TDE COSTS more (context tax), tier only trims a little (< min_net_savings).
    v = evaluate_band(_ev(tde_tokens=13000, tier_tokens=9500))
    assert v.verdict == "NO_SAVINGS"


def test_tde_high_quality_loss_cannot_win_even_if_cheap():
    # Cheapest TDE in the world doesn't win if it breaks the quality floor.
    v = evaluate_band(_ev(tde_tokens=1000, tde_loss=0.40, tier_tokens=20000,
                          tier_loss=0.40))
    assert v.verdict == "NO_SAVINGS"


# ── honesty invariant ────────────────────────────────────────────────────────

def test_thin_evidence_returns_insufficient_not_a_verdict():
    v = evaluate_band(_ev(n_measured=N - 1, tde_tokens=1000))
    assert v.verdict == "INSUFFICIENT_DATA"


def test_zero_direct_tokens_is_insufficient_not_a_crash():
    v = evaluate_band(_ev(direct_tokens=0))
    assert v.verdict == "INSUFFICIENT_DATA"


def test_assumption_win_never_sets_amplifier_survives():
    # A TDE_WINS on assumption-sourced data is a PREDICTION, not a decision.
    winning_but_assumed = _ev(tde_tokens=4000, tier_tokens=9000,
                              data_source="assumptions")
    out = evaluate_tde_verdict([winning_but_assumed])
    assert out["per_band"][0].verdict == "TDE_WINS"
    assert out["per_band"][0].data_source == "assumptions"
    assert out["amplifier_survives"] is False          # never on assumptions
    assert out["predicted_winning_bands"] == ["moderate"]
    assert out["decided_on_measured_data"] is False


def test_measured_win_sets_amplifier_survives():
    out = evaluate_tde_verdict([_ev(tde_tokens=4000, tier_tokens=9000)])
    assert out["amplifier_survives"] is True
    assert out["winning_bands"] == ["moderate"]
    assert out["decided_on_measured_data"] is True


# ── the encoded hypothesis ───────────────────────────────────────────────────

def test_synthetic_hypothesis_predicts_tde_does_not_survive():
    # Our current assumptions (93-99% context tax paid per worker) predict TDE
    # does NOT net-win on any band vs a warm tier turn. This is the documented
    # PREDICTION Phase 2 exists to confirm or refute — and it must never flip
    # amplifier_survives because it is assumption-sourced.
    out = evaluate_tde_verdict(synthetic_evidence_from_assumptions())
    assert out["amplifier_survives"] is False
    assert out["decided_on_measured_data"] is False
    # every band is either TIER_WINS or NO_SAVINGS under the hypothesis
    assert all(v.verdict in ("TIER_WINS", "NO_SAVINGS") for v in out["per_band"])


def test_assumptions_are_tunable_without_touching_logic():
    strict = GateAssumptions(min_net_savings=0.60)   # demand 60% net savings
    v = evaluate_band(_ev(tde_tokens=5000, tier_tokens=9000), strict)  # only 50%
    assert v.verdict in ("TIER_WINS", "NO_SAVINGS")  # 50% < 60% → not a win


# ── F5 baseline primitive shape ──────────────────────────────────────────────

def test_whole_task_tier_baseline_exists_and_is_async():
    import asyncio
    import inspect
    from tde.tde_engine import whole_task_tier_baseline
    assert inspect.iscoroutinefunction(whole_task_tier_baseline)
    sig = inspect.signature(whole_task_tier_baseline)
    for p in ("engine_id", "user_model", "workload_type", "confidence"):
        assert p in sig.parameters


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
