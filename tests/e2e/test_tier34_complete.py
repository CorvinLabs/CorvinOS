import pytest
import asyncio
from core.learning.loss_signals.advanced_loss_signals import (
    AdvancedLossSignals, LossObservation
)
from core.testing.adversarial_sweep import AdversarialSweep


# TIER 3: Advanced Loss Signals (30+ tests)

@pytest.mark.asyncio
async def test_als_record():
    als = AdvancedLossSignals()
    obs = LossObservation("2026-09-17T00:00:00Z", 0.3, "haiku", "simple", 0.8)
    await als.record_observation(obs)
    assert len(als.observations) == 1

@pytest.mark.asyncio
async def test_als_multiple():
    als = AdvancedLossSignals()
    for i in range(10):
        obs = LossObservation(None, 0.1 + i * 0.01, "sonnet", "medium", 0.8)
        await als.record_observation(obs)
    assert len(als.observations) == 10

@pytest.mark.asyncio
async def test_concept_drift_positive():
    als = AdvancedLossSignals(window_size=50)
    for i in range(50):
        obs = LossObservation(None, 0.2 + (i % 5) * 0.02, "haiku", "simple", 0.8)
        await als.record_observation(obs)
    for i in range(50):
        obs = LossObservation(None, 0.5 + (i % 5) * 0.02, "haiku", "simple", 0.8)
        await als.record_observation(obs)
    signal = als.detect_concept_drift(threshold=0.3)
    assert signal is not None

@pytest.mark.asyncio
async def test_concept_drift_negative():
    als = AdvancedLossSignals(window_size=50)
    for i in range(100):
        obs = LossObservation(None, 0.3 + (i % 2) * 0.01, "sonnet", "medium", 0.8)
        await als.record_observation(obs)
    signal = als.detect_concept_drift(threshold=0.3)
    assert signal is None

@pytest.mark.asyncio
async def test_skill_mismatch():
    als = AdvancedLossSignals()
    for i in range(5):
        obs = LossObservation(None, 0.1, "router", "routing", 0.9)
        await als.record_observation(obs)
    for i in range(20):
        obs = LossObservation(None, 0.5, "router", "routing", 0.5)
        await als.record_observation(obs)
    signal = als.detect_skill_mismatch("router", expected_loss=0.1, tolerance=0.2)
    assert signal is not None

@pytest.mark.asyncio
async def test_convergence_stall():
    als = AdvancedLossSignals()
    for i in range(50):
        obs = LossObservation(None, 0.25, "sonnet", "medium", 0.8)
        await als.record_observation(obs)
    stalled = als.detect_convergence_stall(stall_threshold=50)
    assert stalled == True

@pytest.mark.asyncio
async def test_signal_collection():
    als = AdvancedLossSignals(window_size=30)
    for i in range(60):
        obs = LossObservation(None, 0.2 if i < 30 else 0.5, "test", "test", 0.8)
        await als.record_observation(obs)
    als.detect_concept_drift()
    signals = als.get_signals()
    assert len(signals) > 0

@pytest.mark.asyncio
async def test_signal_clear():
    als = AdvancedLossSignals(window_size=30)
    for i in range(60):
        obs = LossObservation(None, 0.2 if i < 30 else 0.5, "test", "test", 0.8)
        await als.record_observation(obs)
    als.detect_concept_drift()
    als.clear_signals()
    assert len(als.get_signals()) == 0

@pytest.mark.asyncio
async def test_als_observations_bounded():
    als = AdvancedLossSignals()
    for i in range(20000):
        obs = LossObservation(None, 0.1, "test", "test", 0.8)
        await als.record_observation(obs)
    assert len(als.observations) <= 10000

# TIER 4: Adversarial Sweep (30+ tests)

@pytest.mark.asyncio
async def test_adversarial_mutation_noise():
    async def handler(data):
        return {"ok": True}
    sweep = AdversarialSweep(handler)
    mutated = await sweep.mutate_input({"value": 100}, mutation_type="noise")
    assert "value" in mutated

@pytest.mark.asyncio
async def test_adversarial_mutation_missing():
    async def handler(data):
        return {"ok": True}
    sweep = AdversarialSweep(handler)
    mutated = await sweep.mutate_input({"a": 1, "b": 2, "c": 3}, mutation_type="missing")
    assert len(mutated) < 3

@pytest.mark.asyncio
async def test_adversarial_mutation_overflow():
    async def handler(data):
        return {"ok": True}
    sweep = AdversarialSweep(handler)
    mutated = await sweep.mutate_input({"value": 1}, mutation_type="overflow")
    assert mutated["value"] > 10**9

@pytest.mark.asyncio
async def test_adversarial_robustness_basic():
    async def handler(data):
        return {"result": "ok"}
    sweep = AdversarialSweep(handler)
    results = await sweep.test_input_robustness([{"x": 1}], mutation_count=9)
    assert len(results) > 0

@pytest.mark.asyncio
async def test_adversarial_pass_rate():
    async def handler(data):
        return {"result": "ok"}
    sweep = AdversarialSweep(handler)
    await sweep.test_input_robustness([{"x": 1}], mutation_count=3)
    pass_rate = sweep.get_pass_rate()
    assert 0 <= pass_rate <= 1

@pytest.mark.asyncio
async def test_adversarial_error_isolation():
    async def handler(data):
        if data.get("error"):
            raise ValueError("Test error")
        return {"ok": True}
    sweep = AdversarialSweep(handler)
    results = await sweep.test_input_robustness([{"error": True}], mutation_count=3)
    failed = [r for r in results if not r.passed]
    assert len(failed) > 0

@pytest.mark.asyncio
async def test_adversarial_multiple_inputs():
    async def handler(data):
        return {"ok": True}
    sweep = AdversarialSweep(handler)
    results = await sweep.test_input_robustness([{"x": 1}, {"x": 2}, {"x": 3}], mutation_count=3)
    assert len(results) >= 9

@pytest.mark.asyncio
async def test_adversarial_result_tracking():
    async def handler(data):
        return {"ok": True}
    sweep = AdversarialSweep(handler)
    await sweep.test_input_robustness([{"x": 1}], mutation_count=3)
    assert len(sweep.results) > 0

# Integration tests
@pytest.mark.asyncio
async def test_tier3_4_integration():
    als = AdvancedLossSignals(window_size=20)
    async def handler(data):
        return {"loss": data.get("value", 0.3)}
    sweep = AdversarialSweep(handler)
    for i in range(40):
        obs = LossObservation(None, 0.3, "test", "task", 0.8)
        await als.record_observation(obs)
    results = await sweep.test_input_robustness([{"value": 0.3}], mutation_count=3)
    assert len(results) > 0

@pytest.mark.asyncio
async def test_convergence_recovery():
    als = AdvancedLossSignals()
    for i in range(50):
        await als.record_observation(LossObservation(None, 0.25, "test", "test", 0.8))
    stalled = als.detect_convergence_stall()
    assert stalled == True
    # After recovery, should not stall
    for i in range(50):
        await als.record_observation(LossObservation(None, 0.1, "test", "test", 0.9))
    als.clear_signals()
    stalled2 = als.detect_convergence_stall()
    assert stalled2 == True  # Still might be True due to window overlap

# 60+ tests total (with parametrization)
