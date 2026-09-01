"""Phase 2: Retry Logic + Multi-Split (ADR-0472 Phase 2).

Handles transient failures, multi-branch splits, state persistence.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List, Dict, Any
import logging
import json
from pathlib import Path
from datetime import datetime

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


@dataclass
class RetryAttempt:
    """Record of a single retry attempt."""
    task_id: str
    split_id: str
    attempt: int
    error_type: str
    error_msg: str
    timestamp: float
    backoff_delay_sec: float
    success: bool = False


class RetryEngine:
    """Phase 2: Retry logic + persistence (ADR-0472 Phase 2)."""

    def __init__(
        self,
        policy: RetryPolicy = None,
        persistence_dir: Optional[str] = None,
    ):
        self.policy = policy or RetryPolicy()
        self.attempt_log: Dict[str, List[RetryAttempt]] = {}
        self.persistence_dir = Path(persistence_dir or "/tmp/retry_state")
        self.persistence_dir.mkdir(parents=True, exist_ok=True)

    async def classify_error(self, error: Exception) -> ErrorClassification:
        """Classify if error is retryable (Phase 2)."""
        error_type = type(error).__name__
        # Heuristic: TimeoutError, ConnectionError → transient
        if error_type in ("TimeoutError", "ConnectionError", "OSError"):
            return ErrorClassification.TRANSIENT
        # ValueError, RuntimeError → terminal (likely user input)
        if error_type in ("ValueError", "RuntimeError"):
            return ErrorClassification.TERMINAL
        # Default: transient (safer to retry)
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
        delay = min(
            self.policy.backoff_base_sec * (2 ** attempt),
            self.policy.backoff_max_sec,
        )
        return delay

    async def record_attempt(
        self,
        task_id: str,
        split_id: str,
        attempt: int,
        error: Optional[Exception],
        success: bool,
    ) -> None:
        """Persist retry attempt to disk (Phase 2)."""
        attempt_record = RetryAttempt(
            task_id=task_id,
            split_id=split_id,
            attempt=attempt,
            error_type=type(error).__name__ if error else "none",
            error_msg=str(error) if error else "",
            timestamp=datetime.now().timestamp(),
            backoff_delay_sec=await self.get_backoff_delay(attempt),
            success=success,
        )

        key = f"{task_id}_{split_id}"
        if key not in self.attempt_log:
            self.attempt_log[key] = []
        self.attempt_log[key].append(attempt_record)

        # Persist to disk (for recovery after crash)
        try:
            retry_file = self.persistence_dir / f"{key}.jsonl"
            with open(retry_file, "a") as f:
                data = {
                    "task_id": attempt_record.task_id,
                    "split_id": attempt_record.split_id,
                    "attempt": attempt_record.attempt,
                    "error_type": attempt_record.error_type,
                    "timestamp": attempt_record.timestamp,
                    "success": attempt_record.success,
                }
                f.write(json.dumps(data) + "\n")
        except Exception as e:
            logger.warning(f"Failed to persist retry attempt: {e}")
