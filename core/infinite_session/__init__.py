"""Infinite Session Engine (ADR-0540–0541).

Phase A: Foundation
- snapshot_schema: Immutable snapshot dataclass (Snapshot, SnapshotMetadata)
- task_def_parser: JSON-LD task definition parser (TaskDefParser, ExecutionPlan)
- event_store: Append-only snapshot storage (EventStore)

Phase B: Session Bridging + Crypto Signatures (ADR-0541)
- crypto_binding: HMAC-SHA256 cryptographic binding (CryptoBinding)
- session_bridger: Session-to-session bridging (SessionBridger, SessionBridgeEvent)
- audit_verification: Audit chain verification (AuditVerifier, VerificationResult)
"""

from core.infinite_session.snapshot_schema import (
    Snapshot,
    SnapshotType,
    SnapshotMetadata,
)
from core.infinite_session.task_def_parser import (
    TaskDefParser,
    ExecutionPlan,
    Phase,
    Gate,
    AutonomyLevel,
    GateType,
)
from core.infinite_session.event_store import EventStore
from core.infinite_session.crypto_binding import (
    CryptoBinding,
    SignatureMetadata,
    KeyRotationStatus,
)
from core.infinite_session.session_bridger import (
    SessionBridger,
    SessionBridgeEvent,
)
from core.infinite_session.audit_verification import (
    AuditVerifier,
    VerificationResult,
    VerificationStatus,
)

__all__ = [
    # Phase A
    "Snapshot",
    "SnapshotType",
    "SnapshotMetadata",
    "TaskDefParser",
    "ExecutionPlan",
    "Phase",
    "Gate",
    "AutonomyLevel",
    "GateType",
    "EventStore",
    # Phase B
    "CryptoBinding",
    "SignatureMetadata",
    "KeyRotationStatus",
    "SessionBridger",
    "SessionBridgeEvent",
    "AuditVerifier",
    "VerificationResult",
    "VerificationStatus",
]
