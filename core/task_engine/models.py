"""Data models for Task Engine (ADR-0540–0545)."""

from dataclasses import dataclass, field, asdict
from typing import Any, List, Dict, Optional
from datetime import datetime
import hashlib
import json


@dataclass(frozen=True)
class AuditEvent:
    """Immutable audit event (ADR-0232, ADR-0541)."""
    event_type: str
    task_id: str
    tenant_id: str
    timestamp: str  # ISO 8601
    session_id: Optional[str] = None
    phase_id: Optional[str] = None
    payload: Dict[str, Any] = field(default_factory=dict)

    # Hash chain (ADR-0232)
    prev_hash: Optional[str] = None
    hash: str = field(default="")

    def __post_init__(self):
        # Compute hash after frozen initialization
        event_dict = asdict(self)
        event_dict.pop("hash", None)
        hash_input = json.dumps(event_dict, sort_keys=True, default=str)
        computed_hash = hashlib.sha256(hash_input.encode()).hexdigest()
        # frozen dataclass requires object.__setattr__
        object.__setattr__(self, "hash", computed_hash)


@dataclass(frozen=True)
class Snapshot:
    """Immutable state snapshot at session boundary (ADR-0541)."""
    task_id: str
    tenant_id: str
    session_id: str
    snapshot_timestamp: str  # ISO 8601
    phase_completed: str
    events_count: int
    last_event_hash: str
    artifacts: List[str] = field(default_factory=list)
    state: Dict[str, Any] = field(default_factory=dict)

    # Crypto binding (ADR-0541 Fix 1.3)
    snapshot_hash: str = field(default="")
    signature: str = field(default="")

    def __post_init__(self):
        # Compute snapshot_hash
        snap_dict = asdict(self)
        snap_dict.pop("snapshot_hash", None)
        snap_dict.pop("signature", None)
        hash_input = json.dumps(snap_dict, sort_keys=True, default=str)
        computed_hash = hashlib.sha256(hash_input.encode()).hexdigest()
        object.__setattr__(self, "snapshot_hash", computed_hash)


@dataclass
class ExecutionResult:
    """Result of task execution."""
    success: bool
    task_id: str
    final_phase: Optional[str]
    audit_events: List[AuditEvent]
    snapshot: Optional[Snapshot]
    state_hash: str
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "task_id": self.task_id,
            "final_phase": self.final_phase,
            "state_hash": self.state_hash,
            "audit_events_count": len(self.audit_events),
            "error": self.error,
        }
