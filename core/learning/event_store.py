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

    def __init__(self, tenant_home: Path, tenant_id: Optional[str] = None):
        """Initialize event store for a tenant.

        Args:
            tenant_home: ``<corvin_home>/tenants/<tenant_id>/`` — the directory
                the events land under.
            tenant_id: when given, the store is BOUND to that tenant and
                ``write_event`` rejects an event carrying any other tenant
                (a foreign tenant's record must never land under this tenant's
                directory, GDPR Art. 32). Console routes always bind.
        """
        self.tenant_home = Path(tenant_home)
        if tenant_id is not None:
            _validate_tenant_id(tenant_id)
        self.tenant_id = tenant_id
        self.events_dir = self.tenant_home / self._EVENTS_DIR
        self.events_dir.mkdir(parents=True, exist_ok=True)

    def _get_event_file(self, timestamp: str) -> Path:
        """Get path to event file for timestamp (YYYY-MM-DD.jsonl)."""
        date_str = timestamp.split("T")[0]
        return self.events_dir / f"{date_str}.jsonl"

    def write_event(self, event: LearningEvent) -> None:
        """Write event: core audit chain FIRST (fail-closed), then disk.

        ADR-0314 / CLAUDE.md § Phase 3: "write_event writes the core chain
        FIRST; no chain commit → no disk record". Until 2026-09-06 only the
        sibling ``event_persistence.EventStore`` honoured that; THIS store — the
        one every live producer uses (``core/skills/boot.py``, the console
        emitter, operator ratings) — appended plain JSONL and never touched the
        hash chain, so every ACP ``skill_executed`` learning event was
        unattributed in the audit trail.

        The chain record is CONTENT-FREE (ids, type, skill, lom — never the
        ``signal`` payload); the disk record carries the returned ``audit_ref``
        so an operator can join the two.

        Raises:
            RuntimeError: the core audit writer is unavailable or the chain
                write did not commit — nothing is written to disk then.
            IOError: the disk append failed AFTER the chain committed (the
                chain record stands; the disk copy is the lossy side).
        """
        if self.tenant_id is not None and event.tenant_id != self.tenant_id:
            raise ValueError(
                f"Tenant mismatch: store is bound to {self.tenant_id!r}, "
                f"event carries {event.tenant_id!r}"
            )
        with self._lock:
            audit_ref = self._audit_chain_first(event)
            event_file = self._get_event_file(event.timestamp)

            try:
                event_dict = event.to_dict()
                event_dict["audit_ref"] = audit_ref
                line = json.dumps(event_dict, separators=(",", ":")) + "\n"

                with open(event_file, "a") as f:
                    f.write(line)

            except IOError as e:
                raise IOError(f"Failed to write learning event: {e}")

    @staticmethod
    def _audit_chain_first(event: LearningEvent) -> str:
        """Commit a content-free record to the core hash chain; return audit_ref.

        Shares ``event_persistence.core_audit_event`` (writer resolution +
        commit verification by chain-tail read-back) so both stores have ONE
        fail-closed path, not two.
        """
        from core.learning.event_persistence import core_audit_event  # noqa: PLC0415

        details = {
            "event_id": event.event_id,
            "event_type": event.event_type.value,
            "skill_id": event.skill_id,
            "skill_version": event.skill_version,
            "lom": event.lom,
        }
        return core_audit_event(
            f"learning.{event.event_type.value}",
            tenant_id=event.tenant_id,
            details=details,
        )

    def query_events(
        self,
        tenant_id: str,
        event_type: Optional[EventType] = None,
        skill_id: Optional[str] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
        limit: int = 10000,  # FIX #21: Prevent OOM on unbounded queries
        offset: int = 0,
        newest_first: bool = False,
    ) -> list[LearningEvent]:
        """Query events with optional filters.

        Order and selection (round-3 review, R3-B2):

        * ``newest_first=False`` (default) — date files ascending, events in
          write order, and ``offset``/``limit`` select from the OLDEST end.
        * ``newest_first=True`` — date files descending and each file's lines
          reversed, so ``limit`` selects the NEWEST N and the result is ordered
          newest → oldest. Consumers that want "the last N samples" (e.g.
          :class:`core.learning.consistency_checker.FeedbackConsistencyValidator`)
          MUST pass this: the default returned the oldest N, so a "recent"
          window computed from it never advanced.
        """
        # FIX #6: Validate tenant_id upfront (prevent cross-tenant leakage, GDPR Art. 32)
        _validate_tenant_id(tenant_id)

        with self._lock:
            results: list[LearningEvent] = []
            wanted = offset + limit

            start_date = since or "2026-01-01"
            end_date = until or datetime.utcnow().strftime("%Y-%m-%d")

            for event_file in sorted(self.events_dir.glob("*.jsonl"), reverse=newest_first):
                file_date = event_file.stem

                if file_date < start_date or file_date > end_date:
                    continue

                file_results: list[LearningEvent] = []
                try:
                    with open(event_file, "r") as f:
                        for line in f:
                            if not line.strip():
                                continue

                            # One bad line must never discard the rest of the
                            # file: a malformed JSON line used to abort the
                            # whole file, and an unknown ``event_type`` enum
                            # value raised ValueError straight OUT of
                            # query_events, so a single newer-schema record made
                            # every consumer see an empty history (round-3
                            # review, R3-B3).
                            try:
                                data = json.loads(line)
                            except json.JSONDecodeError as e:
                                logger.warning(
                                    f"Corrupted JSON line in {event_file}: {e} — event LOST "
                                    f"at {datetime.utcnow().isoformat()}Z"
                                )
                                continue

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

                            try:
                                parsed_type = EventType(data["event_type"])
                            except ValueError:
                                logger.warning(
                                    f"Skipping event with unknown event_type "
                                    f"{data.get('event_type')!r} in {event_file}"
                                )
                                continue

                            # FIX #14, #25: Include version in reconstruction (prevent schema drift)
                            event = LearningEvent(
                                event_id=data["event_id"],
                                event_type=parsed_type,
                                skill_id=data["skill_id"],
                                tenant_id=data["tenant_id"],
                                timestamp=data["timestamp"],
                                version=data.get("version", "1.0"),  # Default to 1.0 if missing
                                signal=data.get("signal"),
                                skill_config_delta=data.get("skill_config_delta"),
                                skill_version=data.get("skill_version"),
                                lom=data.get("lom"),
                                prev_hash=data.get("prev_hash"),
                                audit_ref=data.get("audit_ref"),
                            )
                            file_results.append(event)

                            # FIX #21 optimization: early exit when limit+offset
                            # reached. Only valid oldest-first — the newest N of
                            # a file live at its END, so a newest-first query
                            # must read the whole file before slicing.
                            if not newest_first and len(results) + len(file_results) >= wanted:
                                break

                except IOError as e:
                    logger.error(f"IO error reading {event_file}: {e}")
                    continue

                if newest_first:
                    file_results.reverse()
                results.extend(file_results)

                if len(results) >= wanted:
                    return results[offset:wanted]

            # FIX #21: Apply limit + offset to prevent OOM
            return results[offset:wanted]

    def count_events(self, tenant_id: str, event_type: Optional[EventType] = None) -> int:
        """Count events for a tenant (stream-based, O(n) time, O(1) space).

        FIX #22: Don't materialize all results; stream-count instead.
        """
        _validate_tenant_id(tenant_id)
        count = 0

        with self._lock:
            for event_file in sorted(self.events_dir.glob("*.jsonl")):
                try:
                    with open(event_file, "r") as f:
                        for line in f:
                            if not line.strip():
                                continue
                            data = json.loads(line)
                            if data.get("tenant_id") != tenant_id:
                                continue
                            if event_type and data.get("event_type") != event_type.value:
                                continue
                            count += 1
                except (json.JSONDecodeError, IOError):
                    continue

        return count
