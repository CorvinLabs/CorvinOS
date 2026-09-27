"""Plugin Confidence Calculator — Learning k=6 (ADR-0923).

Calculates plugin execution confidence based on historical success/failure rates.
Integrates with event store to query PLUGIN_EXECUTED events over a lookback window.
Includes decay function for stale/unused plugins.

Reads the SAME store the plugin registry writes ``plugin_executed`` events to:
``core.learning.event_store.EventStore(tenant_home(tid), tenant_id=tid)``
(``corvin_plugins.registry._emit_plugin_executed``). Until 2026-09-27 it built
``event_persistence.EventStore``, which has no ``query_events`` — every call
raised, was caught, and returned a fabricated neutral 0.5. An unreadable store
now yields ``None`` ("not measured"), never a number.

NOT WIRED: no production caller as of 2026-09-27 (adversarial review).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import learning_events
from .event_store import EventStore

log = logging.getLogger(__name__)


def _store(tenant_id: str) -> EventStore:
    from core.paths import tenant_home  # noqa: PLC0415 — validates tenant_id

    return EventStore(tenant_home(tenant_id), tenant_id=tenant_id)


def _parse_ts(ts: str) -> datetime:
    d = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return d.replace(tzinfo=None) if d.tzinfo is None else d.astimezone(timezone.utc).replace(tzinfo=None)


def calculate_plugin_confidence(
    plugin_id: str,
    tenant_id: str,
    lookback_days: int = 7,
) -> Optional[float]:
    """Calculate plugin execution confidence [0.0, 1.0].

    Args:
        plugin_id: ID of the plugin to calculate confidence for
        tenant_id: Tenant scope (GDPR Art. 32)
        lookback_days: Days to look back in history (default: 7 days)

    Returns:
        Confidence score [0.0, 1.0]:
        - 1.0 = all executions successful
        - 0.0 = all executions failed
        - 0.5 = 50% success rate
        - (decayed value) = plugin unused >30 days
        - None = the store could not be read (not measured)
    """
    try:
        store = _store(tenant_id)

        # Query PLUGIN_EXECUTED events for this plugin within lookback window
        cutoff_time = datetime.utcnow() - timedelta(days=lookback_days)
        events = [
            e for e in store.query_events(
                tenant_id,
                skill_id=f"plugin.{plugin_id}",
                event_type=learning_events.EventType.PLUGIN_EXECUTED,
                since=cutoff_time.strftime("%Y-%m-%d"),  # the store filters by day file
            )
            if _parse_ts(e.timestamp) >= cutoff_time
        ]

        if not events:
            # No execution history — assume neutral confidence
            # Apply decay if plugin has never been executed
            last_execution = _get_last_plugin_execution(plugin_id, tenant_id)
            if last_execution is None:
                # Never executed — return 0.5 (neutral) with decay
                return 0.5 * _decay_factor_days(30)
            else:
                # Has history but nothing in lookback window
                days_since = (datetime.utcnow() - last_execution).days
                return 0.5 * _decay_factor_days(days_since)

        # Calculate success rate
        success_count = sum(1 for e in events if e.signal and e.signal.get("success", False))
        total_count = len(events)

        confidence = success_count / total_count if total_count > 0 else 0.5

        # Apply decay if plugin has been unused recently
        last_execution = events[-1].timestamp if events else None
        if last_execution:
            days_since_exec = (datetime.utcnow() - _parse_ts(last_execution)).days
            if days_since_exec > 30:
                # Plugin unused for >30 days — decay confidence
                confidence *= _decay_factor_days(days_since_exec)

        return max(0.0, min(1.0, confidence))  # Clamp to [0.0, 1.0]

    except Exception as exc:  # noqa: BLE001
        log.error("plugin_confidence.calculate_plugin_confidence failed for %r: %s",
                  plugin_id, type(exc).__name__)
        return None  # not measured — never a fabricated neutral score


def _get_last_plugin_execution(plugin_id: str, tenant_id: str) -> Optional[datetime]:
    """Get the timestamp of the plugin's most recent execution (any lookback)."""
    try:
        events = _store(tenant_id).query_events(
            tenant_id,
            skill_id=f"plugin.{plugin_id}",
            event_type=learning_events.EventType.PLUGIN_EXECUTED,
            since="2020-01-01",  # all history
            limit=1,
            newest_first=True,
        )
        if events:
            return _parse_ts(events[0].timestamp)
        return None
    except Exception:  # noqa: BLE001
        return None


def _decay_factor_days(days_since: int) -> float:
    """Exponential decay factor for stale plugin confidence.

    After N days of non-use, confidence decays as: e^(-lambda * N)
    where lambda = ln(2) / 30, so confidence halves every 30 days.

    Args:
        days_since: Days since last execution or creation

    Returns:
        Decay factor [0.0, 1.0] (1.0 = no decay, 0.5 = halved after 30 days)
    """
    import math

    if days_since <= 0:
        return 1.0

    # Half-life = 30 days
    lambda_param = math.log(2) / 30
    return math.exp(-lambda_param * days_since)


def emit_confidence_update(
    plugin_id: str,
    tenant_id: str,
    new_confidence: float,
    prev_confidence: Optional[float] = None,
) -> bool:
    """Emit PLUGIN_CONFIDENCE_UPDATED event if change exceeds threshold.

    Args:
        plugin_id: Plugin ID
        tenant_id: Tenant scope
        new_confidence: Newly calculated confidence [0.0, 1.0]
        prev_confidence: Previous confidence (optional; skips update if no change)

    Returns:
        True if event was emitted, False otherwise
    """
    if prev_confidence is not None:
        delta = abs(new_confidence - prev_confidence)
        if delta < 0.1:
            # Confidence change <10% — don't emit (noise filtering)
            return False

    try:
        store = _store(tenant_id)

        event = learning_events.LearningEvent.create(
            event_type=learning_events.EventType.PLUGIN_CONFIDENCE_UPDATED,
            skill_id=f"plugin.{plugin_id}",
            tenant_id=tenant_id,
            signal={
                "plugin_id": plugin_id,
                "confidence": float(new_confidence),
                "prev_confidence": float(prev_confidence) if prev_confidence else None,
                "delta": float(new_confidence - prev_confidence) if prev_confidence else None,
            },
            lom="learning.plugin_confidence.emit_confidence_update",
        )
        store.write_event(event)
        return True
    except Exception as exc:  # noqa: BLE001
        log.error(
            "emit_confidence_update failed for plugin %r: %s",
            plugin_id, exc
        )
        return False
