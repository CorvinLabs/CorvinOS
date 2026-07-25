"""k=4 Integration Tests — MeasurementRecorder hook in chat_runtime context.

Tests mock orchestration and feature-flag gating for Phase 1 integration.
"""

import os
import pytest
import asyncio
import time


@pytest.fixture(autouse=True)
def enable_measurement():
    """Enable TDE_MEASUREMENT_ENABLED for these tests."""
    os.environ["TDE_MEASUREMENT_ENABLED"] = "1"
    yield
    if "TDE_MEASUREMENT_ENABLED" in os.environ:
        del os.environ["TDE_MEASUREMENT_ENABLED"]


def test_mock_orchestrator_band_classification():
    """Verify band classification for measurement."""
    # Import directly from module, avoid tde/__init__.py dependency chain
    import sys
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "tde_measurement",
        "/home/shumway/projects/CorvinOS/operator/orchestration/tde/tde_measurement.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tde_measurement"] = mod
    spec.loader.exec_module(mod)
    MockTdeOrchestrator = mod.MockTdeOrchestrator

    assert MockTdeOrchestrator.classify_band("trivial") == "trivial"
    assert MockTdeOrchestrator.classify_band("complex") == "complex"
    assert MockTdeOrchestrator.classify_band("moderate") == "moderate"
    assert MockTdeOrchestrator.classify_band(None) == "moderate"  # default


def test_mock_direct_execution():
    """Mock direct baseline execution."""
    from operator.orchestration.tde.tde_measurement import MockTdeOrchestrator

    result = MockTdeOrchestrator.mock_direct_execution("test prompt")

    assert result["tokens"] > 0
    assert result["output"] is not None
    assert result["loss"] == 0.0  # Direct is reference


def test_mock_tier_execution():
    """Mock tier baseline execution."""
    from operator.orchestration.tde.tde_measurement import MockTdeOrchestrator

    result = MockTdeOrchestrator.mock_tier_execution("test prompt")

    assert result["tokens"] > 0
    assert result["output"] is not None
    assert 0.0 <= result["loss"] <= 1.0


@pytest.mark.asyncio
async def test_orchestrate_measurement_sample():
    """Mock orchestration produces valid MeasurementSample."""
    from operator.orchestration.tde.tde_measurement import MockTdeOrchestrator

    sample = await MockTdeOrchestrator.orchestrate_measurement(
        prompt="test task",
        tde_tokens=3500,
        tde_output="tde answer",
        task_complexity="moderate",
    )

    assert sample is not None
    assert sample.task_band == "moderate"
    assert sample.direct_tokens > 0
    assert sample.tier_tokens > 0
    assert sample.tde_tokens == 3500
    assert sample.tde_output == "tde answer"
    # Validation in __post_init__ ensures loss is in [0.0, 1.0]
    assert 0.0 <= sample.tde_loss <= 1.0


@pytest.mark.asyncio
async def test_k4_hook_measurement_disabled():
    """When disabled, orchestrator is not called (feature-flag gates it)."""
    if "TDE_MEASUREMENT_ENABLED" in os.environ:
        del os.environ["TDE_MEASUREMENT_ENABLED"]

    from operator.orchestration.tde.tde_measurement import (
        MeasurementRecorder,
        MockTdeOrchestrator,
    )

    recorder = MeasurementRecorder()
    assert not recorder.enabled

    # Even if orchestrator runs, disabled recorder drops sample
    sample = await MockTdeOrchestrator.orchestrate_measurement(
        prompt="test", tde_tokens=3500, tde_output="answer"
    )
    await recorder.record_sample(sample)

    assert len(recorder.samples) == 0


@pytest.mark.asyncio
async def test_k4_hook_measurement_enabled():
    """When enabled, hook collects sample from orchestrator."""
    os.environ["TDE_MEASUREMENT_ENABLED"] = "1"

    from operator.orchestration.tde.tde_measurement import (
        MeasurementRecorder,
        MockTdeOrchestrator,
    )

    recorder = MeasurementRecorder()
    assert recorder.enabled

    # Simulate hook: orchestrate → record
    sample = await MockTdeOrchestrator.orchestrate_measurement(
        prompt="test query",
        tde_tokens=3500,
        tde_output="tde response",
        task_complexity="complex",
    )
    await recorder.record_sample(sample)

    assert len(recorder.samples) == 1
    assert recorder.samples[0].task_band == "complex"
    assert recorder.samples[0].tde_tokens == 3500


@pytest.mark.asyncio
async def test_k4_hook_aggregates_multiple_samples():
    """Hook runs multiple times, samples aggregate by band."""
    os.environ["TDE_MEASUREMENT_ENABLED"] = "1"

    from operator.orchestration.tde.tde_measurement import (
        MeasurementRecorder,
        MockTdeOrchestrator,
    )

    recorder = MeasurementRecorder()

    # Simulate 3 hook invocations (different bands)
    for band, tde_tokens in [
        ("trivial", 2000),
        ("moderate", 3500),
        ("complex", 8000),
    ]:
        sample = await MockTdeOrchestrator.orchestrate_measurement(
            prompt="prompt",
            tde_tokens=tde_tokens,
            tde_output="answer",
            task_complexity=band,
        )
        await recorder.record_sample(sample)

    # Aggregation
    evidence = recorder.get_aggregated_evidence()

    assert len(evidence) == 3
    bands = {e.band: e for e in evidence}

    assert bands["trivial"].n_measured == 1
    assert bands["trivial"].tde_tokens == 2000.0

    assert bands["moderate"].n_measured == 1
    assert bands["moderate"].tde_tokens == 3500.0

    assert bands["complex"].n_measured == 1
    assert bands["complex"].tde_tokens == 8000.0


@pytest.mark.asyncio
async def test_k4_hook_gate_ready():
    """Aggregated samples ready for decision gate."""
    os.environ["TDE_MEASUREMENT_ENABLED"] = "1"

    from operator.orchestration.tde.tde_measurement import (
        MeasurementRecorder,
        MockTdeOrchestrator,
    )

    recorder = MeasurementRecorder()

    # Single moderate sample
    sample = await MockTdeOrchestrator.orchestrate_measurement(
        prompt="test",
        tde_tokens=3500,
        tde_output="answer",
        task_complexity="moderate",
    )
    await recorder.record_sample(sample)

    evidence = recorder.get_aggregated_evidence()

    # Gate will receive this
    assert len(evidence) == 1
    assert evidence[0].band == "moderate"
    assert evidence[0].data_source == "measured"
    # Gate can now evaluate: TDE_WINS / TIER_WINS / NO_SAVINGS based on these metrics
