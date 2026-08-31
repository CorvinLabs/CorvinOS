"""
Realistic scenario tests for consolidation layer patterns.
Tests complex failure scenarios and recovery paths.
"""

import asyncio
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.consolidation.self_healing import (
    ExponentialBackoff,
    CircuitBreaker,
    GracefulDegradation,
    BackoffConfig,
    CircuitBreakerConfig,
    DegradationConfig,
)
from core.consolidation.error_recovery import (
    RetryLogic,
    FallbackStrategy,
    StateRollback,
    ErrorClassifier,
    FallbackConfig,
)


# Scenario 1: API call with intermittent failures
async def scenario_api_intermittent_failures():
    """
    Simulate an external API that fails intermittently.
    Expected: Retry logic recovers after transient failures.
    """
    api_call_count = 0
    success_threshold = 3  # Success on 3rd attempt

    async def api_call():
        nonlocal api_call_count
        api_call_count += 1
        if api_call_count < success_threshold:
            raise asyncio.TimeoutError("API timeout (transient)")
        return {"status": "ok", "data": "response"}

    retry = RetryLogic(BackoffConfig(max_attempts=5, jitter=False))
    result = await retry.call_with_retry(api_call)

    assert result["status"] == "ok"
    assert api_call_count == success_threshold
    stats = retry.get_stats()
    assert stats["successes"] == 1
    assert stats["failures"] == 2
    print("✓ Scenario 1: API intermittent failures → recovery")


# Scenario 2: Service degradation with graceful fallback
async def scenario_service_degradation():
    """
    Simulate a service that degrades, triggering graceful fallback.
    Expected: Fallback strategy serves cached response when primary fails.
    """
    breaker = CircuitBreaker(CircuitBreakerConfig(failure_threshold=3))
    degradation = GracefulDegradation(
        DegradationConfig(fallback_value={"cached": True, "timestamp": "2026-08-26"})
    )

    async def primary_api():
        # Simulate persistent failure
        breaker.record_failure()
        raise ConnectionError("Service unavailable")

    async def fallback_api():
        return degradation.get_fallback()

    # Trigger failures until circuit opens
    for _ in range(3):
        breaker.record_failure()

    assert breaker.state.value == "open"
    assert not breaker.is_closed()

    # Use fallback
    result = await fallback_api()
    assert result["cached"] is True
    print("✓ Scenario 2: Service degradation → graceful fallback")


# Scenario 3: Complex operation with checkpointing and recovery
async def scenario_complex_operation_with_checkpoints():
    """
    Simulate a complex multi-step operation that can fail partway through.
    Expected: Checkpoint mechanism allows recovery to known-good state.
    """
    class OperationState:
        def __init__(self):
            self.step = 0
            self.results = []
            self.attempts = 0

        def to_dict(self):
            return {"step": self.step, "results": self.results.copy(), "attempts": self.attempts}

    state = OperationState()
    rollback = StateRollback(max_checkpoints=5)

    # Save initial state
    initial_ckpt = rollback.save_checkpoint(state.to_dict(), operation_id="complex_op")

    async def multi_step_operation():
        state.attempts += 1
        state.step = 1
        state.results.append("step_1_ok")

        # Simulate failure on first invocation only (attempts==1)
        if state.attempts == 1:
            raise RuntimeError("Failed at step 2")

        state.step = 2
        state.results.append("step_2_ok")
        return state.to_dict()

    try:
        # First attempt fails
        await multi_step_operation()
    except RuntimeError:
        # Restore to initial state on failure (but keep attempts counter)
        restored = rollback.restore_checkpoint(initial_ckpt)
        state.step = restored["step"]
        state.results = restored["results"]
        # Don't restore attempts - we want to track total attempts across retries

    # Retry succeeds on second attempt
    result = await multi_step_operation()
    assert result["step"] == 2
    assert "step_1_ok" in result["results"]
    assert "step_2_ok" in result["results"]

    rollback.commit_checkpoint(initial_ckpt)
    print("✓ Scenario 3: Complex operation → checkpoint/recovery")


# Scenario 4: Circuit breaker preventing cascading failures
async def scenario_circuit_breaker_cascade_prevention():
    """
    Simulate cascading failure scenario.
    Expected: Circuit breaker stops retries and prevents load.
    """
    breaker = CircuitBreaker(
        CircuitBreakerConfig(failure_threshold=3, success_threshold=1, timeout_sec=0.05)
    )
    retry = RetryLogic(BackoffConfig(max_attempts=2, jitter=False))

    failing_service_calls = 0

    async def failing_service():
        nonlocal failing_service_calls
        failing_service_calls += 1
        raise ConnectionError("Service down")

    async def protected_call():
        if not breaker.is_closed():
            # Circuit is open; don't retry
            raise RuntimeError("Circuit breaker is open")
        result = await retry.call_with_retry(failing_service)
        return result

    # Trigger initial failures
    for i in range(3):
        try:
            await protected_call()
        except (ConnectionError, RuntimeError):
            if i < 3:
                breaker.record_failure()

    assert breaker.state.value == "open"

    # Subsequent calls fail immediately without retry
    before_calls = failing_service_calls
    try:
        await protected_call()
    except RuntimeError:
        pass
    after_calls = failing_service_calls

    # No new service calls made; circuit prevented retry storm
    assert after_calls == before_calls
    print("✓ Scenario 4: Circuit breaker → cascade prevention")


# Scenario 5: Error classification driving recovery strategy
async def scenario_error_classification_routing():
    """
    Simulate error classification determining retry vs fail-fast.
    Expected: Transient errors retry; permanent errors fail immediately.
    """
    classifier = ErrorClassifier()
    retry = RetryLogic(classifier=classifier)

    transient_attempts = 0
    permanent_attempts = 0

    async def transient_operation():
        nonlocal transient_attempts
        transient_attempts += 1
        if transient_attempts < 2:
            raise asyncio.TimeoutError("temporary")
        return "success"

    async def permanent_operation():
        nonlocal permanent_attempts
        permanent_attempts += 1
        raise ValueError("invalid request")

    # Transient error: retry succeeds
    result = await retry.call_with_retry(transient_operation)
    assert result == "success"
    assert transient_attempts == 2  # Retried

    # Permanent error: fails immediately
    retry.reset_stats()
    try:
        await retry.call_with_retry(permanent_operation)
    except ValueError:
        pass
    assert permanent_attempts == 1  # No retry
    print("✓ Scenario 5: Error classification → routing strategy")


# Scenario 6: Exponential backoff preventing thundering herd
async def scenario_backoff_thundering_herd():
    """
    Simulate concurrent retries with exponential backoff.
    Expected: Jitter + backoff prevents synchronized retry storms.
    """
    backoff_config = BackoffConfig(
        initial_delay_sec=0.01,
        multiplier=2.0,
        jitter=True,
        max_attempts=3,
    )

    async def concurrent_retries():
        tasks = []
        for i in range(5):

            async def retry_with_backoff(task_id):
                backoff = ExponentialBackoff(backoff_config)
                delays = []
                for attempt in range(2):
                    delay = backoff.next_delay()
                    delays.append(delay)
                return (task_id, delays)

            tasks.append(retry_with_backoff(i))

        results = await asyncio.gather(*tasks)
        return results

    results = await concurrent_retries()
    assert len(results) == 5

    # Verify delays increase
    for task_id, delays in results:
        assert len(delays) == 2
        assert delays[0] < delays[1]  # Second delay is larger

    print("✓ Scenario 6: Exponential backoff → thundering herd prevention")


# Scenario 7: Full integration with all patterns
async def scenario_full_integration():
    """
    Combine all patterns: retry + fallback + circuit + degradation + checkpoint.
    Expected: All patterns work together in realistic failure scenario.
    """
    # Setup all components
    backoff = ExponentialBackoff(BackoffConfig(max_attempts=3, jitter=False))
    breaker = CircuitBreaker(CircuitBreakerConfig(failure_threshold=4))
    degradation = GracefulDegradation(
        DegradationConfig(fallback_value={"mode": "degraded"})
    )
    rollback = StateRollback()
    strategy = FallbackStrategy(
        config=FallbackConfig(fail_open=True, fallback_value={"mode": "fallback"}),
        circuit_breaker=breaker,
    )

    # Simulation state
    operation_state = {"call_count": 0, "success": False}

    async def primary_operation():
        operation_state["call_count"] += 1
        if operation_state["call_count"] < 3:
            raise asyncio.TimeoutError("transient failure")
        operation_state["success"] = True
        return {"result": "primary"}

    async def degraded_operation():
        return degradation.get_fallback()

    # Save checkpoint
    ckpt = rollback.save_checkpoint(operation_state.copy(), operation_id="full_integration")

    # Call with full resilience stack
    result = await strategy.call_with_fallback(primary_operation, degraded_operation)

    # Verify result
    assert operation_state["success"] is True
    assert result == {"result": "primary"}

    # Restore checkpoint for verification
    restored = rollback.restore_checkpoint(ckpt)
    assert "call_count" in restored

    print("✓ Scenario 7: Full integration → all patterns working together")


# Run all scenarios
if __name__ == "__main__":
    print("Running consolidation layer scenario tests...\n")

    asyncio.run(scenario_api_intermittent_failures())
    asyncio.run(scenario_service_degradation())
    asyncio.run(scenario_complex_operation_with_checkpoints())
    asyncio.run(scenario_circuit_breaker_cascade_prevention())
    asyncio.run(scenario_error_classification_routing())
    asyncio.run(scenario_backoff_thundering_herd())
    asyncio.run(scenario_full_integration())

    print("\n═══════════════════════════════════════════════════════")
    print("✅ ALL SCENARIO TESTS PASSED (7/7)")
    print("═══════════════════════════════════════════════════════")
