"""
Unit tests for error recovery: retry logic, fallback strategies, state rollback.
"""

import pytest
import asyncio
from datetime import datetime

from core.consolidation.error_recovery import (
    ErrorClass,
    ErrorClassifier,
    Checkpoint,
    StateRollback,
    FallbackStrategy,
    FallbackConfig,
    RetryLogic,
    BackoffConfig,
)
from core.consolidation.self_healing import CircuitBreaker, CircuitBreakerConfig


class TestErrorClassifier:
    """ErrorClassifier pattern tests."""

    def test_classifier_transient_timeout(self):
        """Test TimeoutError classified as transient."""
        classifier = ErrorClassifier()
        exc = asyncio.TimeoutError("timeout")
        assert classifier.classify(exc) == ErrorClass.TRANSIENT

    def test_classifier_transient_connection(self):
        """Test ConnectionError classified as transient."""
        classifier = ErrorClassifier()
        exc = ConnectionError("network error")
        assert classifier.classify(exc) == ErrorClass.TRANSIENT

    def test_classifier_permanent_value_error(self):
        """Test ValueError classified as permanent."""
        classifier = ErrorClassifier()
        exc = ValueError("bad value")
        assert classifier.classify(exc) == ErrorClass.PERMANENT

    def test_classifier_unknown_custom_error(self):
        """Test custom exception classified as unknown."""
        class CustomError(Exception):
            pass

        classifier = ErrorClassifier()
        exc = CustomError("custom")
        assert classifier.classify(exc) == ErrorClass.UNKNOWN


class TestCheckpoint:
    """Checkpoint immutability tests."""

    def test_checkpoint_frozen(self):
        """Test Checkpoint is frozen (immutable)."""
        ckpt = Checkpoint(state_snapshot={"key": "value"})

        with pytest.raises(AttributeError):
            ckpt.state_snapshot = {"new": "state"}

    def test_checkpoint_hashable(self):
        """Test Checkpoint is hashable."""
        ckpt1 = Checkpoint(state_snapshot={"a": 1}, operation_id="op1")
        ckpt2 = Checkpoint(state_snapshot={"a": 1}, operation_id="op1")

        # Both should be hashable
        s = {ckpt1, ckpt2}
        assert len(s) == 1  # Same hash due to same timestamp+operation_id


class TestStateRollback:
    """StateRollback pattern tests."""

    def test_state_rollback_save_restore(self):
        """Test save and restore checkpoint."""
        rollback = StateRollback()
        original_state = {"key": "value", "counter": 42}

        ckpt = rollback.save_checkpoint(original_state, operation_id="op1")

        # Modify original (to ensure copy was made)
        original_state["counter"] = 100

        restored = rollback.restore_checkpoint(ckpt)
        assert restored == {"key": "value", "counter": 42}

    def test_state_rollback_restore_missing_raises_error(self):
        """Test restore non-existent checkpoint raises error."""
        rollback = StateRollback()
        ckpt = Checkpoint(state_snapshot={}, operation_id="missing")

        with pytest.raises(ValueError, match="not found"):
            rollback.restore_checkpoint(ckpt)

    def test_state_rollback_commit_checkpoint(self):
        """Test commit_checkpoint() marks checkpoint committed."""
        rollback = StateRollback()
        state = {"a": 1}
        ckpt = rollback.save_checkpoint(state, operation_id="op1")

        rollback.commit_checkpoint(ckpt)
        assert "op1" in rollback.committed

    def test_state_rollback_evicts_oldest_on_limit(self):
        """Test oldest checkpoint evicted when over max."""
        rollback = StateRollback(max_checkpoints=2)

        ckpt1 = rollback.save_checkpoint({"a": 1}, operation_id="op1")
        asyncio.run(asyncio.sleep(0.01))
        ckpt2 = rollback.save_checkpoint({"b": 2}, operation_id="op2")
        asyncio.run(asyncio.sleep(0.01))
        ckpt3 = rollback.save_checkpoint({"c": 3}, operation_id="op3")

        # op1 should be evicted
        assert len(rollback.checkpoints) == 2
        assert "op1" not in rollback.checkpoints

        with pytest.raises(ValueError):
            rollback.restore_checkpoint(ckpt1)

    def test_state_rollback_get_checkpoint_count(self):
        """Test get_checkpoint_count()."""
        rollback = StateRollback()
        assert rollback.get_checkpoint_count() == 0

        rollback.save_checkpoint({}, operation_id="op1")
        assert rollback.get_checkpoint_count() == 1

        rollback.save_checkpoint({}, operation_id="op2")
        assert rollback.get_checkpoint_count() == 2


class TestRetryLogic:
    """RetryLogic pattern tests."""

    @pytest.mark.asyncio
    async def test_retry_success_on_first_attempt(self):
        """Test successful operation on first attempt."""
        retry = RetryLogic()

        async def operation():
            return "success"

        result = await retry.call_with_retry(operation)
        assert result == "success"
        assert retry.success_count == 1
        assert retry.failure_count == 0

    @pytest.mark.asyncio
    async def test_retry_success_after_transient_failures(self):
        """Test success after transient failures."""
        retry = RetryLogic()
        attempt_count = 0

        async def operation():
            nonlocal attempt_count
            attempt_count += 1
            if attempt_count < 3:
                raise asyncio.TimeoutError("timeout")
            return "success"

        result = await retry.call_with_retry(operation)
        assert result == "success"
        assert retry.success_count == 1

    @pytest.mark.asyncio
    async def test_retry_permanent_error_not_retried(self):
        """Test permanent error fails immediately."""
        retry = RetryLogic()
        attempt_count = 0

        async def operation():
            nonlocal attempt_count
            attempt_count += 1
            raise ValueError("permanent error")

        with pytest.raises(ValueError, match="permanent error"):
            await retry.call_with_retry(operation)

        assert attempt_count == 1  # No retries
        assert retry.failure_count == 1

    @pytest.mark.asyncio
    async def test_retry_max_attempts_exhausted(self):
        """Test max retries exhausted."""
        config = BackoffConfig(max_attempts=2, jitter=False)
        retry = RetryLogic(config)

        async def operation():
            raise asyncio.TimeoutError("timeout")

        with pytest.raises(asyncio.TimeoutError):
            await retry.call_with_retry(operation)

        assert retry.failure_count == 2

    def test_retry_get_stats(self):
        """Test get_stats() returns statistics."""
        retry = RetryLogic()
        retry.attempt_count = 10
        retry.success_count = 8
        retry.failure_count = 2

        stats = retry.get_stats()
        assert stats["total_attempts"] == 10
        assert stats["successes"] == 8
        assert stats["failures"] == 2
        assert stats["success_rate"] == 0.8

    def test_retry_reset_stats(self):
        """Test reset_stats() clears counters."""
        retry = RetryLogic()
        retry.attempt_count = 5
        retry.success_count = 3
        retry.failure_count = 2

        retry.reset_stats()

        assert retry.attempt_count == 0
        assert retry.success_count == 0
        assert retry.failure_count == 0


class TestFallbackStrategy:
    """FallbackStrategy pattern tests."""

    @pytest.mark.asyncio
    async def test_fallback_primary_success(self):
        """Test primary operation succeeds."""
        strategy = FallbackStrategy()

        async def primary():
            return "primary_result"

        async def fallback():
            return "fallback_result"

        result = await strategy.call_with_fallback(primary, fallback)
        assert result == "primary_result"

    @pytest.mark.asyncio
    async def test_fallback_uses_fallback_on_transient_exhaustion(self):
        """Test fallback called when primary retries exhausted."""
        config = FallbackConfig(max_attempts=2)
        strategy = FallbackStrategy(config)

        async def primary():
            raise asyncio.TimeoutError("timeout")

        async def fallback():
            return "fallback_result"

        result = await strategy.call_with_fallback(primary, fallback)
        assert result == "fallback_result"
        assert strategy.fallback_count == 1

    @pytest.mark.asyncio
    async def test_fallback_circuit_breaker_open(self):
        """Test circuit breaker prevents primary calls."""
        breaker_config = CircuitBreakerConfig(failure_threshold=1)
        breaker = CircuitBreaker(breaker_config)
        strategy = FallbackStrategy(config=None, circuit_breaker=breaker)

        breaker.record_failure()
        assert breaker.state.value == "open"

        async def primary():
            return "primary_result"

        async def fallback():
            return "fallback_result"

        # With fail_open=True, should use fallback
        result = await strategy.call_with_fallback(primary, fallback)
        assert result == "fallback_result"

    @pytest.mark.asyncio
    async def test_fallback_fail_open_returns_configured_value(self):
        """Test fail_open mode returns configured fallback_value."""
        config = FallbackConfig(fail_open=True, fallback_value="default_value")
        strategy = FallbackStrategy(config)

        async def primary():
            raise asyncio.TimeoutError("timeout")

        result = await strategy.call_with_fallback(primary)
        assert result == "default_value"

    @pytest.mark.asyncio
    async def test_fallback_fail_closed_raises_error(self):
        """Test fail_closed mode propagates error."""
        config = FallbackConfig(fail_open=False, max_attempts=1)
        strategy = FallbackStrategy(config)

        async def primary():
            raise asyncio.TimeoutError("timeout")

        with pytest.raises(asyncio.TimeoutError):
            await strategy.call_with_fallback(primary)

    def test_fallback_get_stats(self):
        """Test get_stats() returns fallback statistics."""
        strategy = FallbackStrategy()
        strategy.call_count = 10
        strategy.fallback_count = 3

        stats = strategy.get_stats()
        assert stats["total_calls"] == 10
        assert stats["fallback_invocations"] == 3
        assert stats["fallback_rate"] == 0.3

    @pytest.mark.asyncio
    async def test_fallback_permanent_error_raises_immediately(self):
        """Test permanent error raises immediately."""
        strategy = FallbackStrategy()

        async def primary():
            raise ValueError("bad value")

        async def fallback():
            return "fallback_result"

        with pytest.raises(ValueError, match="bad value"):
            await strategy.call_with_fallback(primary, fallback)

        # Fallback should not have been called
        assert strategy.fallback_count == 0

    @pytest.mark.asyncio
    async def test_fallback_no_fallback_provided(self):
        """Test with no fallback provided."""
        config = FallbackConfig(fail_open=True, fallback_value="default")
        strategy = FallbackStrategy(config)

        async def primary():
            raise asyncio.TimeoutError("timeout")

        result = await strategy.call_with_fallback(primary, fallback=None)
        assert result == "default"
