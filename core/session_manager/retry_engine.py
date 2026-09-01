"""Phase 2: Retry Logic + Multi-Split (ADR-0472 Phase 2).

Handles transient failures, multi-branch splits, state persistence.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional, List
import logging

logger = logging.getLogger(__name__)


class ErrorClassification(Enum):
    """Error categories for retry decision."""
    TRANSIENT = "transient"      # Retry (network timeout, temp resource)
    TERMINAL = "terminal"        # Fail (wrong goal, invalid input)
    CAPACITY = "capacity"        # Backpressure (queue full)


@dataclass
class RetryPolicy:
    """Retry strategy configuration."""
    max_attempts: int = 3
    backoff_base_sec: float = 1.0
    backoff_max_sec: float = 60.0
    jitter: bool = True


class RetryEngine:
    """Phase 2: Retry logic for task splits (ADR-0472 Phase 2)."""

    def __init__(self, policy: RetryPolicy = None):
        self.policy = policy or RetryPolicy()
        self.attempt_log: dict[str, List[dict]] = {}

    async def classify_error(self, error: Exception) -> ErrorClassification:
        """Classify if error is retryable (Phase 2)."""
        # TODO: Implement error classification logic
        # For now: all non-terminal errors are transient
        return ErrorClassification.TRANSIENT

    async def should_retry(
        self,
        task_id: str,
        split_id: str,
        attempt: int,
        error: Exception,
    ) -> bool:
        """Decide if split should be retried (Phase 2)."""
        if attempt >= self.policy.max_attempts:
            return False

        classification = await self.classify_error(error)
        return classification == ErrorClassification.TRANSIENT

    async def get_backoff_delay(self, attempt: int) -> float:
        """Calculate exponential backoff with jitter (Phase 2)."""
        # TODO: Implement EMA-based backoff
        delay = min(
            self.policy.backoff_base_sec * (2 ** attempt),
            self.policy.backoff_max_sec,
        )
        return delay
