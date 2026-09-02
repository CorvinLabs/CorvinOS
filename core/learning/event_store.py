"""Phase 2: EventStore — Learning event persistence (ADR-0314)."""

from __future__ import annotations

import json
import logging
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from core.learning.learning_events import LearningEvent, EventType

logger = logging.getLogger(__name__)


def _validate_tenant_id(tenant_id: str) -> None:
    """Validate tenant_id format (alphanumeric + underscore, no path traversal).

    FIX #6: Prevent tenant isolation bypass (GDPR Art. 32).
    """
    if not tenant_id or not isinstance(tenant_id, str):
        raise ValueError(f"Invalid tenant_id: must be non-empty string, got {tenant_id!r}")

    if not re.match(r'^[a-zA-Z0-9_-]+$', tenant_id):
        raise ValueError(f"Invalid tenant_id format: {tenant_id!r}")


class EventStore:
    """Date-partitioned JSON event storage (GDPR Art. 30, 32).

    Structure:
      {tenant_home}/global/learning/events/YYYY-MM-DD.jsonl
      One JSON line per event, append-only
    """

    _EVENTS_DIR = "learning/events"
    _lock = threading.RLock()

    def __init__(self, tenant_home: Path):
        """Initialize event store for a tenant."""
        self.tenant_home = Path(tenant_home)
        self.events_dir = self.tenant_home / self._EVENTS_DIR
        self.events_dir.mkdir(parents=True, exist_ok=True)

    def _get_event_file(self, timestamp: str) -> Path:
        """Get path to event file for timestamp (YYYY-MM-DD.jsonl)."""
        date_str = timestamp.split("T")[0]
        return self.events_dir / f"{date_str}.jsonl"

    def write_event(self, event: LearningEvent) -> None:
        """Write event to store (append-only, atomic)."""
        with self._lock:
            event_file = self._get_event_file(event.timestamp)

            try:
                event_dict = event.to_dict()
                line = json.dumps(event_dict, separators=(",", ":")) + "\n"

                with open(event_file, "a") as f:
                    f.write(line)

            except IOError as e:
                raise IOError(f"Failed to write learning event: {e}")

    def query_events(
        self,
        tenant_id: str,
        event_type: Optional[EventType] = None,
        skill_id: Optional[str] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
    ) -> list[LearningEvent]:
        """Query events with optional filters."""
        # FIX #6: Validate tenant_id upfront (prevent cross-tenant leakage, GDPR Art. 32)
        _validate_tenant_id(tenant_id)

        with self._lock:
            results = []

            start_date = since or "2026-01-01"
            end_date = until or datetime.utcnow().strftime("%Y-%m-%d")

            for event_file in sorted(self.events_dir.glob("*.jsonl")):
                file_date = event_file.stem

                if file_date < start_date or file_date > end_date:
                    continue

                try:
                    with open(event_file, "r") as f:
                        for line in f:
                            if not line.strip():
                                continue

                            data = json.loads(line)

                            # FIX #13: Validate required fields before reconstruction (prevent KeyError)
                            required_fields = {"event_id", "event_type", "skill_id", "tenant_id", "timestamp"}
                            if not all(field in data for field in required_fields):
                                logger.warning(f"Skipping malformed event: missing fields {required_fields - set(data.keys())} in {data}")
                                continue

                            if data.get("tenant_id") != tenant_id:
                                continue

                            if event_type and data.get("event_type") != event_type.value:
                                continue
                            if skill_id and data.get("skill_id") != skill_id:
                                continue

                            event = LearningEvent(
                                event_id=data["event_id"],
                                event_type=EventType(data["event_type"]),
                                skill_id=data["skill_id"],
                                tenant_id=data["tenant_id"],
                                timestamp=data["timestamp"],
                                signal=data.get("signal"),
                                skill_config_delta=data.get("skill_config_delta"),
                                skill_version=data.get("skill_version"),
                                lom=data.get("lom"),
                                prev_hash=data.get("prev_hash"),
                            )
                            results.append(event)

                except json.JSONDecodeError as e:
                    # FIX #4: Log corrupted JSON instead of silent skip (audit trail incomplete)
                    logger.warning(f"Corrupted JSON in {event_file} (line {event_file.name}): {e} — event(s) LOST")
                    continue
                except IOError as e:
                    logger.error(f"IO error reading {event_file}: {e}")
                    continue

            return results

    def count_events(self, tenant_id: str, event_type: Optional[EventType] = None) -> int:
        """Count events for a tenant."""
        return len(self.query_events(tenant_id, event_type=event_type))
