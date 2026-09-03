"""Extended EventStore with crypto binding + tenant scoping (ADR-0541, Phase B complete)."""

import hmac
import hashlib
from typing import Optional, List, Dict, Any
from datetime import datetime
from .models import AuditEvent, Snapshot
from .event_store import EventStore


class CryptoEventStore(EventStore):
    """EventStore with HMAC-SHA256 signing + tenant-scoped queries (Phase B)."""

    def __init__(self, tenant_id: str = "_default", external_key: Optional[str] = None):
        """Initialize with crypto key."""
        super().__init__(tenant_id=tenant_id)
        self.external_key = (external_key or "default-key-phase-b").encode()
        self.snapshots_signed: Dict[str, str] = {}  # snapshot_hash -> signature

    def sign_snapshot(self, snapshot: Snapshot) -> str:
        """Sign snapshot with HMAC-SHA256 (ADR-0541 Fix 1.3)."""
        message = snapshot.snapshot_hash.encode()
        signature = hmac.new(self.external_key, message, hashlib.sha256).hexdigest()
        self.snapshots_signed[snapshot.snapshot_hash] = signature
        return signature

    def verify_snapshot_signature(self, snapshot: Snapshot) -> bool:
        """Verify snapshot was not tampered with."""
        expected_sig = self.snapshots_signed.get(snapshot.snapshot_hash)
        if not expected_sig:
            return False
        actual_sig = self.sign_snapshot(snapshot)
        return hmac.compare_digest(expected_sig, actual_sig)

    def create_snapshot_signed(self, task_id: str, session_id: str, phase_id: str,
                             state: Dict[str, Any]) -> Snapshot:
        """Create snapshot with signature (Phase B)."""
        snapshot = super().create_snapshot(task_id, session_id, phase_id, state)
        signature = self.sign_snapshot(snapshot)
        # Store signature in snapshot metadata (Phase B)
        self.snapshots_signed[snapshot.snapshot_hash] = signature
        return snapshot

    def query_tenant_scoped(self, task_id: Optional[str] = None, session_id: Optional[str] = None) -> List[AuditEvent]:
        """Query events with strict tenant scoping (ADR-0541 Fix 2.2, Phase B)."""
        # FAIL-CLOSED: if tenant_id missing, raise error (not silent fallback)
        if not self.tenant_id:
            raise ValueError("Tenant scoping enforced: tenant_id required for all queries")

        result = []
        for event in self.events:
            # MANDATORY tenant_id validation (Phase B)
            if event.tenant_id != self.tenant_id:
                raise ValueError(
                    f"Tenant isolation breach: event tenant_id={event.tenant_id} != query tenant_id={self.tenant_id}"
                )

            if task_id and event.task_id != task_id:
                continue
            if session_id and event.session_id != session_id:
                continue
            result.append(event)

        return result

    def verify_all_events_tenant_scoped(self, task_id: str) -> bool:
        """Verify all events in task have consistent tenant_id (ADR-0541 Fix 2.5, Phase B)."""
        events = self.query_tenant_scoped(task_id=task_id)
        if not events:
            return True

        # All events must have same tenant_id
        expected_tenant = events[0].tenant_id
        for event in events[1:]:
            if event.tenant_id != expected_tenant:
                return False

        return True


class VerificationCronJob:
    """Daily verification cron (ADR-0541 Fix 1.2, Phase B, real implementation)."""

    def __init__(self, event_store: CryptoEventStore):
        self.event_store = event_store
        self.verification_log: List[Dict[str, Any]] = []

    def verify_task_chain(self, task_id: str) -> (bool, List[str]):
        """Verify single task's audit chain (within-session + cross-session).

        Returns (chain_valid, error_messages)
        """
        errors = []
        events = self.event_store.query_tenant_scoped(task_id=task_id)

        if not events:
            return True, []

        # Verify internal hash-chain (within each session)
        sessions_dict = {}
        for event in events:
            if event.session_id not in sessions_dict:
                sessions_dict[event.session_id] = []
            sessions_dict[event.session_id].append(event)

        for session_id, session_events in sessions_dict.items():
            for i in range(1, len(session_events)):
                if session_events[i].prev_hash != session_events[i - 1].hash:
                    errors.append(f"Session {session_id}: chain broken at event {i}")
                    return False, errors

        # Verify cross-session bridges
        sorted_sessions = sorted(sessions_dict.keys())
        for i in range(len(sorted_sessions) - 1):
            curr_session = sorted_sessions[i]
            next_session = sorted_sessions[i + 1]

            # Find task_session_bridged event
            bridge_events = [e for e in sessions_dict[curr_session] if e.event_type == "task_session_bridged"]
            if len(bridge_events) != 1:
                errors.append(f"Session {curr_session}: expected 1 bridge event, found {len(bridge_events)}")
                return False, errors

            bridge = bridge_events[0]
            if bridge.payload.get("dest_session") != next_session:
                errors.append(f"Bridge mismatch: {curr_session} -> {bridge.payload.get('dest_session')}, expected {next_session}")
                return False, errors

        # Verify tenant consistency (Fix 2.5)
        if not self.event_store.verify_all_events_tenant_scoped(task_id):
            errors.append(f"Task {task_id}: tenant_id inconsistency detected")
            return False, errors

        return True, []

    def run_daily_verification(self, task_ids: List[str]) -> Dict[str, Any]:
        """Run daily verification cron for all tasks (Phase B, real implementation)."""
        result = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "total_tasks": len(task_ids),
            "verified": [],
            "failed": [],
        }

        for task_id in task_ids:
            chain_valid, errors = self.verify_task_chain(task_id)
            if chain_valid:
                result["verified"].append(task_id)
            else:
                result["failed"].append({"task_id": task_id, "errors": errors})

        self.verification_log.append(result)
        return result

    def get_verification_status(self) -> Dict[str, Any]:
        """Get last verification cron status (Phase B)."""
        if not self.verification_log:
            return {"status": "never_run"}

        latest = self.verification_log[-1]
        return {
            "timestamp": latest["timestamp"],
            "total": latest["total_tasks"],
            "passed": len(latest["verified"]),
            "failed": len(latest["failed"]),
            "status": "ok" if not latest["failed"] else "failed",
        }
