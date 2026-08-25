"""Session Manager for autonomous multi-phase task management.

Phase 2.1 Core: 4 Core Subsystems
- SessionLifecycleManager: Detects 6 split-triggers, initiates checkpoints
- CheckpointManager: Serializes task state to JSON
- ContextReducer: 91% context reduction (200k → 18k tokens)
- RecoveryEngine: 4 recovery patterns (Replay, Adapt, Backtrack, Pause)

ADR-0XXX: Session Manager Architecture (Phase 2.1)
Depends on: ADR-0347 (Hub), ADR-0348 (EventBus), ADR-0399 (Context-Pipeline v2)
"""

from .lifecycle import SessionLifecycleManager, SessionSplitTrigger
from .checkpoint import CheckpointManager, SessionCheckpoint
from .context_reducer import ContextReducer, ContextTier
from .recovery import RecoveryEngine, RecoveryPattern

__all__ = [
    "SessionLifecycleManager",
    "SessionSplitTrigger",
    "CheckpointManager",
    "SessionCheckpoint",
    "ContextReducer",
    "ContextTier",
    "RecoveryEngine",
    "RecoveryPattern",
]
