"""Infinite Session Engine (ADR-0540–0542).

Phase A: Foundation
- snapshot_schema: Immutable snapshot dataclass (Snapshot, SnapshotMetadata)
- task_def_parser: JSON-LD task definition parser (TaskDefParser, ExecutionPlan)
- event_store: Append-only snapshot storage (EventStore)

Phase B: Session Bridging + Crypto Signatures (ADR-0541)
- crypto_binding: HMAC-SHA256 cryptographic binding (CryptoBinding)
- session_bridger: Session-to-session bridging (SessionBridger, SessionBridgeEvent)
- audit_verification: Audit chain verification (AuditVerifier, VerificationResult)

Phase C: Rollback Atomicity + Drift Detection (ADR-0542)
- rollback_manager: WAL-based rollback with a keyed hash chain (RollbackManager, TransactionLog)
- ema_smoother: Exponential Moving Average filter (EMASmoother, EMASample, DriftLevel)
- drift_detector: Drift-detection gates and revert button (DriftDetector, DriftAlert)

Every store is TENANT-BOUND (constructed for one tenant, re-checked per call),
every id is validated against ``paths.ID_PATTERN``, every root is derived from
``core.paths.tenant.corvin_home()``. ``snapshot_task_state`` is the producer
entry point (see docs/claude-ref/infinite-session.md).
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
from core.infinite_session.event_store import EventStore, snapshot_task_state
from core.infinite_session.paths import ID_PATTERN, InvalidIdentifier, PathEscape, validate_id
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
from core.infinite_session.rollback_manager import (
    RollbackManager,
    TransactionLog,
    TransactionStatus,
)
from core.infinite_session.ema_smoother import (
    EMASmoother,
    EMASample,
    DriftLevel,
)
from core.infinite_session.drift_detector import (
    DriftDetector,
    DriftAlert,
    DriftAssessment,
    DriftGateType,
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
    "snapshot_task_state",
    "ID_PATTERN",
    "InvalidIdentifier",
    "PathEscape",
    "validate_id",
    # Phase B
    "CryptoBinding",
    "SignatureMetadata",
    "KeyRotationStatus",
    "SessionBridger",
    "SessionBridgeEvent",
    "AuditVerifier",
    "VerificationResult",
    "VerificationStatus",
    # Phase C
    "RollbackManager",
    "TransactionLog",
    "TransactionStatus",
    "EMASmoother",
    "EMASample",
    "DriftLevel",
    "DriftDetector",
    "DriftAlert",
    "DriftAssessment",
    "DriftGateType",
]
