"""Event Persistence — disk I/O + audit integration (ADR-0314)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from .event_schema import LearningEvent, LearningEventType


class EventStore:
    """Persist learning events to disk with audit trail."""

    def __init__(self, tenant_id: str):
        """Initialize store.

        Args:
            tenant_id: Tenant identifier (e.g., "_default")

        Raises:
            ValueError: If tenant_id is invalid
        """
        from core.paths import tenant_learning_dir, tenant_audit_file

        self.tenant_id = tenant_id
        self.events_dir = tenant_learning_dir(tenant_id) / "events"
        self.events_dir.mkdir(parents=True, exist_ok=True)

        self.audit_path = tenant_audit_file(tenant_id)

    async def write_event(self, event: LearningEvent, tenant_id: str) -> str:
        """Write event to disk and audit chain.

        Args:
            event: Learning event to persist
            tenant_id: Tenant ID (for isolation check)

        Returns:
            audit_id (hash-chain reference)

        Raises:
            ValueError: If tenant_id mismatch
        """
        # Tenant isolation check
        if event.tenant_id != tenant_id:
            raise ValueError(f"Tenant mismatch: event.tenant_id={event.tenant_id}, expected {tenant_id}")

        # 1. Convert to audit format
        event_dict = event.to_audit_event()

        # 2. Write to audit chain (hash-chained)
        try:
            from core.compliance.corvin_compliance_reports.audit_writer import write_audit_event

            audit_id = write_audit_event(event_dict)
        except Exception as e:
            # Fallback: disk-only (no audit_id)
            audit_id = None
            event_dict["_persistence_fallback"] = str(e)

        # 3. Append to events file (date-partitioned)
        event_date = event.timestamp_utc.date()
        events_file = self.events_dir / f"{event_date.isoformat()}.jsonl"

        with open(events_file, "a") as f:
            f.write(json.dumps(event_dict) + "\n")

        return audit_id or "disk-only"

    async def read_events(
        self,
        *,
        tenant_id: str,
        event_type: Optional[LearningEventType] = None,
        skill_name: Optional[str] = None,
        session_id: Optional[str] = None,
        since: Optional[datetime] = None,
        limit: int = 1000,
    ) -> list[LearningEvent]:
        """Read events from store with filtering.

        Args:
            tenant_id: Tenant ID (isolation)
            event_type: Filter by event type
            skill_name: Filter by skill
            session_id: Filter by session
            since: Events after this timestamp
            limit: Maximum events to return

        Returns:
            Matching events, newest first
        """
        events = []

        # Scan event files (newest first)
        for events_file in sorted(self.events_dir.glob("*.jsonl"), reverse=True):
            if len(events) >= limit:
                break

            with open(events_file) as f:
                for line in reversed(f.readlines()):
                    if not line.strip():
                        continue

                    event_dict = json.loads(line)

                    # Tenant isolation
                    if event_dict.get("tenant_id") != tenant_id:
                        continue

                    # Apply filters
                    if event_type and not event_dict["event_type"].endswith(f".{event_type.value}"):
                        continue
                    if skill_name and event_dict.get("skill_name") != skill_name:
                        continue
                    if session_id and event_dict.get("session_id") != session_id:
                        continue

                    event_ts = datetime.fromisoformat(event_dict["timestamp"].rstrip("Z"))
                    if since and event_ts < since:
                        continue

                    # Reconstruct event
                    event_type_value = event_dict["event_type"].replace("learning.", "")
                    event = LearningEvent(
                        event_type=LearningEventType(event_type_value),
                        tenant_id=event_dict["tenant_id"],
                        instance_id=event_dict["instance_id"],
                        user_id=event_dict.get("user_id"),
                        skill_name=event_dict.get("skill_name"),
                        session_id=event_dict["session_id"],
                        timestamp_utc=event_ts,
                        event_id=event_dict["event_id"],
                        payload=event_dict.get("payload", {}),
                        audit_id=event_dict.get("audit_id"),
                        tags=event_dict.get("tags", []),
                    )
                    events.append(event)

                    if len(events) >= limit:
                        break

        return events

    async def cleanup_old_events(self, *, tenant_id: str, retention_days: int = 90) -> int:
        """Remove events older than retention period.

        Args:
            tenant_id: Tenant ID (isolation)
            retention_days: Keep events from last N days

        Returns:
            Number of events deleted
        """
        cutoff = datetime.utcnow() - timedelta(days=retention_days)
        cutoff_date = cutoff.date()

        deleted_count = 0

        for events_file in self.events_dir.glob("*.jsonl"):
            file_date = datetime.fromisoformat(events_file.stem).date()
            if file_date >= cutoff_date:
                continue

            # Read file, filter by tenant, write back if needed
            remaining_lines = []
            with open(events_file) as f:
                for line in f:
                    if not line.strip():
                        continue
                    event_dict = json.loads(line)
                    if event_dict.get("tenant_id") == tenant_id:
                        deleted_count += 1
                    else:
                        remaining_lines.append(line)

            # Write back non-deleted lines or delete file
            if remaining_lines:
                with open(events_file, "w") as f:
                    f.writelines(remaining_lines)
            else:
                events_file.unlink()

        return deleted_count

    async def get_event_count(self, *, tenant_id: str) -> int:
        """Get total event count for a tenant.

        Args:
            tenant_id: Tenant ID

        Returns:
            Number of events
        """
        count = 0

        for events_file in self.events_dir.glob("*.jsonl"):
            with open(events_file) as f:
                for line in f:
                    if not line.strip():
                        continue
                    event_dict = json.loads(line)
                    if event_dict.get("tenant_id") == tenant_id:
                        count += 1

        return count

    def _read_by_type(
        self,
        tenant_id: str,
        event_type_suffix: str,
        filters: dict,
        limit: int = 1000,
    ) -> list[dict]:
        """Generic reader for typed events (shared implementation).

        Args:
            tenant_id: Tenant ID
            event_type_suffix: Event type to match (e.g. "decision.record")
            filters: Dict of {field_name: value} to match on payload/top-level
                     (keys starting with "payload." match fields in payload dict)
            limit: Max results

        Returns:
            Matching event payloads
        """
        results = []

        for events_file in sorted(self.events_dir.glob("*.jsonl"), reverse=True):
            if len(results) >= limit:
                break

            with open(events_file) as f:
                for line in reversed(f.readlines()):
                    if not line.strip():
                        continue

                    event_dict = json.loads(line)

                    # Tenant + type filter
                    if event_dict.get("tenant_id") != tenant_id:
                        continue
                    if not event_dict["event_type"].endswith(event_type_suffix):
                        continue

                    # Apply optional filters
                    skip = False
                    for key, value in filters.items():
                        if key.startswith("payload."):
                            # Payload field: extract field name after "payload." prefix
                            payload_key = key[8:]  # len("payload.") == 8
                            if event_dict.get("payload", {}).get(payload_key) != value:
                                skip = True
                                break
                        else:
                            # Top-level field
                            if event_dict.get(key) != value:
                                skip = True
                                break
                    if skip:
                        continue

                    results.append(event_dict.get("payload", {}))

                    if len(results) >= limit:
                        break

        return results

    async def read_decisions(
        self,
        *,
        tenant_id: str,
        session_id: Optional[str] = None,
        choice_type: Optional[str] = None,
        limit: int = 1000,
    ) -> list[dict]:
        """Read decision records (ADR-0316).

        Args:
            tenant_id: Tenant ID for filtering
            session_id: Optional session ID to filter by
            choice_type: Optional choice type to filter by
            limit: Maximum results to return (default 1000)

        Returns:
            Decision record payloads matching the filters
        """
        filters = {}
        if session_id:
            filters["session_id"] = session_id
        if choice_type:
            filters["payload.choice_type"] = choice_type

        return self._read_by_type(
            tenant_id, "decision.record", filters, limit
        )

    async def read_outcomes(
        self,
        *,
        tenant_id: str,
        session_id: Optional[str] = None,
        decision_id: Optional[str] = None,
        limit: int = 1000,
    ) -> list[dict]:
        """Read outcome feedback records (ADR-0317).

        Args:
            tenant_id: Tenant ID for filtering
            session_id: Optional session ID to filter by
            decision_id: Optional decision ID to filter by
            limit: Maximum results to return (default 1000)

        Returns:
            Outcome record payloads matching the filters
        """
        filters = {}
        if session_id:
            filters["session_id"] = session_id
        if decision_id:
            filters["payload.decision_id"] = decision_id

        return self._read_by_type(
            tenant_id, "outcome.observed", filters, limit
        )

    async def read_preferences(
        self,
        *,
        tenant_id: str,
        user_id: Optional[str] = None,
        preference_type: Optional[str] = None,
        limit: int = 1000,
    ) -> list[dict]:
        """Read preference change records (ADR-0318).

        Args:
            tenant_id: Tenant ID for filtering
            user_id: Optional user ID to filter by
            preference_type: Optional preference type to filter by
            limit: Maximum results to return (default 1000)

        Returns:
            Preference record payloads matching the filters
        """
        filters = {}
        if user_id:
            filters["payload.user_id"] = user_id
        if preference_type:
            filters["payload.preference_type"] = preference_type

        return self._read_by_type(
            tenant_id, "preference.set", filters, limit
        )

    async def read_metrics(
        self,
        *,
        tenant_id: str,
        metric_type: Optional[str] = None,
        skill_name: Optional[str] = None,
        session_id: Optional[str] = None,
        limit: int = 1000,
    ) -> list[dict]:
        """Read metric records (ADR-0320).

        Args:
            tenant_id: Tenant ID for filtering
            metric_type: Optional metric type to filter by
            skill_name: Optional skill name to filter by
            session_id: Optional session ID to filter by
            limit: Maximum results to return (default 1000)

        Returns:
            Metric record payloads matching the filters
        """
        filters = {}
        if metric_type:
            filters["payload.metric_type"] = metric_type
        if skill_name:
            filters["skill_name"] = skill_name
        if session_id:
            filters["session_id"] = session_id

        return self._read_by_type(
            tenant_id, "metric.aggregated", filters, limit
        )
