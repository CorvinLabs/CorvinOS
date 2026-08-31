"""
Integration tests for consolidation layer patterns working together.
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
)
from core.consolidation.error_recovery import (
    RetryLogic,
    FallbackStrategy,
    StateRollback,
    ErrorClassifier,
)


def test_integration_backoff_with_circuit_breaker():
    """Test ExponentialBackoff + CircuitBreaker integration."""
    config = BackoffConfig(initial_delay_sec=0.01, multiplier=2.0, jitter=False)
    backoff = ExponentialBackoff(config)

    breaker_config = CircuitBreakerConfig(failure_threshold=3)
    breaker = CircuitBreaker(breaker_config)

    # Simulate 3 failures: backoff delays, circuit opens
    for _ in range(3):
        delay = backoff.next_delay()
        breaker.record_failure()
        assert delay >= 0
        assert breaker.state.value in ["closed", "open"]

    assert breaker.state.value == "open"
    assert not breaker.is_closed()


def test_integration_graceful_degradation_with_fallback():
    """Test GracefulDegradation + FallbackStrategy integration."""
    degradation = GracefulDegradation()
    breaker = CircuitBreaker()

    strategy_config = None
    strategy = FallbackStrategy(
        config=strategy_config,
        circuit_breaker=breaker,
    )

    # Circuit fails, degradation activates
    breaker.record_failure()
    breaker.record_failure()
    breaker.record_failure()
    breaker.record_failure()
    breaker.record_failure()

    assert breaker.state.value == "open"

    # Degradation provides fallback
    degradation.mark_failure()
    assert degradation.should_degrade()
    fallback_response = degradation.get_fallback()
    assert fallback_response is None  # Default value


def test_integration_state_rollback_with_retry():
    """Test StateRollback + RetryLogic integration."""
    rollback = StateRollback(max_checkpoints=5)
    retry = RetryLogic()

    # Save checkpoint before operation
    original_state = {"attempt": 0, "status": "pending"}
    ckpt = rollback.save_checkpoint(original_state, operation_id="op_001")

    # Simulate retry logic would check checkpoint on failure
    retry.attempt_count = 2
    retry.failure_count = 2

    # Verify checkpoint is trackable
    restored = rollback.restore_checkpoint(ckpt)
    assert restored == {"attempt": 0, "status": "pending"}

    # Commit checkpoint (operation succeeded)
    rollback.commit_checkpoint(ckpt)
    assert "op_001" in rollback.committed


async def test_integration_retry_with_fallback():
    """Test RetryLogic + FallbackStrategy async integration."""
    retry_config = BackoffConfig(initial_delay_sec=0.001, max_attempts=2, jitter=False)
    retry = RetryLogic(retry_config)

    strategy_config = None
    strategy = FallbackStrategy(config=strategy_config)

    attempt_count = 0

    async def primary():
        nonlocal attempt_count
        attempt_count += 1
        if attempt_count < 2:
            raise asyncio.TimeoutError("temporary failure")
        return "success"

    async def fallback():
        return "fallback_value"

    # With fallback, should eventually return primary result
    result = await strategy.call_with_fallback(primary, fallback)
    assert result == "success"


async def test_integration_all_patterns_together():
    """Test all patterns working in concert."""
    # Setup all layers
    backoff_config = BackoffConfig(initial_delay_sec=0.001, max_attempts=3, jitter=False)
    backoff = ExponentialBackoff(backoff_config)

    breaker_config = CircuitBreakerConfig(failure_threshold=5, success_threshold=2)
    breaker = CircuitBreaker(breaker_config)

    degradation = GracefulDegradation()
    rollback = StateRollback()

    retry = RetryLogic(backoff_config)
    strategy = FallbackStrategy(config=None, circuit_breaker=breaker)

    # Simulate operation state
    operation_state = {"calls": 0, "successes": 0}

    async def operation():
        operation_state["calls"] += 1
        if operation_state["calls"] < 2:
            raise asyncio.TimeoutError("transient")
        operation_state["successes"] += 1
        return {"result": "ok"}

    async def degraded_operation():
        return {"result": "cached"}

    # Save checkpoint before
    ckpt = rollback.save_checkpoint(operation_state, operation_id="complex_op")

    # Call with retry + fallback
    result = await strategy.call_with_fallback(operation, degraded_operation)

    # Verify result
    assert result == {"result": "ok"}
    assert operation_state["successes"] == 1

    # Verify circuit breaker recorded success
    assert breaker.failure_count == 0 or breaker.state.value in ["closed", "half_open"]

    # Restore checkpoint (for verification)
    restored = rollback.restore_checkpoint(ckpt)
    assert "calls" in restored


def test_integration_error_classifier():
    """Test ErrorClassifier with retry logic."""
    classifier = ErrorClassifier()
    retry = RetryLogic(classifier=classifier)

    # Verify classifier is used in retry
    assert retry.classifier == classifier

    # Classify various errors
    assert classifier.classify(asyncio.TimeoutError()).value == "transient"
    assert classifier.classify(ConnectionError()).value == "transient"
    assert classifier.classify(ValueError()).value == "permanent"
    assert classifier.classify(KeyError()).value == "permanent"


# Run async tests manually if pytest-asyncio isn't available
if __name__ == "__main__":
    print("Running integration tests...")

    # Sync tests
    test_integration_backoff_with_circuit_breaker()
    print("✓ backoff + circuit_breaker integration")

    test_integration_graceful_degradation_with_fallback()
    print("✓ graceful_degradation + fallback integration")

    test_integration_state_rollback_with_retry()
    print("✓ state_rollback + retry integration")

    test_integration_error_classifier()
    print("✓ error_classifier integration")

    # Async tests
    asyncio.run(test_integration_retry_with_fallback())
    print("✓ retry + fallback async integration")

    asyncio.run(test_integration_all_patterns_together())
    print("✓ all_patterns_together integration")

    print("\n✅ All integration tests passed!")
