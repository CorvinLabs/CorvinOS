"""
Error recovery orchestration: retry logic, fallback strategies, state rollback.

Layer 2 (strategic): Orchestrates Layer 1 tactical patterns.
Composes BackoffRetry, FallbackStrategy, StateRollback.
"""

from dataclasses import dataclass, field
from typing import Optional, Callable, Any, Awaitable, Dict, List
from enum import Enum
import asyncio
from datetime import datetime

from .self_healing import (
    ExponentialBackoff,
    BackoffConfig,
    CircuitBreaker,
    CircuitBreakerConfig,
)


class ErrorClass(Enum):
    """Error classification for recovery strategy selection."""
    TRANSIENT = "transient"  # e.g., timeout, rate limit → retry
    PERMANENT = "permanent"  # e.g., not found, auth failed → don't retry
    UNKNOWN = "unknown"  # Treat as transient by default


@dataclass(frozen=True)
class ErrorClassifier:
    """Classify errors into recovery buckets."""
    transient_exceptions: tuple = (
        asyncio.TimeoutError,
        ConnectionError,
        TimeoutError,
    )
    permanent_exceptions: tuple = (
        ValueError,
        KeyError,
        AttributeError,
    )

    def classify(self, exc: Exception) -> ErrorClass:
        """Classify exception into recovery bucket."""
        if isinstance(exc, self.transient_exceptions):
            return ErrorClass.TRANSIENT
        if isinstance(exc, self.permanent_exceptions):
            return ErrorClass.PERMANENT
        return ErrorClass.UNKNOWN


@dataclass(frozen=True)
class Checkpoint:
    """Immutable checkpoint for state rollback."""
    timestamp: datetime = field(default_factory=datetime.utcnow)
    state_snapshot: Dict[str, Any] = field(default_factory=dict)
    operation_id: str = ""

    def __hash__(self) -> int:
        """Make checkpoint hashable."""
        return hash((self.timestamp, self.operation_id))


class StateRollback:
    """
    Checkpoint/restore mechanism for idempotent operations.

    Usage:
        rollback = StateRollback()
        ckpt = rollback.save_checkpoint(state_dict)
        try:
            state = await operation()
            rollback.commit_checkpoint(ckpt)
        except Exception:
            rollback.restore_checkpoint(ckpt)
            raise
    """

    def __init__(self, max_checkpoints: int = 10):
        """Initialize state rollback."""
        self.max_checkpoints = max_checkpoints
        self.checkpoints: Dict[str, Checkpoint] = {}
        self.committed: set = set()

    def save_checkpoint(self, state: Dict[str, Any], operation_id: str = "") -> Checkpoint:
        """Save checkpoint of current state."""
        ckpt = Checkpoint(state_snapshot=state.copy(), operation_id=operation_id)
        self.checkpoints[operation_id] = ckpt

        # Evict oldest if over limit
        if len(self.checkpoints) > self.max_checkpoints:
            oldest_id = min(
                self.checkpoints.keys(),
                key=lambda k: self.checkpoints[k].timestamp,
            )
            del self.checkpoints[oldest_id]

        return ckpt

    def restore_checkpoint(self, ckpt: Checkpoint) -> Dict[str, Any]:
        """Restore state from checkpoint."""
        if ckpt.operation_id not in self.checkpoints:
            raise ValueError(f"Checkpoint {ckpt.operation_id} not found")
        return self.checkpoints[ckpt.operation_id].state_snapshot.copy()

    def commit_checkpoint(self, ckpt: Checkpoint) -> None:
        """Mark checkpoint as committed (no longer needed for rollback)."""
        self.committed.add(ckpt.operation_id)

    def get_checkpoint_count(self) -> int:
        """Get current checkpoint count."""
        return len(self.checkpoints)


@dataclass(frozen=True)
class FallbackConfig:
    """Fallback strategy configuration."""
    name: str = "fallback"
    fail_open: bool = True  # True: degrade on error; False: propagate error
    fallback_value: Any = None
    max_attempts: int = 3


class FallbackStrategy:
    """
    Primary → fallback path selection with circuit breaker awareness.

    Usage:
        strategy = FallbackStrategy(config, circuit_breaker)
        result = await strategy.call_with_fallback(
            primary=primary_op,
            fallback=fallback_op,
        )
    """

    def __init__(
        self,
        config: Optional[FallbackConfig] = None,
        circuit_breaker: Optional[CircuitBreaker] = None,
    ):
        """Initialize fallback strategy."""
        self.config = config or FallbackConfig()
        self.circuit_breaker = circuit_breaker or CircuitBreaker()
        self.classifier = ErrorClassifier()
        self.backoff = ExponentialBackoff(
            BackoffConfig(max_attempts=self.config.max_attempts)
        )
        self.call_count = 0
        self.fallback_count = 0

    async def call_with_fallback(
        self,
        primary: Callable[[], Awaitable[Any]],
        fallback: Optional[Callable[[], Awaitable[Any]]] = None,
    ) -> Any:
        """
        Call primary; fall back to fallback on failure.

        Args:
            primary: Primary async operation
            fallback: Fallback async operation (optional)

        Returns:
            Result from primary or fallback, or fallback_value if configured.

        Raises:
            Exception if circuit is open or fail_open=False
        """
        self.call_count += 1

        # Check circuit breaker
        if not self.circuit_breaker.is_closed():
            if self.config.fail_open and fallback:
                self.fallback_count += 1
                return await fallback()
            raise RuntimeError("Circuit breaker is open")

        # Try primary with backoff
        last_exc = None
        self.backoff.reset()

        while not self.backoff.is_exhausted():
            try:
                result = await primary()
                self.circuit_breaker.record_success()
                return result
            except Exception as exc:
                last_exc = exc
                error_class = self.classifier.classify(exc)

                if error_class == ErrorClass.PERMANENT:
                    self.circuit_breaker.record_failure()
                    raise

                # Transient: retry with backoff
                self.circuit_breaker.record_failure()
                delay = self.backoff.next_delay()
                await asyncio.sleep(delay)

        # Exhausted retries; try fallback
        if fallback:
            self.fallback_count += 1
            return await fallback()

        if self.config.fail_open:
            return self.config.fallback_value

        raise last_exc or RuntimeError("Max retries exhausted")

    def get_stats(self) -> Dict[str, Any]:
        """Get call statistics."""
        return {
            "total_calls": self.call_count,
            "fallback_invocations": self.fallback_count,
            "fallback_rate": (
                self.fallback_count / self.call_count
                if self.call_count > 0
                else 0.0
            ),
        }


class RetryLogic:
    """
    Retry wrapper with exponential backoff + jitter.

    Usage:
        retry = RetryLogic()
        result = await retry.call_with_retry(operation)
    """

    def __init__(
        self,
        config: Optional[BackoffConfig] = None,
        classifier: Optional[ErrorClassifier] = None,
    ):
        """Initialize retry logic."""
        self.config = config or BackoffConfig()
        self.classifier = classifier or ErrorClassifier()
        self.backoff = ExponentialBackoff(self.config)
        self.attempt_count = 0
        self.success_count = 0
        self.failure_count = 0

    async def call_with_retry(
        self,
        operation: Callable[[], Awaitable[Any]],
    ) -> Any:
        """
        Call operation with retry logic.

        Args:
            operation: Async operation to retry

        Returns:
            Result from operation

        Raises:
            Exception if max retries exhausted
        """
        self.backoff.reset()
        last_exc = None

        while not self.backoff.is_exhausted():
            try:
                self.attempt_count += 1
                result = await operation()
                self.success_count += 1
                return result
            except Exception as exc:
                self.failure_count += 1
                last_exc = exc
                error_class = self.classifier.classify(exc)

                # Don't retry permanent errors
                if error_class == ErrorClass.PERMANENT:
                    raise

                # Transient: sleep and retry
                delay = self.backoff.next_delay()
                await asyncio.sleep(delay)

        raise last_exc or RuntimeError("Max retries exhausted")

    def get_stats(self) -> Dict[str, Any]:
        """Get retry statistics."""
        return {
            "total_attempts": self.attempt_count,
            "successes": self.success_count,
            "failures": self.failure_count,
            "success_rate": (
                self.success_count / self.attempt_count
                if self.attempt_count > 0
                else 0.0
            ),
        }

    def reset_stats(self) -> None:
        """Reset statistics counters."""
        self.attempt_count = 0
        self.success_count = 0
        self.failure_count = 0
