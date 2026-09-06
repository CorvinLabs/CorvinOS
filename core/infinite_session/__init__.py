"""Phase A: Infinite Session Engine — Foundation (ADR-0540).

Core components for session state persistence and task execution planning.

Modules:
- snapshot_schema: Immutable snapshot dataclass (Snapshot, SnapshotMetadata)
- task_def_parser: JSON-LD task definition parser (TaskDefParser, ExecutionPlan)
- event_store: Append-only snapshot storage (EventStore)
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

__all__ = [
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
]
