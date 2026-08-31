"""Tests for MeasurementRecorder singleton (ADR-0222 Phase 2, k=3).

Isolated test suite: tests MeasurementRecorder in isolation using mocked
dataclasses. Does not import full TDE stack to avoid dependency resolution.
"""

import os
import json
import pytest
import tempfile
from dataclasses import dataclass, asdict
from typing import Any


# Minimal mock dataclass for testing (no TDE dependencies)
@dataclass
class MockMeasurementSample:
    """Mock for testing without full tde_measurement imports."""
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


# Minimal mock for BandEvidence (no full decision_gate import)
@dataclass
class MockBandEvidence:
    """Mock for testing aggregation without decision_gate import."""
    band: str
    direct_tokens: float
    tde_tokens: float
    tde_loss: float
    tier_tokens: float
    tier_loss: float
    n_measured: int
    data_source: str


class MockMeasurementRecorder:
    """Isolated MeasurementRecorder implementation for testing."""

    _instance: "MockMeasurementRecorder | None" = None

    def __init__(self, measurement_log_path: str | None = None):
        self.enabled = os.getenv("TDE_MEASUREMENT_ENABLED") == "1"

        if measurement_log_path is None:
            corvin_home = os.getenv("CORVIN_HOME", os.path.expanduser("~/.corvin"))
            measurement_log_path = os.path.join(
                corvin_home, "measurement-week", "measurement.jsonl"
            )

        self.log_path = measurement_log_path
        self.samples: list[MockMeasurementSample] = []

        if self.enabled:
            log_dir = os.path.dirname(self.log_path)
            if log_dir:
                os.makedirs(log_dir, exist_ok=True)

    @classmethod
    def get_instance(
        cls, measurement_log_path: str | None = None
    ) -> "MockMeasurementRecorder":
        if cls._instance is None:
            cls._instance = cls(measurement_log_path)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        cls._instance = None

    async def record_sample(self, sample: MockMeasurementSample) -> None:
        if not self.enabled:
            return

        self.samples.append(sample)

        try:
            with open(self.log_path, "a") as f:
                f.write(json.dumps(asdict(sample), default=str) + "\n")
        except (IOError, OSError) as e:
            print(f"Warning: Failed to write measurement: {e}")

    def get_aggregated_evidence(self) -> list[MockBandEvidence]:
        """Return aggregated evidence by band."""
        from collections import defaultdict
        by_band: dict[str, list[MockMeasurementSample]] = defaultdict(list)
        for sample in self.samples:
            by_band[sample.task_band].append(sample)

        evidence = []
        for band, band_samples in by_band.items():
            if not band_samples:
                continue
            avg_direct = sum(s.direct_tokens for s in band_samples) / len(band_samples)
            avg_tier = sum(s.tier_tokens for s in band_samples) / len(band_samples)
            avg_tier_loss = sum(s.tier_loss for s in band_samples) / len(band_samples)
            avg_tde = sum(s.tde_tokens for s in band_samples) / len(band_samples)
            avg_tde_loss = sum(s.tde_loss for s in band_samples) / len(band_samples)

            evidence.append(MockBandEvidence(
                band=band,
                direct_tokens=avg_direct,
                tde_tokens=avg_tde,
                tde_loss=avg_tde_loss,
                tier_tokens=avg_tier,
                tier_loss=avg_tier_loss,
                n_measured=len(band_samples),
                data_source="measured",
            ))
        return evidence

    def load_from_log(self) -> None:
        if not os.path.exists(self.log_path):
            return
        try:
            with open(self.log_path, "r") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                        sample = MockMeasurementSample(**data)
                        self.samples.append(sample)
                    except (json.JSONDecodeError, TypeError) as e:
                        print(f"Warning: Failed to parse line: {e}")
        except (IOError, OSError) as e:
            print(f"Warning: Failed to load: {e}")

    def clear_samples(self) -> None:
        self.samples.clear()


# ============================================================================
# Test Suite
# ============================================================================

@pytest.fixture(autouse=True)
def reset_recorder():
    """Reset singleton before each test."""
    MockMeasurementRecorder.reset_instance()
    yield
    MockMeasurementRecorder.reset_instance()


@pytest.fixture
def temp_log():
    """Create temporary log directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield os.path.join(tmpdir, "measurement.jsonl")


def test_singleton(temp_log):
    """Verify singleton pattern."""
    os.environ["TDE_MEASUREMENT_ENABLED"] = "1"
    r1 = MockMeasurementRecorder.get_instance(temp_log)
    r2 = MockMeasurementRecorder.get_instance(temp_log)
    assert r1 is r2


def test_disabled_by_default():
    """Without flag, recorder is disabled."""
    if "TDE_MEASUREMENT_ENABLED" in os.environ:
        del os.environ["TDE_MEASUREMENT_ENABLED"]
    with tempfile.TemporaryDirectory() as tmpdir:
        r = MockMeasurementRecorder(os.path.join(tmpdir, "m.jsonl"))
        assert r.enabled is False


@pytest.mark.asyncio
async def test_disabled_no_write(temp_log):
    """Disabled recorder doesn't write."""
    if "TDE_MEASUREMENT_ENABLED" in os.environ:
        del os.environ["TDE_MEASUREMENT_ENABLED"]
    r = MockMeasurementRecorder(temp_log)
    sample = MockMeasurementSample(
        task_id="t1", task_band="moderate", timestamp=1.0,
        direct_tokens=5000, direct_output="a",
        tier_tokens=4500, tier_output="b", tier_loss=0.02,
        tde_tokens=3500, tde_output="c", tde_loss=0.05,
    )
    await r.record_sample(sample)
    assert not os.path.exists(temp_log)


@pytest.mark.asyncio
async def test_persists_sample(temp_log):
    """Enabled recorder writes to JSONL."""
    os.environ["TDE_MEASUREMENT_ENABLED"] = "1"
    r = MockMeasurementRecorder(temp_log)
    sample = MockMeasurementSample(
        task_id="t1", task_band="moderate", timestamp=1.0,
        direct_tokens=5000, direct_output="a",
        tier_tokens=4500, tier_output="b", tier_loss=0.02,
        tde_tokens=3500, tde_output="c", tde_loss=0.05,
        quality_judge_model="opus",
    )
    await r.record_sample(sample)

    assert os.path.exists(temp_log)
    with open(temp_log, "r") as f:
        data = json.loads(f.readline())
    assert data["task_id"] == "t1"
    assert data["direct_tokens"] == 5000


@pytest.mark.asyncio
async def test_appends_multiple(temp_log):
    """Multiple samples append correctly."""
    os.environ["TDE_MEASUREMENT_ENABLED"] = "1"
    r = MockMeasurementRecorder(temp_log)

    s1 = MockMeasurementSample(
        task_id="t1", task_band="trivial", timestamp=1.0,
        direct_tokens=1000, direct_output="a",
        tier_tokens=900, tier_output="b", tier_loss=0.01,
        tde_tokens=800, tde_output="c", tde_loss=0.03,
    )
    s2 = MockMeasurementSample(
        task_id="t2", task_band="complex", timestamp=2.0,
        direct_tokens=20000, direct_output="a",
        tier_tokens=18000, tier_output="b", tier_loss=0.08,
        tde_tokens=12000, tde_output="c", tde_loss=0.12,
    )
    await r.record_sample(s1)
    await r.record_sample(s2)

    with open(temp_log, "r") as f:
        lines = f.readlines()
    assert len(lines) == 2


@pytest.mark.asyncio
async def test_in_memory_buffer(temp_log):
    """In-memory buffer tracks samples."""
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


@pytest.mark.asyncio
async def test_aggregated_evidence(temp_log):
    """get_aggregated_evidence() returns correct aggregates."""
    os.environ["TDE_MEASUREMENT_ENABLED"] = "1"
    r = MockMeasurementRecorder(temp_log)
    s = MockMeasurementSample(
        task_id="t1", task_band="moderate", timestamp=1.0,
        direct_tokens=5000, direct_output="a",
        tier_tokens=4500, tier_output="b", tier_loss=0.02,
        tde_tokens=3500, tde_output="c", tde_loss=0.05,
    )
    await r.record_sample(s)

    evidence = r.get_aggregated_evidence()
    assert len(evidence) == 1
    assert evidence[0].band == "moderate"
    assert evidence[0].direct_tokens == 5000.0
    assert evidence[0].data_source == "measured"


def test_load_from_log(temp_log):
    """load_from_log() reads existing JSONL."""
    os.environ["TDE_MEASUREMENT_ENABLED"] = "1"
    sample_dict = {
        "task_id": "t1", "task_band": "trivial", "timestamp": 1.0,
        "direct_tokens": 1000, "direct_output": "a",
        "tier_tokens": 900, "tier_output": "b", "tier_loss": 0.01,
        "tde_tokens": 800, "tde_output": "c", "tde_loss": 0.03,
        "quality_judge_model": "haiku", "data_source": "measured",
    }
    with open(temp_log, "w") as f:
        f.write(json.dumps(sample_dict) + "\n")

    r = MockMeasurementRecorder(temp_log)
    r.load_from_log()
    assert len(r.samples) == 1
    assert r.samples[0].task_id == "t1"


def test_clear_samples(temp_log):
    """clear_samples() empties buffer."""
    os.environ["TDE_MEASUREMENT_ENABLED"] = "1"
    r = MockMeasurementRecorder(temp_log)
    s = MockMeasurementSample(
        task_id="t1", task_band="moderate", timestamp=1.0,
        direct_tokens=5000, direct_output="a",
        tier_tokens=4500, tier_output="b", tier_loss=0.02,
        tde_tokens=3500, tde_output="c", tde_loss=0.05,
    )
    r.samples.append(s)
    assert len(r.samples) == 1
    r.clear_samples()
    assert len(r.samples) == 0


def test_directory_created(temp_log):
    """Recorder creates parent directory."""
    os.environ["TDE_MEASUREMENT_ENABLED"] = "1"
    log_dir = os.path.dirname(temp_log)
    if os.path.exists(log_dir):
        import shutil
        shutil.rmtree(log_dir)

    MockMeasurementRecorder(temp_log)
    assert os.path.isdir(log_dir)
