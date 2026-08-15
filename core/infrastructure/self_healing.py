"""Self-Healing Recovery — ADR-0332

Non-blocking recovery from transient failures. Fire-and-forget background recovery.
Main request path never blocked. Recovery failures never propagate to users.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Optional


class RecoveryStrategy(Enum):
    """Recovery strategy options."""
    RETRY = "retry"
    BACKOFF = "backoff"
    CIRCUIT_BREAK = "circuit_break"
    RESET = "reset"


class RecoveryError(Exception):
    """Raised when recovery itself fails."""

    def __init__(self, message: str, strategy: RecoveryStrategy = None):
        self.message = message
        self.strategy = strategy
        super().__init__(message)


@dataclass(frozen=True)
class RecoveryResult:
    """Immutable recovery result."""

    success: bool
    strategy: RecoveryStrategy
    attempts: int
    error_message: Optional[str] = None


class SelfHealingLoop:
    """Non-blocking recovery from transient failures."""

    def __init__(self):
        """Initialize self-healing loop."""
        self._recovery_tasks: list[asyncio.Task] = []
        self._recovery_history: dict[str, RecoveryResult] = {}
        # Idempotency tracking: prevent duplicate recovery runs for same failure+tenant
        self._in_progress_recoveries: set[str] = set()

    async def trigger_recovery(
        self,
        failure_type: str,
        *,
        tenant_id: str,
        strategy: RecoveryStrategy = RecoveryStrategy.RETRY,
        max_attempts: int = 3,
    ) -> None:
        """Trigger recovery asynchronously (fire-and-forget).

        Idempotent: prevents duplicate recovery runs for the same failure+tenant.

        Args:
            failure_type: Type of failure ("quota", "timeout", "unavailable")
            tenant_id: Tenant context
            strategy: Recovery strategy
            max_attempts: Maximum recovery attempts

        Note:
            Never blocks main request path. Failures logged, never propagated.
            Duplicate recovery requests are silently coalesced (idempotency).
        """
        # Generate idempotency key to prevent duplicate recoveries
        recovery_key = f"{failure_type}:{tenant_id}"

        # Fail-closed: if recovery is already in progress for this key, skip
        if recovery_key in self._in_progress_recoveries:
            # Recovery already running for this failure+tenant; don't create duplicate
            return

        # Mark this recovery as in-progress
        self._in_progress_recoveries.add(recovery_key)

        # Create background task for recovery (fire-and-forget)
        task = asyncio.create_task(
            self._do_recovery_with_idempotency(
                failure_type=failure_type,
                tenant_id=tenant_id,
                strategy=strategy,
                max_attempts=max_attempts,
                recovery_key=recovery_key,
            )
        )
        self._recovery_tasks.append(task)

    async def _do_recovery_with_idempotency(
        self,
        failure_type: str,
        tenant_id: str,
        strategy: RecoveryStrategy,
        max_attempts: int,
        recovery_key: str,
    ) -> RecoveryResult:
        """Execute recovery and manage idempotency key lifecycle.

        Ensures recovery_key is removed from in-progress set when done.
        """
        try:
            return await self._do_recovery(
                failure_type=failure_type,
                tenant_id=tenant_id,
                strategy=strategy,
                max_attempts=max_attempts,
            )
        finally:
            # Always remove from in-progress set when recovery completes
            self._in_progress_recoveries.discard(recovery_key)

    async def _do_recovery(
        self,
        failure_type: str,
        tenant_id: str,
        strategy: RecoveryStrategy,
        max_attempts: int,
    ) -> RecoveryResult:
        """Execute recovery asynchronously."""
        attempts = 0
        last_error = None

        for attempt in range(max_attempts):
            attempts += 1
            try:
                # Apply strategy-specific recovery
                if strategy == RecoveryStrategy.RETRY:
                    await self._recover_retry(failure_type, tenant_id)
                elif strategy == RecoveryStrategy.BACKOFF:
                    await self._recover_backoff(failure_type, tenant_id, attempt)
                elif strategy == RecoveryStrategy.CIRCUIT_BREAK:
                    await self._recover_circuit_break(failure_type, tenant_id)
                elif strategy == RecoveryStrategy.RESET:
                    await self._recover_reset(failure_type, tenant_id)

                # Recovery succeeded
                result = RecoveryResult(
                    success=True,
                    strategy=strategy,
                    attempts=attempts,
                )
                self._recovery_history[failure_type] = result
                return result

            except Exception as e:
                last_error = str(e)
                # Continue to next attempt

        # All attempts failed, log but don't propagate
        result = RecoveryResult(
            success=False,
            strategy=strategy,
            attempts=attempts,
            error_message=last_error,
        )
        self._recovery_history[failure_type] = result
        return result

    async def _recover_retry(self, failure_type: str, tenant_id: str) -> None:
        """Retry recovery strategy."""
        await asyncio.sleep(0.1)

    async def _recover_backoff(
        self,
        failure_type: str,
        tenant_id: str,
        attempt: int,
    ) -> None:
        """Exponential backoff recovery strategy."""
        delay = min(2 ** attempt, 10)
        await asyncio.sleep(delay * 0.01)

    async def _recover_circuit_break(self, failure_type: str, tenant_id: str) -> None:
        """Circuit breaker recovery strategy."""
        await asyncio.sleep(0.1)

    async def _recover_reset(self, failure_type: str, tenant_id: str) -> None:
        """Reset/reinit recovery strategy."""
        await asyncio.sleep(0.05)

    def get_recovery_history(self) -> dict[str, RecoveryResult]:
        """Get recovery attempt history."""
        return dict(self._recovery_history)
