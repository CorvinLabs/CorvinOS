"""Mock EventStore for task execution audit trail (ADR-0314, ADR-0541)."""

from typing import List, Dict, Any, Optional
from datetime import datetime
from .models import AuditEvent, Snapshot


class EventStore:
    """In-memory audit event store with WAL atomicity (CRITICAL FIX, ADR-0541)."""

    def __init__(self, tenant_id: str = "_default"):
        self.tenant_id = tenant_id
        self.events: List[AuditEvent] = []
        self.snapshots: Dict[str, Snapshot] = {}
        self.wal_log: List[Dict[str, Any]] = []  # Write-ahead log for crash recovery
        import threading
        self.lock = threading.Lock()

    def append_event(self, event: AuditEvent) -> str:
        """Append immutable event to chain with WAL (CRITICAL FIX: proper atomicity)."""
        if event.tenant_id != self.tenant_id:
            raise ValueError(f"Tenant mismatch: expected {self.tenant_id}, got {event.tenant_id}")

        with self.lock:
            # CRITICAL FIX: Capture last_hash BEFORE modifying event
            last_hash = self.events[-1].hash if self.events else ""

            # CRITICAL FIX: If event doesn't have prev_hash set, set it now (BEFORE persistence)
            if not event.prev_hash and self.events:
                # Create NEW event object with correct prev_hash (immutable pattern)
                # Dataclass __post_init__ will compute correct hash
                from .models import AuditEvent as AE
                event = AE(
                    event_type=event.event_type,
                    task_id=event.task_id,
                    tenant_id=event.tenant_id,
                    timestamp=event.timestamp,
                    session_id=event.session_id,
                    phase_id=event.phase_id,
                    payload=event.payload,
                    prev_hash=last_hash  # NOW set before creation
                )

            # WAL: Log intent BEFORE appending
            self.wal_log.append({
                "op": "append_event",
                "event_hash": event.hash,
                "event_type": event.event_type,
                "timestamp": datetime.utcnow().isoformat() + "Z"
            })

            # Now append (safe: WAL logged first)
            self.events.append(event)

            return event.hash

    def create_snapshot(self, task_id: str, session_id: str, phase_id: str,
                       state: Dict[str, Any]) -> Snapshot:
        """Create immutable snapshot at session boundary (ROUND 4 FIX: with locking)."""
        with self.lock:  # ROUND 4 FIX: protect snapshot creation
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
        """Load snapshot by hash (ROUND 4 FIX: with tenant check)."""
        with self.lock:  # ROUND 4 FIX: protect snapshot access
            snapshot = self.snapshots.get(snapshot_hash)
            # ROUND 4 FIX: Verify tenant isolation
            if snapshot and snapshot.tenant_id != self.tenant_id:
                raise ValueError(f"Tenant isolation: snapshot {snapshot_hash} not for tenant {self.tenant_id}")
            return snapshot

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
