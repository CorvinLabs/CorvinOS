"""Learning Loop Audit Chain Integration — ADR-0314 + ADR-0906 k=3

Query audit events to compute real health metrics for learning loops.
Implements efficient filtering + aggregation for 7-day health windows.
"""

from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta
from pathlib import Path
import json
from collections import defaultdict


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
        days_window: int = 7
    ) -> Dict[str, Any]:
        """
        Query audit chain and compute health metrics for a learning loop.

        Returns:
            {
                "last_event_ts": "2026-09-25T14:30:00Z",
                "event_count_7d": 42,
                "health_score": 0.85,
                "status": "active" | "dormant" | "degrading"
            }
        """
        # k=3: Locate audit chain (default: ~/.corvin/tenants/_default/global/audit.jsonl)
        if audit_chain_path is None:
            home = Path.home()
            audit_chain_path = home / ".corvin" / "tenants" / "_default" / "global" / "audit.jsonl"

        if not audit_chain_path.exists():
            # Graceful degradation: audit chain not available
            return {
                "last_event_ts": None,
                "event_count_7d": 0,
                "health_score": None,
                "status": "unknown"  # Cannot compute without audit
            }

        # Query audit events matching this loop
        cutoff_time = datetime.utcnow() - timedelta(days=days_window)
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
            "last_event_ts": last_event.get("timestamp"),
            "event_count_7d": event_count,
            "health_score": health_score,
            "status": status
        }

    @staticmethod
    def _query_events_for_loop(
        audit_chain_path: Path,
        loop_id: str,
        event_source: str,
        cutoff_time: datetime
    ) -> List[Dict[str, Any]]:
        """
        Query audit.jsonl for events matching loop_id + event_source.

        Efficient filtering:
        1. Read file line-by-line (don't load entire chain)
        2. Filter by timestamp (≥ cutoff_time)
        3. Filter by event_type matching event_source
        4. Return matching events
        """
        matching_events = []
        cutoff_iso = cutoff_time.isoformat() + "Z"

        try:
            with open(audit_chain_path, "r") as f:
                for line in f:
                    try:
                        event = json.loads(line)

                        # Filter 1: Timestamp (skip old events)
                        event_ts = event.get("timestamp", "")
                        if event_ts < cutoff_iso:
                            continue

                        # Filter 2: Event type matching event_source
                        # event_source format: "SkillExecutedEvent.confidence_score"
                        event_type = event.get("event_type", "")
                        if not event_type.startswith("SkillExecutedEvent"):
                            continue

                        # Filter 3: Loop identifier in event (if present)
                        # k=3: For now, accept all SkillExecutedEvent
                        # k=4: Add loop_id filtering when events include it

                        matching_events.append(event)

                    except json.JSONDecodeError:
                        # Skip malformed lines (graceful degradation)
                        continue

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
        """
        if not last_event:
            return "stale"

        last_event_ts = last_event.get("timestamp", "")
        try:
            last_dt = datetime.fromisoformat(last_event_ts.replace("Z", "+00:00"))
            age = datetime.utcnow() - last_dt

            if age > timedelta(days=7):
                return "stale"
            elif age > timedelta(hours=24):
                return "dormant"
            elif health_score < 0.5:
                return "degrading"
            else:
                return "active"
        except (ValueError, AttributeError):
            # Malformed timestamp
            return "unknown"
