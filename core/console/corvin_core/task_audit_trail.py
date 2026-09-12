"""Task Audit Trail — Hash-Chained Task State Transitions (ADR-0XXX).

Every task state transition (PENDING → RUNNING → COMPLETED | FAILED | CANCELLED)
is recorded as an immutable, hash-chained audit event. This enables:
1. Complete traceability of task lifecycle
2. Recovery of who changed what when
3. Compliance proof for Phase 2 VIBE deployment

Design (aligned with ADR-0665 Learning Audit Trail):
- Events are immutable, append-only
- Each event has sha256(prev_hash + event_data)
- Verification: replay chain, check hashes
- Tenant-scoped (ADR-0007)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TaskAuditEvent:
    """Immutable audit event for task state transition."""
    event_id: str
    task_id: str
    event_type: str  # task.created | task.started | task.completed | task.failed | task.cancelled
    old_state: Optional[str]  # PENDING | RUNNING | COMPLETED | FAILED | CANCELLED
    new_state: str
    executor_id: Optional[str]  # user_id or system
    reason: Optional[str]  # why the change happened
    timestamp: str  # ISO8601
    hash: str  # sha256(prev_hash + event_data)
    prev_hash: str  # hash of previous event
    tenant_id: str = "_default"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class TaskAuditTrail:
    """Immutable, hash-chained audit log for task state transitions."""

    def __init__(self, task_id: str, tenant_id: str = "_default"):
        """Initialize audit trail for a task.

        Args:
            task_id: Task identifier
            tenant_id: Tenant scoping (ADR-0007)
        """
        self.task_id = task_id
        self.tenant_id = tenant_id
        self.audit_dir = Path(
            os.environ.get("CORVIN_HOME", os.path.expanduser("~/.corvin"))
        ) / "tenants" / tenant_id / "sessions" / "tasks" / task_id
        self.audit_dir.mkdir(parents=True, exist_ok=True)
        self.audit_file = self.audit_dir / "audit_trail.jsonl"
        self.chain_hash = "genesis"

        # Load existing chain
        self._load_chain_hash()

    def _load_chain_hash(self) -> None:
        """Load the last hash from audit file (for resuming)."""
        if not self.audit_file.exists():
            self.chain_hash = "genesis"
            return

        try:
            with open(self.audit_file, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        record = json.loads(line)
                        self.chain_hash = record.get("hash", "genesis")
        except Exception as e:
            logger.warning(f"Failed to load chain hash for task {self.task_id}: {e}")
            self.chain_hash = "genesis"

    def write_event(
        self,
        event_type: str,
        old_state: Optional[str],
        new_state: str,
        executor_id: Optional[str] = None,
        reason: Optional[str] = None,
        event_id: Optional[str] = None,
    ) -> str:
        """Write event to audit trail (immutable).

        Args:
            event_type: task.created | task.started | task.completed | task.failed | task.cancelled
            old_state: Previous task state (None for task.created)
            new_state: New task state
            executor_id: Who triggered the change (user_id or "system")
            reason: Why the change happened
            event_id: Optional event identifier (auto-generated if None)

        Returns: hash of this event

        Raises:
            IOError: If audit file cannot be written
        """
        if event_id is None:
            event_id = f"{self.task_id}-{event_type}-{datetime.utcnow().isoformat()}"

        timestamp = datetime.utcnow().isoformat()

        # Build event data (in canonical order for hash consistency)
        event_data = {
            "task_id": self.task_id,
            "event_type": event_type,
            "old_state": old_state,
            "new_state": new_state,
            "executor_id": executor_id,
            "reason": reason,
            "timestamp": timestamp,
        }

        # Compute hash: sha256(prev_hash + event_data)
        data_to_hash = json.dumps(event_data, sort_keys=True, separators=(",", ":"))
        combined = f"{self.chain_hash}{data_to_hash}"
        event_hash = hashlib.sha256(combined.encode()).hexdigest()

        # Create record
        record = TaskAuditEvent(
            event_id=event_id,
            task_id=self.task_id,
            event_type=event_type,
            old_state=old_state,
            new_state=new_state,
            executor_id=executor_id,
            reason=reason,
            timestamp=timestamp,
            hash=event_hash,
            prev_hash=self.chain_hash,
            tenant_id=self.tenant_id,
        )

        # Write to file (append-only)
        try:
            with open(self.audit_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record.to_dict(), separators=(",", ":")) + "\n")
                f.flush()

            # Update chain
            self.chain_hash = event_hash
            return event_hash

        except IOError as e:
            logger.error(f"Failed to write audit event for task {self.task_id}: {e}")
            raise

    def read_events(self) -> List[Dict[str, Any]]:
        """Read all audit events for this task."""
        if not self.audit_file.exists():
            return []

        events = []
        try:
            with open(self.audit_file, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        try:
                            events.append(json.loads(line))
                        except json.JSONDecodeError as e:
                            logger.warning(f"Failed to parse audit event: {e}")
        except Exception as e:
            logger.error(f"Failed to read audit events for task {self.task_id}: {e}")

        return events

    def verify_chain(self) -> bool:
        """Verify hash chain integrity.

        Returns: True if chain is unbroken
        """
        if not self.audit_file.exists():
            return True  # No events to verify

        try:
            chain_hash = "genesis"
            with open(self.audit_file, "r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue

                    record = json.loads(line)
                    prev_hash = record.get("prev_hash")
                    event_hash = record.get("hash")

                    # Use the exact event_data structure that was used for hashing
                    # Extract only the fields that were included in the original hash
                    event_data = {
                        "task_id": record.get("task_id"),
                        "event_type": record.get("event_type"),
                        "old_state": record.get("old_state"),
                        "new_state": record.get("new_state"),
                        "executor_id": record.get("executor_id"),
                        "reason": record.get("reason"),
                        "timestamp": record.get("timestamp"),
                    }

                    # Verify prev_hash matches
                    if prev_hash != chain_hash:
                        logger.error(
                            f"Chain broken at {record['event_id']}: expected prev_hash={chain_hash}, got {prev_hash}"
                        )
                        return False

                    # Verify event hash
                    data_to_hash = json.dumps(event_data, sort_keys=True, separators=(",", ":"))
                    combined = f"{chain_hash}{data_to_hash}"
                    expected_hash = hashlib.sha256(combined.encode()).hexdigest()

                    if event_hash != expected_hash:
                        logger.error(f"Hash mismatch at {record['event_id']}: expected {expected_hash}, got {event_hash}")
                        return False

                    chain_hash = event_hash

            return True

        except Exception as e:
            logger.error(f"Verification error for task {self.task_id}: {e}")
            return False

    def get_chain_status(self) -> Dict[str, Any]:
        """Get current chain status (height, last hash, integrity)."""
        if not self.audit_file.exists():
            return {
                "task_id": self.task_id,
                "height": 0,
                "last_hash": "genesis",
                "integrity_verified": True,
                "last_verified": datetime.utcnow().isoformat(),
            }

        height = 0
        last_hash = "genesis"

        try:
            with open(self.audit_file, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        height += 1
                        record = json.loads(line)
                        last_hash = record.get("hash", "genesis")

            integrity_ok = self.verify_chain()

            return {
                "task_id": self.task_id,
                "height": height,
                "last_hash": last_hash,
                "integrity_verified": integrity_ok,
                "last_verified": datetime.utcnow().isoformat(),
            }

        except Exception as e:
            logger.error(f"Status error for task {self.task_id}: {e}")
            return {
                "task_id": self.task_id,
                "height": 0,
                "last_hash": "genesis",
                "integrity_verified": False,
                "error": str(e),
            }
