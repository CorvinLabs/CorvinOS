"""Event Persistence — disk I/O + audit integration (ADR-0314)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from .event_schema import LearningEvent, LearningEventType


class EventStore:
    """Persist learning events to disk with audit trail."""

    def __init__(self, tenant_home: Path):
        """Initialize store.

        Args:
            tenant_home: Tenant root directory (~/.corvin/tenants/_default/)
        """
        self.tenant_home = tenant_home
        self.events_dir = tenant_home / "global" / "learning" / "events"
        self.events_dir.mkdir(parents=True, exist_ok=True)

        self.audit_path = tenant_home / "global" / "forge" / "audit.jsonl"

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

    async def cleanup_old_events(self, tenant_id: str, retention_days: int = 90) -> int:
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

    async def get_event_count(self, tenant_id: str) -> int:
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

    async def read_decisions(
        self,
        tenant_id: str,
        session_id: Optional[str] = None,
        choice_type: Optional[str] = None,
        limit: int = 1000,
    ) -> list[dict]:
        """Read decision records (ADR-0316).

        Args:
            tenant_id: Tenant ID
            session_id: Filter by session
            choice_type: Filter by choice type
            limit: Max results

        Returns:
            Decision record payloads
        """
        decisions = []

        for events_file in sorted(self.events_dir.glob("*.jsonl"), reverse=True):
            if len(decisions) >= limit:
                break

            with open(events_file) as f:
                for line in reversed(f.readlines()):
                    if not line.strip():
                        continue

                    event_dict = json.loads(line)

                    # Filter
                    if event_dict.get("tenant_id") != tenant_id:
                        continue
                    if not event_dict["event_type"].endswith("decision.record"):
                        continue
                    if session_id and event_dict.get("session_id") != session_id:
                        continue
                    if choice_type and event_dict.get("payload", {}).get("choice_type") != choice_type:
                        continue

                    decisions.append(event_dict.get("payload", {}))

                    if len(decisions) >= limit:
                        break

        return decisions

    async def read_outcomes(
        self,
        tenant_id: str,
        session_id: Optional[str] = None,
        decision_id: Optional[str] = None,
        limit: int = 1000,
    ) -> list[dict]:
        """Read outcome feedback records (ADR-0317).

        Args:
            tenant_id: Tenant ID
            session_id: Filter by session
            decision_id: Filter by decision
            limit: Max results

        Returns:
            Outcome record payloads
        """
        outcomes = []

        for events_file in sorted(self.events_dir.glob("*.jsonl"), reverse=True):
            if len(outcomes) >= limit:
                break

            with open(events_file) as f:
                for line in reversed(f.readlines()):
                    if not line.strip():
                        continue

                    event_dict = json.loads(line)

                    # Filter
                    if event_dict.get("tenant_id") != tenant_id:
                        continue
                    if not event_dict["event_type"].endswith("outcome.observed"):
                        continue
                    if session_id and event_dict.get("session_id") != session_id:
                        continue
                    if decision_id and event_dict.get("payload", {}).get("decision_id") != decision_id:
                        continue

                    outcomes.append(event_dict.get("payload", {}))

                    if len(outcomes) >= limit:
                        break

        return outcomes

    async def read_preferences(
        self,
        tenant_id: str,
        user_id: Optional[str] = None,
        preference_type: Optional[str] = None,
        limit: int = 1000,
    ) -> list[dict]:
        """Read preference change records (ADR-0318).

        Args:
            tenant_id: Tenant ID
            user_id: Filter by user
            preference_type: Filter by preference type
            limit: Max results

        Returns:
            Preference record payloads
        """
        preferences = []

        for events_file in sorted(self.events_dir.glob("*.jsonl"), reverse=True):
            if len(preferences) >= limit:
                break

            with open(events_file) as f:
                for line in reversed(f.readlines()):
                    if not line.strip():
                        continue

                    event_dict = json.loads(line)

                    # Filter
                    if event_dict.get("tenant_id") != tenant_id:
                        continue
                    if not event_dict["event_type"].endswith("preference.set"):
                        continue
                    if user_id and event_dict.get("payload", {}).get("user_id") != user_id:
                        continue
                    if preference_type and event_dict.get("payload", {}).get("preference_type") != preference_type:
                        continue

                    preferences.append(event_dict.get("payload", {}))

                    if len(preferences) >= limit:
                        break

        return preferences
