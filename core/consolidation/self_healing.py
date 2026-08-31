"""
Self-healing patterns: exponential backoff, circuit breaker, graceful degradation.

Layer 1 (tactical): Composable, single-concern patterns.
Integrates with HealthMonitor for observability.
"""

from dataclasses import dataclass, field
from typing import Optional, Callable, Any, Dict
from enum import Enum
import asyncio
import time
from datetime import datetime, timedelta


class CircuitState(Enum):
    """Circuit breaker state machine."""
    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing, reject calls
    HALF_OPEN = "half_open"  # Testing recovery


@dataclass(frozen=True)
class BackoffConfig:
    """Exponential backoff configuration."""
    initial_delay_sec: float = 0.1
    multiplier: float = 2.0
    max_delay_sec: float = 30.0
    max_attempts: int = 5
    jitter: bool = True


class ExponentialBackoff:
    """
    Exponential backoff with optional jitter.

    Usage:
        backoff = ExponentialBackoff(config)
        delay = backoff.next_delay()
        await asyncio.sleep(delay)
    """

    def __init__(self, config: Optional[BackoffConfig] = None):
        """Initialize backoff strategy."""
        self.config = config or BackoffConfig()
        self.attempt = 0

    def next_delay(self) -> float:
        """
        Get next delay in seconds, respecting max attempts.

        Returns:
            Delay in seconds. Raises ValueError if max_attempts exceeded.
        """
        if self.attempt >= self.config.max_attempts:
            raise ValueError(f"Max attempts {self.config.max_attempts} exceeded")

        delay = min(
            self.config.initial_delay_sec * (self.config.multiplier ** self.attempt),
            self.config.max_delay_sec,
        )

        if self.config.jitter:
            # Add ±10% jitter to prevent thundering herd
            jitter_amount = delay * 0.1
            delay += (asyncio.get_event_loop().time() % jitter_amount) - (
                jitter_amount / 2
            )
            delay = max(0.001, delay)  # Never negative

        self.attempt += 1
        return delay

    def reset(self) -> None:
        """Reset attempt counter."""
        self.attempt = 0

    def is_exhausted(self) -> bool:
        """Check if max attempts reached."""
        return self.attempt >= self.config.max_attempts


@dataclass(frozen=True)
class CircuitBreakerConfig:
    """Circuit breaker configuration."""
    failure_threshold: int = 5
    success_threshold: int = 2  # Needed in HALF_OPEN to close
    timeout_sec: float = 60.0  # OPEN → HALF_OPEN after timeout
    name: str = "circuit_breaker"


class CircuitBreaker:
    """
    Circuit breaker: fail-fast pattern to prevent cascading failures.

    State machine: CLOSED → OPEN → HALF_OPEN → CLOSED

    Usage:
        breaker = CircuitBreaker(config)
        if breaker.is_closed():
            result = await call_protected_operation()
            if success:
                breaker.record_success()
            else:
                breaker.record_failure()
    """

    def __init__(self, config: Optional[CircuitBreakerConfig] = None):
        """Initialize circuit breaker."""
        self.config = config or CircuitBreakerConfig()
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time: Optional[datetime] = None

    def is_closed(self) -> bool:
        """Check if circuit is CLOSED (calls allowed)."""
        if self.state == CircuitState.CLOSED:
            return True

        if self.state == CircuitState.OPEN:
            # Check timeout; transition to HALF_OPEN
            if self.last_failure_time:
                elapsed = (datetime.utcnow() - self.last_failure_time).total_seconds()
                if elapsed >= self.config.timeout_sec:
                    self.state = CircuitState.HALF_OPEN
                    self.success_count = 0
                    return True  # Allow one test call
            return False

        # HALF_OPEN: only special handling in record_success
        return True

    def record_success(self) -> None:
        """Record successful call."""
        if self.state == CircuitState.HALF_OPEN:
            self.success_count += 1
            if self.success_count >= self.config.success_threshold:
                self.state = CircuitState.CLOSED
                self.failure_count = 0
                self.success_count = 0
        elif self.state == CircuitState.CLOSED:
            self.failure_count = 0

    def record_failure(self) -> None:
        """Record failed call."""
        self.failure_count += 1
        self.last_failure_time = datetime.utcnow()

        if self.state == CircuitState.CLOSED:
            if self.failure_count >= self.config.failure_threshold:
                self.state = CircuitState.OPEN
        elif self.state == CircuitState.HALF_OPEN:
            self.state = CircuitState.OPEN
            self.success_count = 0

    def get_state(self) -> CircuitState:
        """Get current state."""
        return self.state


@dataclass(frozen=True)
class DegradationConfig:
    """Graceful degradation configuration."""
    enabled: bool = True
    fallback_value: Any = None
    degrade_message: str = "Service degraded, using cached/reduced response"


class GracefulDegradation:
    """
    Serve reduced functionality on error (e.g., stale cache, reduced feature set).

    Usage:
        degradation = GracefulDegradation(config)
        try:
            result = await expensive_operation()
            degradation.mark_success()
        except Exception:
            if degradation.should_degrade():
                result = degradation.get_fallback()
            else:
                raise
    """

    def __init__(self, config: Optional[DegradationConfig] = None):
        """Initialize degradation strategy."""
        self.config = config or DegradationConfig()
        self.in_degraded_mode = False
        self.degraded_since: Optional[datetime] = None

    def mark_success(self) -> None:
        """Mark operation successful; exit degradation mode."""
        self.in_degraded_mode = False
        self.degraded_since = None

    def mark_failure(self) -> None:
        """Mark operation failed; enter degradation mode."""
        if not self.in_degraded_mode:
            self.degraded_since = datetime.utcnow()
        self.in_degraded_mode = True

    def should_degrade(self) -> bool:
        """Check if should serve degraded response."""
        return self.config.enabled and self.in_degraded_mode

    def get_fallback(self) -> Any:
        """Get degraded/fallback response."""
        return self.config.fallback_value

    def get_status(self) -> Dict[str, Any]:
        """Get degradation status."""
        return {
            "in_degraded_mode": self.in_degraded_mode,
            "degraded_since": self.degraded_since.isoformat() if self.degraded_since else None,
            "message": self.config.degrade_message if self.in_degraded_mode else "",
        }


# Optional: HealthMonitor integration helper
async def report_recovery_attempt(
    health_monitor: Optional[Any],
    subsystem_id: str,
    recovery_type: str,
    success: bool,
    details: str = "",
) -> None:
    """
    Report recovery attempt to HealthMonitor (if available).

    Args:
        health_monitor: HealthMonitor instance (optional)
        subsystem_id: Subsystem name
        recovery_type: "backoff", "circuit_break", or "degrade"
        success: True if recovery succeeded
        details: Additional context
    """
    if not health_monitor:
        return

    # Simplified logging; in production, integrate with actual HealthMonitor API
    message = f"Recovery ({recovery_type}): {success} - {details}"
    # await health_monitor.report_event(subsystem_id, recovery_type, message)
