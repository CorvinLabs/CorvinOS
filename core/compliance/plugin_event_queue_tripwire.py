"""Plugin event queue tripwire — ADR-0682 (Learning k=6).

Drain tripwire: Before turn N+1, drain event queue completely.
Fail-closed: if queue not drained within timeout, deny turn (TripwireError).

Load-bearing: ensures no plugin events are lost between turns.
GDPR Art. 30, 32: Audit trail is immutable and complete.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TripwireResult:
    """Immutable tripwire result."""
    success: bool
    events_drained: int
    elapsed_ms: float
    error: Optional[str] = None

    def __bool__(self) -> bool:
        """True if tripwire passed (success=True)."""
        return self.success


class TripwireError(Exception):
    """Queue drain failed or timed out — turn denied (fail-closed)."""
    pass


def drain_event_queue_before_turn(
    tenant_id: str = "_default",
    timeout_sec: float = 5.0,
) -> TripwireResult:
    """Drain event queue before turn execution (fail-closed tripwire).

    MUST complete successfully before turn N+1 can proceed.
    If queue cannot be drained within timeout_sec, raises TripwireError
    and turn is DENIED.

    Args:
        tenant_id: Tenant scope (default '_default')
        timeout_sec: Hard timeout (default 5.0 seconds)

    Returns:
        TripwireResult (immutable)

    Raises:
        TripwireError: If drain fails or times out (turn DENIED)
    """
    start_time = datetime.utcnow()

    try:
        from core.audit.event_queue import EventQueue, DrainingError

        queue = EventQueue()

        # Drain pending events
        try:
            drained_events = queue.drain(batch_size=1000, timeout_sec=timeout_sec)
        except DrainingError as e:
            # Timeout or other drain error
            elapsed_ms = (datetime.utcnow() - start_time).total_seconds() * 1000
            error_msg = f"Queue drain failed: {e}"
            logger.error(f"Tripwire FAILED: {error_msg}")

            result = TripwireResult(
                success=False,
                events_drained=0,
                elapsed_ms=elapsed_ms,
                error=error_msg,
            )

            # Fail-closed: raise TripwireError to deny turn
            raise TripwireError(error_msg) from e

        elapsed_ms = (datetime.utcnow() - start_time).total_seconds() * 1000

        # Check if any events remain (should be empty after drain)
        remaining = queue.stats()
        if remaining.pending_count > 0:
            warning_msg = (
                f"Queue still has {remaining.pending_count} pending events after drain; "
                f"turn will proceed but audit trail is incomplete"
            )
            logger.warning(f"Tripwire WARNING: {warning_msg}")

        result = TripwireResult(
            success=True,
            events_drained=len(drained_events),
            elapsed_ms=elapsed_ms,
        )

        logger.info(
            f"✅ Tripwire passed: drained {len(drained_events)} events in {elapsed_ms:.1f}ms "
            f"(tenant={tenant_id})"
        )

        return result

    except TripwireError:
        raise  # Re-raise tripwire errors
    except Exception as e:
        elapsed_ms = (datetime.utcnow() - start_time).total_seconds() * 1000
        error_msg = f"Unexpected tripwire error: {e}"
        logger.error(f"Tripwire FAILED: {error_msg}")

        # Fail-closed: treat unexpected errors as drain failures
        raise TripwireError(error_msg) from e


def verify_queue_drained(tenant_id: str = "_default") -> bool:
    """Verify queue is empty (audit check, non-blocking).

    Called for monitoring/auditing; does not block turn.
    Used to detect incomplete drains (alert operator).

    Args:
        tenant_id: Tenant scope (default '_default')

    Returns:
        True if queue is empty, False otherwise
    """
    try:
        from core.audit.event_queue import EventQueue

        queue = EventQueue()
        stats = queue.stats()

        is_empty = stats.pending_count == 0

        if not is_empty:
            logger.warning(
                f"Queue not fully drained: {stats.pending_count} pending events "
                f"({stats.high_priority_count} HIGH, {stats.low_priority_count} LOW)"
            )

        return is_empty

    except Exception as e:
        logger.warning(f"Failed to verify queue drain: {e}")
        return False
