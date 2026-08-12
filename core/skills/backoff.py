"""Self-Healing Backoff — recovers from transient health issues (ADR-0310)."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable


class BackoffState(Enum):
    """Backoff state machine."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    RECOVERING = "recovering"


@dataclass
class BackoffConfig:
    """Backoff configuration."""

    base_delay_s: float = 1.0  # Initial retry delay
    max_delay_s: float = 60.0  # Max retry delay
    multiplier: float = 2.0  # Exponential backoff multiplier
    max_retries: int = 10  # Max retry attempts before giving up
    check_interval_s: float = 5.0  # How often to check health


class SelfHealingBackoff:
    """Implements exponential backoff for unhealthy components."""

    def __init__(self, config: BackoffConfig):
        """Initialize backoff.

        Args:
            config: BackoffConfig instance
        """
        self.config = config
        self.state = BackoffState.HEALTHY
        self.retry_count = 0
        self.current_delay = config.base_delay_s
        self.last_recovery_attempt = time.time()

    async def execute_with_backoff(
        self,
        fn: Callable[[], Any],
        health_check: Callable[[], bool],
    ) -> bool:
        """Execute function with backoff on failure.

        Args:
            fn: Async function to execute
            health_check: Async function returning True if healthy

        Returns:
            True if successful, False if exhausted retries
        """
        while self.retry_count < self.config.max_retries:
            try:
                # Try to execute
                await fn()

                # Check if healthy now
                if await health_check():
                    self._recover()
                    return True
                else:
                    self._degrade()
                    await asyncio.sleep(self.current_delay)
                    self._apply_backoff()

            except Exception:
                self._degrade()
                await asyncio.sleep(self.current_delay)
                self._apply_backoff()

        # Exhausted retries
        self.state = BackoffState.DEGRADED
        return False

    def _degrade(self) -> None:
        """Mark as degraded."""
        self.state = BackoffState.DEGRADED
        self.retry_count += 1

    def _recover(self) -> None:
        """Reset backoff on successful recovery."""
        self.state = BackoffState.HEALTHY
        self.retry_count = 0
        self.current_delay = self.config.base_delay_s

    def _apply_backoff(self) -> None:
        """Apply exponential backoff."""
        self.current_delay = min(
            self.current_delay * self.config.multiplier,
            self.config.max_delay_s,
        )

    def get_status(self) -> dict[str, Any]:
        """Get current backoff status."""
        return {
            "state": self.state.value,
            "retry_count": self.retry_count,
            "current_delay": self.current_delay,
            "max_retries": self.config.max_retries,
        }
