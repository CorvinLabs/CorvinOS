"""Learning Loop Audit Chain Integration — ADR-0314 + ADR-0906 k=3

Query audit events to compute real health metrics for learning loops.
Implements efficient filtering + aggregation for 7-day health windows.

NOT WIRED: no production caller as of 2026-09-27 (adversarial review). The only
caller is the Flask blueprint ``corvin_console.routes.learning_loops``, which
nothing registers (the console is FastAPI).

Fixed 2026-09-27 (adversarial review) — every one of these made the helper
report nothing or crash:

* the default chain was hand-composed as
  ``~/.corvin/tenants/_default/global/audit.jsonl`` — neither the canonical
  ``tenant_audit_chain()`` file (``.../global/forge/audit.jsonl``) nor
  ``CORVIN_HOME``-aware, so on a real install it never existed;
* records were filtered on an ISO ``timestamp`` key, while the core writer
  stamps ``ts`` (epoch seconds) — every real record was skipped;
* ``_compute_status`` subtracted an AWARE timestamp (``...Z`` → ``+00:00``)
  from the NAIVE ``datetime.utcnow()`` → ``TypeError`` (not caught);
* every ``SkillExecutedEvent`` counted for every loop — one global count was
  presented as each loop's own health. Only records attributable to the loop
  (``details.loop_id`` or ``details.skill_id`` equal to the loop id, or the
  loop's plugin-scoped id's local part) are counted now.
"""

from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json


#: Event types that record one skill execution: the core writer's spelling
#: (``skill.executed``) plus the legacy ``SkillExecutedEvent*`` names.
_SKILL_EXECUTED_TYPES = ("skill.executed", "skill_executed")
_LEGACY_PREFIX = "SkillExecutedEvent"


def _event_time(event: Dict[str, Any]) -> Optional[datetime]:
    """Aware UTC time of a chain record (``ts`` epoch, or ISO ``timestamp``)."""
    ts = event.get("ts")
    if isinstance(ts, (int, float)) and not isinstance(ts, bool):
        try:
            return datetime.fromtimestamp(float(ts), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    raw = event.get("timestamp")
    if isinstance(raw, str) and raw:
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if dt else None


class AuditQueryHelper:
    """Query audit chain for learning loop health metrics."""

    # k=3: Expected event frequency (for health score)
    # Default: ≥1 event per day = healthy
    MIN_EVENTS_PER_7D = 7

    @staticmethod
    def compute_loop_health_from_audit(
        loop_id: str,
        event_source: str,
        audit_chain_path: Optional[Path] = None,
        days_window: int = 7,
        tenant_id: str = "_default",
    ) -> Dict[str, Any]:
        """
        Query audit chain and compute health metrics for a learning loop.

        Returns:
            {
                "last_event_ts": "2026-09-25T14:30:00Z",
                "event_count_7d": 42,
                "health_score": 0.85,
                "status": "active" | "dormant" | "degrading" | "stale" | "unknown"
            }
        """
        if audit_chain_path is None:
            from core.paths.tenant import tenant_audit_chain
            audit_chain_path = tenant_audit_chain(tenant_id)

        if not audit_chain_path.exists():
            # Graceful degradation: audit chain not available
            return {
                "last_event_ts": None,
                "event_count_7d": 0,
                "health_score": None,
                "status": "unknown"  # Cannot compute without audit
            }

        # Query audit events matching this loop
        cutoff_time = datetime.now(timezone.utc) - timedelta(days=days_window)
        matching_events = AuditQueryHelper._query_events_for_loop(
            audit_chain_path, loop_id, event_source, cutoff_time
        )

        if not matching_events:
            return {
                "last_event_ts": None,
                "event_count_7d": 0,
                "health_score": 0.0,
                "status": "dormant"
            }

        # Compute metrics
        last_event = matching_events[-1]  # Most recent (append-only)
        event_count = len(matching_events)
        health_score = AuditQueryHelper._compute_health_score(
            event_count, AuditQueryHelper.MIN_EVENTS_PER_7D
        )
        status = AuditQueryHelper._compute_status(health_score, last_event)

        return {
            "last_event_ts": _iso(_event_time(last_event)),
            "event_count_7d": event_count,
            "health_score": health_score,
            "status": status
        }

    @staticmethod
    def _attributable(event: Dict[str, Any], loop_id: str) -> bool:
        details = event.get("details")
        if not isinstance(details, dict):
            details = {}
        local = loop_id.split(":", 1)[-1]
        for key in ("loop_id", "skill_id"):
            val = details.get(key, event.get(key))
            if isinstance(val, str) and val and val in (loop_id, local):
                return True
        return False

    @staticmethod
    def _query_events_for_loop(
        audit_chain_path: Path,
        loop_id: str,
        event_source: str,
        cutoff_time: datetime
    ) -> List[Dict[str, Any]]:
        """
        Query the chain for skill-execution records of ``loop_id``.

        1. Read file line-by-line (don't load entire chain)
        2. Filter by record time (≥ cutoff_time; records without one are skipped)
        3. Filter by a skill-execution event type
        4. Keep only records attributable to this loop
        """
        if cutoff_time.tzinfo is None:
            cutoff_time = cutoff_time.replace(tzinfo=timezone.utc)
        matching_events = []

        try:
            with open(audit_chain_path, "r") as f:
                for line in f:
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue  # Skip malformed lines (graceful degradation)
                    if not isinstance(event, dict):
                        continue

                    when = _event_time(event)
                    if when is None or when < cutoff_time:
                        continue

                    event_type = str(event.get("event_type", ""))
                    if not (event_type in _SKILL_EXECUTED_TYPES
                            or event_type.startswith(_LEGACY_PREFIX)):
                        continue

                    if not AuditQueryHelper._attributable(event, loop_id):
                        continue

                    matching_events.append(event)

        except FileNotFoundError:
            pass  # Audit chain not available

        return matching_events

    @staticmethod
    def _compute_health_score(event_count: int, min_expected: int) -> float:
        """
        Compute health score from event count.

        Formula (k=3):
        health = min(1.0, event_count / min_expected)

        - 0 events → 0.0 (dead)
        - min_expected events → 1.0 (healthy)
        - 2x min_expected events → 1.0 (capped)
        """
        if min_expected == 0:
            return 1.0

        score = min(1.0, event_count / min_expected)
        return float(score)

    @staticmethod
    def _compute_status(health_score: float, last_event: Dict[str, Any]) -> str:
        """
        Compute loop status from health score + recency.

        Status values:
        - "active": health ≥ 0.5 and event within 24h
        - "dormant": health > 0 but no events in 24h
        - "degrading": health < 0.5
        - "stale": no events in 7 days
        - "unknown": the record carries no readable time
        """
        if not last_event:
            return "stale"

        last_dt = _event_time(last_event)
        if last_dt is None:
            return "unknown"  # Malformed / missing timestamp
        age = datetime.now(timezone.utc) - last_dt

        if age > timedelta(days=7):
            return "stale"
        elif age > timedelta(hours=24):
            return "dormant"
        elif health_score < 0.5:
            return "degrading"
        else:
            return "active"
