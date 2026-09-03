"""Mock EventStore for task execution audit trail (ADR-0314, ADR-0541)."""

from typing import List, Dict, Any, Optional
from datetime import datetime
from .models import AuditEvent, Snapshot


class EventStore:
    """In-memory audit event store with hash-chain verification (ADR-0541)."""

    def __init__(self, tenant_id: str = "_default"):
        self.tenant_id = tenant_id
        self.events: List[AuditEvent] = []
        self.snapshots: Dict[str, Snapshot] = {}

    def append_event(self, event: AuditEvent) -> str:
        """Append immutable event to chain. Returns event hash."""
        if event.tenant_id != self.tenant_id:
            raise ValueError(f"Tenant mismatch: expected {self.tenant_id}, got {event.tenant_id}")

        # Set prev_hash to last event's hash
        if self.events:
            prev_hash = self.events[-1].hash
            object.__setattr__(event, "prev_hash", prev_hash)

        self.events.append(event)
        return event.hash

    def create_snapshot(self, task_id: str, session_id: str, phase_id: str,
                       state: Dict[str, Any]) -> Snapshot:
        """Create immutable snapshot at session boundary."""
        snapshot = Snapshot(
            task_id=task_id,
            tenant_id=self.tenant_id,
            session_id=session_id,
            snapshot_timestamp=datetime.utcnow().isoformat() + "Z",
            phase_completed=phase_id,
            events_count=len(self.events),
            last_event_hash=self.events[-1].hash if self.events else "",
            state=state,
        )
        self.snapshots[snapshot.snapshot_hash] = snapshot
        return snapshot

    def get_snapshot(self, snapshot_hash: str) -> Optional[Snapshot]:
        """Load snapshot by hash."""
        return self.snapshots.get(snapshot_hash)

    def query(self, task_id: Optional[str] = None, session_id: Optional[str] = None) -> List[AuditEvent]:
        """Query events by task/session (tenant-scoped)."""
        result = []
        for event in self.events:
            if event.tenant_id != self.tenant_id:
                continue
            if task_id and event.task_id != task_id:
                continue
            if session_id and event.session_id != session_id:
                continue
            result.append(event)
        return result

    def verify_chain(self, task_id: str) -> bool:
        """Verify audit-chain integrity for task (ADR-0541 Fix 5.1)."""
        events = self.query(task_id=task_id)
        if not events:
            return True

        # Verify internal hash-chain
        for i in range(1, len(events)):
            if events[i].prev_hash != events[i - 1].hash:
                return False

        return True

    def verify_session_bridge(self, source_session: str, dest_session: str) -> bool:
        """Verify session bridge event exists and is valid."""
        events = self.query(session_id=source_session)
        if not events:
            return False

        # Find task_session_bridged event
        bridge_events = [e for e in events if e.event_type == "task_session_bridged"]
        if len(bridge_events) != 1:
            return False

        bridge = bridge_events[0]
        return bridge.payload.get("dest_session") == dest_session

    def get_all_events(self) -> List[AuditEvent]:
        """Return all events for this tenant."""
        return [e for e in self.events if e.tenant_id == self.tenant_id]
