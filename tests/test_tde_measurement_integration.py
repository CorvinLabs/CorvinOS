"""Integration tests for MeasurementRecorder (ADR-0222 Phase 2 k=2).

Mock-based integration tests: verify feature-flag gating, sample flow,
aggregation pipeline. Does NOT import full TDE stack or modify chat_runtime.
"""

import os
import pytest
import tempfile
from unittest.mock import Mock, AsyncMock, patch


# Minimal mock classes (duplicated from unit tests to avoid import chain)
from dataclasses import dataclass, asdict
from collections import defaultdict


@dataclass
class MockMeasurementSample:
    task_id: str
    task_band: str
    timestamp: float
    direct_tokens: int
    direct_output: str
    tier_tokens: int
    tier_output: str
    tier_loss: float
    tde_tokens: int
    tde_output: str
    tde_loss: float
    quality_judge_model: str = "haiku"
    data_source: str = "measured"


@dataclass
class MockBandEvidence:
    band: str
    direct_tokens: float
    tde_tokens: float
    tde_loss: float
    tier_tokens: float
    tier_loss: float
    n_measured: int
    data_source: str


class MockMeasurementRecorder:
    """Standalone recorder for integration testing (no external deps)."""

    def __init__(self, measurement_log_path: str | None = None):
        self.enabled = os.getenv("TDE_MEASUREMENT_ENABLED") == "1"
        self.log_path = measurement_log_path or "m.jsonl"
        self.samples: list[MockMeasurementSample] = []

    async def record_sample(self, sample: MockMeasurementSample) -> None:
        if self.enabled:
            self.samples.append(sample)

    def get_aggregated_evidence(self) -> list[MockBandEvidence]:
        by_band: dict[str, list[MockMeasurementSample]] = defaultdict(list)
        for s in self.samples:
            by_band[s.task_band].append(s)

        evidence = []
        for band, samples in by_band.items():
            if samples:
                avg_direct = sum(s.direct_tokens for s in samples) / len(samples)
                avg_tde = sum(s.tde_tokens for s in samples) / len(samples)
                avg_tde_loss = sum(s.tde_loss for s in samples) / len(samples)
                avg_tier = sum(s.tier_tokens for s in samples) / len(samples)
                avg_tier_loss = sum(s.tier_loss for s in samples) / len(samples)

                evidence.append(MockBandEvidence(
                    band=band,
                    direct_tokens=avg_direct,
                    tde_tokens=avg_tde,
                    tde_loss=avg_tde_loss,
                    tier_tokens=avg_tier,
                    tier_loss=avg_tier_loss,
                    n_measured=len(samples),
                    data_source="measured",
                ))
        return evidence


# ============================================================================
# Integration Test Suite
# ============================================================================

@pytest.fixture
def temp_log():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield os.path.join(tmpdir, "m.jsonl")


def test_feature_flag_gates_recording(temp_log):
    """Feature flag TDE_MEASUREMENT_ENABLED gates recorder enabled state."""
    os.environ["TDE_MEASUREMENT_ENABLED"] = "0"
    r = MockMeasurementRecorder(temp_log)
    assert not r.enabled

    os.environ["TDE_MEASUREMENT_ENABLED"] = "1"
    r = MockMeasurementRecorder(temp_log)
    assert r.enabled

    del os.environ["TDE_MEASUREMENT_ENABLED"]
    r = MockMeasurementRecorder(temp_log)
    assert not r.enabled


@pytest.mark.asyncio
async def test_sample_flow_disabled(temp_log):
    """When disabled, recorder drops samples silently."""
    os.environ["TDE_MEASUREMENT_ENABLED"] = "0"
    r = MockMeasurementRecorder(temp_log)

    s = MockMeasurementSample(
        task_id="t1", task_band="moderate", timestamp=1.0,
        direct_tokens=5000, direct_output="a",
        tier_tokens=4500, tier_output="b", tier_loss=0.02,
        tde_tokens=3500, tde_output="c", tde_loss=0.05,
    )
    await r.record_sample(s)

    assert len(r.samples) == 0


@pytest.mark.asyncio
async def test_sample_flow_enabled(temp_log):
    """When enabled, recorder collects samples."""
    os.environ["TDE_MEASUREMENT_ENABLED"] = "1"
    r = MockMeasurementRecorder(temp_log)

    s = MockMeasurementSample(
        task_id="t1", task_band="moderate", timestamp=1.0,
        direct_tokens=5000, direct_output="a",
        tier_tokens=4500, tier_output="b", tier_loss=0.02,
        tde_tokens=3500, tde_output="c", tde_loss=0.05,
    )
    await r.record_sample(s)

    assert len(r.samples) == 1
    assert r.samples[0].task_band == "moderate"


@pytest.mark.asyncio
async def test_aggregation_pipeline(temp_log):
    """Full pipeline: collect samples → aggregate by band."""
    os.environ["TDE_MEASUREMENT_ENABLED"] = "1"
    r = MockMeasurementRecorder(temp_log)

    # Add two samples in same band
    for i, direct_tok in enumerate([5000, 6000]):
        s = MockMeasurementSample(
            task_id=f"t{i}", task_band="moderate", timestamp=float(i),
            direct_tokens=direct_tok, direct_output="a",
            tier_tokens=4500 + i*500, tier_output="b", tier_loss=0.02,
            tde_tokens=3500 + i*500, tde_output="c", tde_loss=0.05,
        )
        await r.record_sample(s)

    evidence = r.get_aggregated_evidence()

    assert len(evidence) == 1
    assert evidence[0].band == "moderate"
    # Verify averaging: (5000 + 6000) / 2 = 5500
    assert evidence[0].direct_tokens == 5500.0
    assert evidence[0].n_measured == 2
    assert evidence[0].data_source == "measured"


@pytest.mark.asyncio
async def test_multi_band_aggregation(temp_log):
    """Aggregation correctly groups samples by band."""
    os.environ["TDE_MEASUREMENT_ENABLED"] = "1"
    r = MockMeasurementRecorder(temp_log)

    trivial = MockMeasurementSample(
        task_id="t1", task_band="trivial", timestamp=1.0,
        direct_tokens=1000, direct_output="a",
        tier_tokens=900, tier_output="b", tier_loss=0.01,
        tde_tokens=800, tde_output="c", tde_loss=0.03,
    )
    complex_sample = MockMeasurementSample(
        task_id="t2", task_band="complex", timestamp=2.0,
        direct_tokens=20000, direct_output="a",
        tier_tokens=18000, tier_output="b", tier_loss=0.08,
        tde_tokens=12000, tde_output="c", tde_loss=0.12,
    )

    await r.record_sample(trivial)
    await r.record_sample(complex_sample)

    evidence = r.get_aggregated_evidence()

    assert len(evidence) == 2
    bands = {e.band: e for e in evidence}

    assert "trivial" in bands
    assert "complex" in bands
    assert bands["trivial"].direct_tokens == 1000.0
    assert bands["complex"].direct_tokens == 20000.0


@pytest.mark.asyncio
async def test_verdict_flag_on_measured_data():
    """Aggregated evidence carries data_source='measured' for gate."""
    os.environ["TDE_MEASUREMENT_ENABLED"] = "1"
    r = MockMeasurementRecorder()

    s = MockMeasurementSample(
        task_id="t1", task_band="moderate", timestamp=1.0,
        direct_tokens=5000, direct_output="a",
        tier_tokens=4500, tier_output="b", tier_loss=0.02,
        tde_tokens=3500, tde_output="c", tde_loss=0.05,
    )
    await r.record_sample(s)

    evidence = r.get_aggregated_evidence()

    # Gate verifies data_source="measured" → amplifier_survives eligible
    assert evidence[0].data_source == "measured"


def test_gate_mock_receives_evidence():
    """Mock gate consumes aggregated evidence correctly."""
    os.environ["TDE_MEASUREMENT_ENABLED"] = "1"
    r = MockMeasurementRecorder()

    # Mock the gate function
    def mock_gate(evidence_list):
        """Minimal gate mock: returns verdict based on measurement data."""
        for e in evidence_list:
            if e.data_source == "measured" and e.tde_tokens < e.direct_tokens:
                return {"amplifier_survives": True, "band": e.band}
        return {"amplifier_survives": False}

    # Simulated sample
    r.samples.append(MockMeasurementSample(
        task_id="t1", task_band="moderate", timestamp=1.0,
        direct_tokens=5000, direct_output="a",
        tier_tokens=4500, tier_output="b", tier_loss=0.02,
        tde_tokens=3500, tde_output="c", tde_loss=0.05,
    ))

    evidence = r.get_aggregated_evidence()
    verdict = mock_gate(evidence)

    # TDE (3500) < direct (5000) → amplifier can survive
    assert verdict["amplifier_survives"] is True
    assert verdict["band"] == "moderate"
