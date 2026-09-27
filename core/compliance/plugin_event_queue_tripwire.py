"""Plugin event queue tripwire — ADR-0682 (Learning k=6).

NOT WIRED: no production caller as of 2026-09-27 (adversarial review) — no
host, turn loop or route calls :func:`drain_event_queue_before_turn`.

Drain tripwire: before turn N+1, write every pending plugin event of the
tenant to the tenant audit chain. Fail-closed: when the queue cannot be fully
drained within ``timeout_sec`` — a chain write fails, the deadline passes, or
events are still pending — :class:`TripwireError` is raised and the turn must
be denied. "Drained" means "in the audit chain", never merely "read".
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

from core.audit.event_queue import DrainingError, EventQueue

logger = logging.getLogger(__name__)

_DRAIN_BATCH = 1000


@dataclass(frozen=True)
class TripwireResult:
    """Immutable tripwire result."""
    success: bool
    events_drained: int
    elapsed_ms: float
    error: Optional[str] = None

    def __bool__(self) -> bool:
        return self.success


class TripwireError(Exception):
    """Queue drain failed or timed out — turn denied (fail-closed)."""


def drain_event_queue_before_turn(
    tenant_id: str = "_default",
    timeout_sec: float = 5.0,
) -> TripwireResult:
    """Drain the tenant's plugin event queue into its audit chain.

    Raises:
        TripwireError: the queue could not be emptied into the chain in time
            (turn DENIED).
    """
    start = time.monotonic()
    deadline = start + float(timeout_sec)
    drained = 0
    try:
        queue = EventQueue(tenant_id=tenant_id)
        while True:
            remaining_s = deadline - time.monotonic()
            if remaining_s <= 0:
                raise TripwireError("queue drain timed out before the queue was empty")
            batch = queue.drain(batch_size=_DRAIN_BATCH, timeout_sec=remaining_s)
            drained += len(batch)
            pending = queue.stats().pending_count
            if pending == 0:
                break
            if not batch:
                # Pending rows exist but a drain wrote none — it cannot progress.
                raise TripwireError(f"{pending} plugin event(s) pending and not drainable")
    except TripwireError as exc:
        logger.error("plugin event tripwire FAILED (tenant=%s): %s", tenant_id, exc)
        raise
    except DrainingError as exc:
        logger.error("plugin event tripwire FAILED (tenant=%s): %s", tenant_id, exc)
        raise TripwireError(f"queue drain failed: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 - every failure denies the turn
        logger.error("plugin event tripwire FAILED (tenant=%s): %s", tenant_id,
                     type(exc).__name__)
        raise TripwireError(f"unexpected tripwire error: {type(exc).__name__}") from exc

    elapsed_ms = (time.monotonic() - start) * 1000
    return TripwireResult(success=True, events_drained=drained, elapsed_ms=elapsed_ms)


def verify_queue_drained(tenant_id: str = "_default") -> bool:
    """True iff the tenant's queue has no pending event. Fail-closed: an
    unreadable queue reads as NOT drained."""
    try:
        stats = EventQueue(tenant_id=tenant_id).stats()
    except Exception as exc:  # noqa: BLE001
        logger.warning("plugin event queue unreadable (tenant=%s): %s", tenant_id,
                       type(exc).__name__)
        return False
    if stats.pending_count:
        logger.warning("plugin event queue not drained: %d pending (tenant=%s)",
                       stats.pending_count, tenant_id)
    return stats.pending_count == 0
