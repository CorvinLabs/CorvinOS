"""Tests for ADR-0222 Phase 2 measurement sampler."""

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "operator" / "orchestration"))

from tde.tde_measurement import (  # noqa: E402
    MeasurementSample,
    AggregatedBandEvidence,
    aggregate_measured_evidence,
)
from tde.decision_gate import BandEvidence  # noqa: E402


def test_measurement_sample_creation():
    """Create a single measurement sample."""
    sample = MeasurementSample(
        task_id="run-123",
        task_band="moderate",
        timestamp=1234567890.0,
        direct_tokens=5000,
        direct_output="answer",
        tier_tokens=4500,
        tier_output="tier_answer",
        tier_loss=0.02,
        tde_tokens=3500,
        tde_output="tde_answer",
        tde_loss=0.05,
        quality_judge_model="opus",
    )
    assert sample.task_band == "moderate"
    assert sample.data_source == "measured"
    assert sample.direct_tokens == 5000


def test_aggregated_band_evidence_single_sample():
    """Aggregation on a single sample returns that sample's values."""
    sample = MeasurementSample(
        task_id="run-123",
        task_band="trivial",
        timestamp=1234567890.0,
        direct_tokens=1000,
        direct_output="a",
        tier_tokens=900,
        tier_output="b",
        tier_loss=0.01,
        tde_tokens=800,
        tde_output="c",
        tde_loss=0.03,
    )
    agg = AggregatedBandEvidence(band="trivial", samples=[sample])

    assert agg.direct_tokens == 1000.0
    assert agg.tier_tokens == 900.0
    assert agg.tier_loss == 0.01
    assert agg.tde_tokens == 800.0
    assert agg.tde_loss == 0.03
    assert agg.n_measured == 1


def test_aggregated_band_evidence_multiple_samples():
    """Aggregation averages across multiple samples."""
    samples = [
        MeasurementSample(
            task_id="run-1",
            task_band="complex",
            timestamp=1.0,
            direct_tokens=10000,
            direct_output="a",
            tier_tokens=9000,
            tier_output="b",
            tier_loss=0.05,
            tde_tokens=7000,
            tde_output="c",
            tde_loss=0.08,
        ),
        MeasurementSample(
            task_id="run-2",
            task_band="complex",
            timestamp=2.0,
            direct_tokens=12000,
            direct_output="a",
            tier_tokens=10000,
            tier_output="b",
            tier_loss=0.06,
            tde_tokens=8000,
            tde_output="c",
            tde_loss=0.09,
        ),
    ]
    agg = AggregatedBandEvidence(band="complex", samples=samples)

    assert agg.direct_tokens == 11000.0  # (10000 + 12000) / 2
    assert agg.tier_tokens == 9500.0  # (9000 + 10000) / 2
    assert agg.tier_loss == 0.055  # (0.05 + 0.06) / 2
    assert agg.tde_tokens == 7500.0  # (7000 + 8000) / 2
    assert agg.tde_loss == pytest.approx(0.085)  # (0.08 + 0.09) / 2
    assert agg.n_measured == 2


def test_aggregate_measured_evidence_single_band():
    """Aggregation groups samples by band and produces BandEvidence."""
    samples = [
        MeasurementSample(
            task_id="run-1",
            task_band="moderate",
            timestamp=1.0,
            direct_tokens=5000,
            direct_output="a",
            tier_tokens=4500,
            tier_output="b",
            tier_loss=0.02,
            tde_tokens=3500,
            tde_output="c",
            tde_loss=0.05,
        ),
        MeasurementSample(
            task_id="run-2",
            task_band="moderate",
            timestamp=2.0,
            direct_tokens=6000,
            direct_output="a",
            tier_tokens=5500,
            tier_output="b",
            tier_loss=0.03,
            tde_tokens=4000,
            tde_output="c",
            tde_loss=0.06,
        ),
    ]
    evidence = aggregate_measured_evidence(samples)

    assert len(evidence) == 1
    be = evidence[0]
    assert be.band == "moderate"
    assert be.direct_tokens == 5500.0
    assert be.tier_tokens == 5000.0
    assert be.tier_loss == 0.025
    assert be.tde_tokens == 3750.0
    assert be.tde_loss == 0.055
    assert be.n_measured == 2
    assert be.data_source == "measured"


def test_aggregate_measured_evidence_multiple_bands():
    """Aggregation groups samples by band correctly."""
    samples = [
        MeasurementSample(
            task_id="run-trivial",
            task_band="trivial",
            timestamp=1.0,
            direct_tokens=500,
            direct_output="a",
            tier_tokens=450,
            tier_output="b",
            tier_loss=0.01,
            tde_tokens=400,
            tde_output="c",
            tde_loss=0.02,
        ),
        MeasurementSample(
            task_id="run-complex",
            task_band="complex",
            timestamp=2.0,
            direct_tokens=20000,
            direct_output="a",
            tier_tokens=18000,
            tier_output="b",
            tier_loss=0.08,
            tde_tokens=12000,
            tde_output="c",
            tde_loss=0.12,
        ),
    ]
    evidence = aggregate_measured_evidence(samples)

    assert len(evidence) == 2
    bands = {e.band: e for e in evidence}

    trivial = bands["trivial"]
    assert trivial.n_measured == 1
    assert trivial.direct_tokens == 500.0

    complex_band = bands["complex"]
    assert complex_band.n_measured == 1
    assert complex_band.direct_tokens == 20000.0


def test_aggregate_measured_evidence_empty():
    """Empty input produces empty output."""
    evidence = aggregate_measured_evidence([])
    assert evidence == []


def test_band_evidence_data_source_is_measured():
    """Every BandEvidence from aggregate_measured_evidence has data_source='measured'."""
    samples = [
        MeasurementSample(
            task_id="run-1",
            task_band="moderate",
            timestamp=1.0,
            direct_tokens=5000,
            direct_output="a",
            tier_tokens=4500,
            tier_output="b",
            tier_loss=0.02,
            tde_tokens=3500,
            tde_output="c",
            tde_loss=0.05,
        ),
    ]
    evidence = aggregate_measured_evidence(samples)

    for be in evidence:
        assert be.data_source == "measured", (
            "BandEvidence must have data_source='measured' so the gate knows "
            "this is real data, not assumptions."
        )
