"""
Unit tests for self-healing patterns: backoff, circuit breaker, degradation.
"""

import pytest
import asyncio
from datetime import datetime, timedelta

from core.consolidation.self_healing import (
    ExponentialBackoff,
    BackoffConfig,
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitState,
    GracefulDegradation,
    DegradationConfig,
)


class TestExponentialBackoff:
    """ExponentialBackoff pattern tests."""

    def test_backoff_init_default(self):
        """Test default initialization."""
        backoff = ExponentialBackoff()
        assert backoff.attempt == 0
        assert backoff.config.initial_delay_sec == 0.1
        assert backoff.config.multiplier == 2.0

    def test_backoff_next_delay_increases(self):
        """Test delay increases exponentially."""
        config = BackoffConfig(initial_delay_sec=1.0, multiplier=2.0, jitter=False)
        backoff = ExponentialBackoff(config)

        delays = []
        for _ in range(4):
            delays.append(backoff.next_delay())

        assert delays[0] == 1.0
        assert delays[1] == 2.0
        assert delays[2] == 4.0
        assert delays[3] == 8.0

    def test_backoff_max_delay_capped(self):
        """Test max_delay_sec cap."""
        config = BackoffConfig(
            initial_delay_sec=1.0,
            multiplier=2.0,
            max_delay_sec=5.0,
            jitter=False,
        )
        backoff = ExponentialBackoff(config)

        for _ in range(5):
            delay = backoff.next_delay()
            assert delay <= 5.0

    def test_backoff_max_attempts_exceeded(self):
        """Test ValueError when max_attempts exceeded."""
        config = BackoffConfig(max_attempts=2, jitter=False)
        backoff = ExponentialBackoff(config)

        backoff.next_delay()
        backoff.next_delay()

        with pytest.raises(ValueError, match="Max attempts"):
            backoff.next_delay()

    def test_backoff_reset(self):
        """Test reset() resets attempt counter."""
        config = BackoffConfig(jitter=False)
        backoff = ExponentialBackoff(config)

        backoff.next_delay()
        backoff.reset()
        assert backoff.attempt == 0

    def test_backoff_is_exhausted(self):
        """Test is_exhausted() checks limit."""
        config = BackoffConfig(max_attempts=2, jitter=False)
        backoff = ExponentialBackoff(config)

        assert not backoff.is_exhausted()
        backoff.next_delay()
        assert not backoff.is_exhausted()
        backoff.next_delay()
        assert backoff.is_exhausted()


class TestCircuitBreaker:
    """CircuitBreaker pattern tests."""

    def test_circuit_breaker_init_closed(self):
        """Test initial state is CLOSED."""
        breaker = CircuitBreaker()
        assert breaker.state == CircuitState.CLOSED
        assert breaker.is_closed()

    def test_circuit_breaker_closed_to_open(self):
        """Test CLOSED → OPEN on failure threshold."""
        config = CircuitBreakerConfig(failure_threshold=3)
        breaker = CircuitBreaker(config)

        for _ in range(2):
            breaker.record_failure()
        assert breaker.state == CircuitState.CLOSED

        breaker.record_failure()
        assert breaker.state == CircuitState.OPEN
        assert not breaker.is_closed()

    def test_circuit_breaker_open_rejects_calls(self):
        """Test OPEN state rejects calls."""
        config = CircuitBreakerConfig(failure_threshold=1)
        breaker = CircuitBreaker(config)

        breaker.record_failure()
        assert breaker.state == CircuitState.OPEN
        assert not breaker.is_closed()

    def test_circuit_breaker_open_to_half_open(self):
        """Test OPEN → HALF_OPEN after timeout."""
        config = CircuitBreakerConfig(failure_threshold=1, timeout_sec=0.1)
        breaker = CircuitBreaker(config)

        breaker.record_failure()
        assert breaker.state == CircuitState.OPEN

        asyncio.run(asyncio.sleep(0.15))

        # is_closed() should transition to HALF_OPEN
        assert breaker.is_closed()
        assert breaker.state == CircuitState.HALF_OPEN

    def test_circuit_breaker_half_open_to_closed(self):
        """Test HALF_OPEN → CLOSED on success threshold."""
        config = CircuitBreakerConfig(
            failure_threshold=1,
            success_threshold=2,
            timeout_sec=0.1,
        )
        breaker = CircuitBreaker(config)

        breaker.record_failure()
        assert breaker.state == CircuitState.OPEN

        asyncio.run(asyncio.sleep(0.15))
        assert breaker.is_closed()  # Now HALF_OPEN

        breaker.record_success()
        assert breaker.state == CircuitState.HALF_OPEN

        breaker.record_success()
        assert breaker.state == CircuitState.CLOSED

    def test_circuit_breaker_half_open_to_open_on_failure(self):
        """Test HALF_OPEN → OPEN on failure."""
        config = CircuitBreakerConfig(failure_threshold=1, timeout_sec=0.1)
        breaker = CircuitBreaker(config)

        breaker.record_failure()
        assert breaker.state == CircuitState.OPEN

        asyncio.run(asyncio.sleep(0.15))
        assert breaker.is_closed()  # Now HALF_OPEN

        breaker.record_failure()
        assert breaker.state == CircuitState.OPEN

    def test_circuit_breaker_success_in_closed_resets_failures(self):
        """Test success in CLOSED resets failure count."""
        breaker = CircuitBreaker()

        breaker.record_failure()
        breaker.record_failure()
        breaker.record_success()

        assert breaker.failure_count == 0

    def test_circuit_breaker_get_state(self):
        """Test get_state() method."""
        breaker = CircuitBreaker()
        assert breaker.get_state() == CircuitState.CLOSED

        config = CircuitBreakerConfig(failure_threshold=1)
        breaker = CircuitBreaker(config)
        breaker.record_failure()
        assert breaker.get_state() == CircuitState.OPEN


class TestGracefulDegradation:
    """GracefulDegradation pattern tests."""

    def test_degradation_init_normal_mode(self):
        """Test initial state is normal (not degraded)."""
        degradation = GracefulDegradation()
        assert not degradation.in_degraded_mode
        assert not degradation.should_degrade()

    def test_degradation_mark_failure_enters_degraded_mode(self):
        """Test mark_failure() enters degraded mode."""
        degradation = GracefulDegradation()
        degradation.mark_failure()

        assert degradation.in_degraded_mode
        assert degradation.should_degrade()

    def test_degradation_mark_success_exits_degraded_mode(self):
        """Test mark_success() exits degraded mode."""
        degradation = GracefulDegradation()
        degradation.mark_failure()
        assert degradation.in_degraded_mode

        degradation.mark_success()
        assert not degradation.in_degraded_mode
        assert not degradation.should_degrade()

    def test_degradation_get_fallback_returns_configured_value(self):
        """Test get_fallback() returns configured fallback value."""
        config = DegradationConfig(fallback_value={"cached": True})
        degradation = GracefulDegradation(config)

        assert degradation.get_fallback() == {"cached": True}

    def test_degradation_disabled_never_degrades(self):
        """Test disabled degradation never enters degraded mode."""
        config = DegradationConfig(enabled=False)
        degradation = GracefulDegradation(config)

        degradation.mark_failure()
        assert degradation.in_degraded_mode  # Internal state changed
        assert not degradation.should_degrade()  # But should_degrade() respects enabled flag

    def test_degradation_get_status(self):
        """Test get_status() returns degradation details."""
        degradation = GracefulDegradation()
        status = degradation.get_status()

        assert status["in_degraded_mode"] is False
        assert status["degraded_since"] is None
        assert status["message"] == ""

        degradation.mark_failure()
        status = degradation.get_status()

        assert status["in_degraded_mode"] is True
        assert status["degraded_since"] is not None
        assert "Service degraded" in status["message"]

    def test_degradation_timestamp_tracked(self):
        """Test degraded_since timestamp is tracked."""
        degradation = GracefulDegradation()
        before = datetime.utcnow()

        degradation.mark_failure()

        after = datetime.utcnow()
        assert before <= degradation.degraded_since <= after
