"""Tests for decision_gate with MEASURED evidence (ADR-0222 Phase 2)."""

import pytest
from operator.orchestration.tde.decision_gate import (
    BandEvidence,
    GateAssumptions,
    TdeVerdict,
    evaluate_band,
    evaluate_tde_verdict,
)


def test_verdict_on_measured_data():
    """Verdict with measured data has correct data_source."""
    ev = BandEvidence(
        band="moderate",
        direct_tokens=5000.0,
        tde_tokens=3500.0,
        tde_loss=0.03,
        tier_tokens=4500.0,
        tier_loss=0.02,
        n_measured=30,
        data_source="measured",
    )
    verdict = evaluate_band(ev)

    assert verdict.data_source == "measured"


def test_verdict_tde_wins_on_measured():
    """TDE can win on measured data if it beats tier and holds quality."""
    ev = BandEvidence(
        band="complex",
        direct_tokens=20000.0,
        tde_tokens=14000.0,  # 30% savings
        tde_loss=0.04,  # within quality floor
        tier_tokens=18000.0,  # 10% savings (not enough)
        tier_loss=0.02,
        n_measured=35,
        data_source="measured",
    )
    verdict = evaluate_band(ev)

    assert verdict.verdict == "TDE_WINS"
    assert verdict.data_source == "measured"
    assert verdict.tde_net_savings > 0.25  # 30% - margin


def test_verdict_tier_wins_on_measured():
    """Tier can win on measured data if TDE doesn't beat it by margin."""
    ev = BandEvidence(
        band="moderate",
        direct_tokens=5000.0,
        tde_tokens=4000.0,  # 20% savings
        tde_loss=0.03,
        tier_tokens=4100.0,  # 18% savings (too close to TDE)
        tier_loss=0.02,
        n_measured=32,
        data_source="measured",
    )
    verdict = evaluate_band(ev)

    # TDE saves 20%, tier saves 18%; margin needed is 5pp
    # TDE-tier gap = 2pp, not enough
    assert verdict.verdict == "TIER_WINS"
    assert verdict.data_source == "measured"


def test_verdict_no_savings_on_measured():
    """No savings on measured data when nothing beats direct."""
    ev = BandEvidence(
        band="trivial",
        direct_tokens=1000.0,
        tde_tokens=950.0,  # 5% savings (below min_net_savings=15%)
        tde_loss=0.02,
        tier_tokens=980.0,  # 2% savings
        tier_loss=0.01,
        n_measured=40,
        data_source="measured",
    )
    verdict = evaluate_band(ev)

    assert verdict.verdict == "NO_SAVINGS"
    assert verdict.data_source == "measured"


def test_evaluate_tde_verdict_measured_wins():
    """Roll-up: amplifier_survives=True iff TDE_WINS on MEASURED data."""
    bands = [
        BandEvidence(
            band="complex",
            direct_tokens=20000.0,
            tde_tokens=14000.0,
            tde_loss=0.04,
            tier_tokens=18000.0,
            tier_loss=0.02,
            n_measured=35,
            data_source="measured",
        ),
    ]
    result = evaluate_tde_verdict(bands)

    assert result["amplifier_survives"] is True
    assert len(result["winning_bands"]) == 1
    assert result["winning_bands"][0] == "complex"
    assert result["decided_on_measured_data"] is True


def test_evaluate_tde_verdict_measured_mixed():
    """Mix of TDE_WINS and NO_SAVINGS: survives if any measured win."""
    bands = [
        BandEvidence(
            band="complex",
            direct_tokens=20000.0,
            tde_tokens=14000.0,
            tde_loss=0.04,
            tier_tokens=18000.0,
            tier_loss=0.02,
            n_measured=35,
            data_source="measured",  # TDE_WINS here
        ),
        BandEvidence(
            band="trivial",
            direct_tokens=1000.0,
            tde_tokens=950.0,
            tde_loss=0.02,
            tier_tokens=980.0,
            tier_loss=0.01,
            n_measured=40,
            data_source="measured",  # NO_SAVINGS here
        ),
    ]
    result = evaluate_tde_verdict(bands)

    # Amplifier survives because complex band wins on measured
    assert result["amplifier_survives"] is True
    assert len(result["winning_bands"]) == 1
    assert "complex" in result["winning_bands"]


def test_evaluate_tde_verdict_assumptions_only():
    """Prediction mode: TDE_WINS on assumptions does NOT set amplifier_survives."""
    bands = [
        BandEvidence(
            band="moderate",
            direct_tokens=5000.0,
            tde_tokens=3000.0,
            tde_loss=0.04,
            tier_tokens=4500.0,
            tier_loss=0.02,
            n_measured=30,
            data_source="assumptions",  # ASSUMPTIONS, not measured
        ),
    ]
    result = evaluate_tde_verdict(bands)

    # TDE technically wins, but on assumptions
    assert result["amplifier_survives"] is False, (
        "amplifier_survives must be False when winning only on assumptions"
    )
    assert len(result["predicted_winning_bands"]) == 1
    assert result["decided_on_measured_data"] is False


def test_data_source_propagates_through_verdict():
    """data_source field propagates from BandEvidence through TdeVerdict."""
    ev_measured = BandEvidence(
        band="complex",
        direct_tokens=20000.0,
        tde_tokens=14000.0,
        tde_loss=0.04,
        tier_tokens=18000.0,
        tier_loss=0.02,
        n_measured=35,
        data_source="measured",
    )
    ev_assumed = BandEvidence(
        band="trivial",
        direct_tokens=1000.0,
        tde_tokens=500.0,
        tde_loss=0.05,
        tier_tokens=800.0,
        tier_loss=0.03,
        n_measured=30,
        data_source="assumptions",
    )

    v_measured = evaluate_band(ev_measured)
    v_assumed = evaluate_band(ev_assumed)

    assert v_measured.data_source == "measured"
    assert v_assumed.data_source == "assumptions"
