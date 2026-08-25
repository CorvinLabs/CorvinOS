"""CheckpointManager: Serializes task state to JSON for idempotent resumption.

k=2: CheckpointManager with JSON serialization
- Captures complete task state
- Enables idempotent session resumption
- Includes learning state and context essentials

ADR-0XXX: Session Manager Architecture
GDPR Art. 30, 32: Checkpoint creation is audit-logged.
"""

import json
import logging
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Any, Dict
from uuid import uuid4

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TaskState:
    """Immutable task state snapshot."""

    task_id: str
    goal: str
    constraints: List[str] = field(default_factory=list)
    user_intent: str = ""
    progress_summary: str = ""


@dataclass(frozen=True)
class SubgoalRecord:
    """Immutable record of a subgoal."""

    description: str
    status: str  # "pending", "in_progress", "completed", "failed"
    work_done: str = ""
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass(frozen=True)
class ArtifactRecord:
    """Immutable record of a generated artifact."""

    name: str
    path: str
    essential: bool = True
    reason: str = ""


@dataclass(frozen=True)
class LearningState:
    """Immutable learning state snapshot."""

    strategies_tried: List[str] = field(default_factory=list)
    success_rate: float = 0.0
    errors_encountered: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class ContextEssentials:
    """Immutable context essentials for restoration.

    Tier 0 (Keep ✅): Goal, constraints, validated findings, error patterns
    Tier 1 (Keep ✅): Strategies, phase, artifacts
    Tier 2 (Drop ❌): Intermediate attempts, stale approaches
    Tier 3 (Drop ❌): Debug logs, micro-step transcripts
    """

    kept_items: List[str] = field(default_factory=list)  # Tier 0-1
    dropped_items: List[str] = field(default_factory=list)  # Tier 2-3
    reduction_percentage: float = 0.0  # E.g., 91% reduction


@dataclass(frozen=True)
class SessionCheckpoint:
    """Immutable session checkpoint for resumption.

    Can be serialized to JSON and restored idempotently.
    """

    # Metadata
    checkpoint_id: str = field(default_factory=lambda: str(uuid4()))
    session_id: str = ""
    task_id: str = ""
    phase: str = ""
    tenant_id: str = ""
    created_at: datetime = field(default_factory=datetime.utcnow)
    trigger_type: str = ""  # Split trigger that created this checkpoint
    iterations_at_checkpoint: int = 0
    token_count_at_checkpoint: int = 0

    # Task State
    task_state: Optional[TaskState] = None

    # Open Subgoals
    open_subgoals: List[SubgoalRecord] = field(default_factory=list)

    # Artifacts
    artifacts: List[ArtifactRecord] = field(default_factory=list)

    # Learning State
    learning_state: Optional[LearningState] = None

    # Context Essentials
    context_essentials: Optional[ContextEssentials] = None

    def __post_init__(self):
        """Validate checkpoint."""
        if not self.session_id:
            raise ValueError("session_id is required")
        if not self.task_id:
            raise ValueError("task_id is required")
        if not self.phase:
            raise ValueError("phase is required")
        if not self.tenant_id:
            raise ValueError("tenant_id is required")

    def to_dict(self) -> dict[str, Any]:
        """Convert checkpoint to dictionary (JSON-serializable)."""
        return {
            "checkpoint_id": self.checkpoint_id,
            "session_id": self.session_id,
            "task_id": self.task_id,
            "phase": self.phase,
            "tenant_id": self.tenant_id,
            "created_at": self.created_at.isoformat() + "Z",
            "trigger_type": self.trigger_type,
            "iterations_at_checkpoint": self.iterations_at_checkpoint,
            "token_count_at_checkpoint": self.token_count_at_checkpoint,
            "task_state": asdict(self.task_state) if self.task_state else None,
            "open_subgoals": [asdict(sg) for sg in self.open_subgoals],
            "artifacts": [asdict(art) for art in self.artifacts],
            "learning_state": asdict(self.learning_state) if self.learning_state else None,
            "context_essentials": asdict(self.context_essentials) if self.context_essentials else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SessionCheckpoint":
        """Reconstruct checkpoint from dictionary."""
        # Parse nested objects
        if data.get("created_at"):
            created_at = datetime.fromisoformat(data["created_at"].rstrip("Z"))
        else:
            created_at = datetime.utcnow()

        task_state = None
        if data.get("task_state"):
            ts = data["task_state"]
            task_state = TaskState(
                task_id=ts.get("task_id", ""),
                goal=ts.get("goal", ""),
                constraints=ts.get("constraints", []),
                user_intent=ts.get("user_intent", ""),
                progress_summary=ts.get("progress_summary", ""),
            )

        open_subgoals = []
        for sg in data.get("open_subgoals", []):
            sg_ts = datetime.fromisoformat(sg.get("timestamp", "").rstrip("Z")) if sg.get("timestamp") else datetime.utcnow()
            open_subgoals.append(
                SubgoalRecord(
                    description=sg.get("description", ""),
                    status=sg.get("status", "pending"),
                    work_done=sg.get("work_done", ""),
                    timestamp=sg_ts,
                )
            )

        artifacts = []
        for art in data.get("artifacts", []):
            artifacts.append(
                ArtifactRecord(
                    name=art.get("name", ""),
                    path=art.get("path", ""),
                    essential=art.get("essential", True),
                    reason=art.get("reason", ""),
                )
            )

        learning_state = None
        if data.get("learning_state"):
            ls = data["learning_state"]
            learning_state = LearningState(
                strategies_tried=ls.get("strategies_tried", []),
                success_rate=ls.get("success_rate", 0.0),
                errors_encountered=ls.get("errors_encountered", []),
                recommendations=ls.get("recommendations", []),
            )

        context_essentials = None
        if data.get("context_essentials"):
            ce = data["context_essentials"]
            context_essentials = ContextEssentials(
                kept_items=ce.get("kept_items", []),
                dropped_items=ce.get("dropped_items", []),
                reduction_percentage=ce.get("reduction_percentage", 0.0),
            )

        return cls(
            checkpoint_id=data.get("checkpoint_id", str(uuid4())),
            session_id=data.get("session_id", ""),
            task_id=data.get("task_id", ""),
            phase=data.get("phase", ""),
            tenant_id=data.get("tenant_id", ""),
            created_at=created_at,
            trigger_type=data.get("trigger_type", ""),
            iterations_at_checkpoint=data.get("iterations_at_checkpoint", 0),
            token_count_at_checkpoint=data.get("token_count_at_checkpoint", 0),
            task_state=task_state,
            open_subgoals=open_subgoals,
            artifacts=artifacts,
            learning_state=learning_state,
            context_essentials=context_essentials,
        )

    def to_audit_event(self) -> dict[str, Any]:
        """Convert to audit.jsonl format (GDPR Art. 30, 32)."""
        return {
            "event_type": "session.checkpoint_created",
            "tenant_id": self.tenant_id,
            "checkpoint_id": self.checkpoint_id,
            "session_id": self.session_id,
            "task_id": self.task_id,
            "phase": self.phase,
            "trigger_type": self.trigger_type,
            "timestamp": self.created_at.isoformat() + "Z",
            "state_summary": {
                "iterations": self.iterations_at_checkpoint,
                "token_count": self.token_count_at_checkpoint,
                "subgoals_open": len(self.open_subgoals),
                "artifacts": len(self.artifacts),
            },
        }


class CheckpointManager:
    """Manages session checkpoints for idempotent resumption.

    Features:
    - Serialize complete task state to JSON
    - Restore checkpoints for session resumption
    - Track checkpoint history per task
    - Audit logging per GDPR Art. 30, 32
    """

    def __init__(self, checkpoint_dir: Optional[Path] = None, hub: Optional[Any] = None):
        """Initialize CheckpointManager.

        Args:
            checkpoint_dir: Directory for storing checkpoint files. If None, uses memory only.
            hub: Optional SubsystemHub for event publishing.
        """
        self.name = "checkpoint_manager"
        self.version = "0.1.0"
        self.checkpoint_dir = checkpoint_dir
        self.hub = hub
        self.checkpoints: Dict[str, SessionCheckpoint] = {}  # In-memory cache
        self.checkpoint_history: Dict[str, List[str]] = {}  # task_id -> [checkpoint_id, ...]

        if self.checkpoint_dir:
            self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"Checkpoint directory: {self.checkpoint_dir}")

    def startup(self, hub: Any) -> None:
        """Register with SubsystemHub.

        Args:
            hub: SubsystemHub instance
        """
        self.hub = hub
        logger.info(f"Starting {self.name} v{self.version}")

    def shutdown(self) -> None:
        """Clean up on shutdown."""
        logger.info(f"Shutting down {self.name}")

    def create_checkpoint(
        self,
        session_id: str,
        task_id: str,
        phase: str,
        tenant_id: str,
        trigger_type: str = "",
        iterations: int = 0,
        token_count: int = 0,
        task_state: Optional[TaskState] = None,
        open_subgoals: Optional[List[SubgoalRecord]] = None,
        artifacts: Optional[List[ArtifactRecord]] = None,
        learning_state: Optional[LearningState] = None,
        context_essentials: Optional[ContextEssentials] = None,
    ) -> SessionCheckpoint:
        """Create a new checkpoint.

        Args:
            session_id: Current session ID
            task_id: Task identifier
            phase: Phase name
            tenant_id: Tenant identifier (GDPR Art. 5)
            trigger_type: What triggered this checkpoint
            iterations: Iteration count at checkpoint
            token_count: Context size in tokens
            task_state: Current task state
            open_subgoals: List of open subgoals
            artifacts: List of generated artifacts
            learning_state: Learning state snapshot
            context_essentials: Context essentials for restoration

        Returns:
            SessionCheckpoint
        """
        checkpoint = SessionCheckpoint(
            session_id=session_id,
            task_id=task_id,
            phase=phase,
            tenant_id=tenant_id,
            trigger_type=trigger_type,
            iterations_at_checkpoint=iterations,
            token_count_at_checkpoint=token_count,
            task_state=task_state,
            open_subgoals=open_subgoals or [],
            artifacts=artifacts or [],
            learning_state=learning_state,
            context_essentials=context_essentials,
        )

        # Store in memory cache
        self.checkpoints[checkpoint.checkpoint_id] = checkpoint

        # Track history
        if task_id not in self.checkpoint_history:
            self.checkpoint_history[task_id] = []
        self.checkpoint_history[task_id].append(checkpoint.checkpoint_id)

        logger.info(
            f"Created checkpoint {checkpoint.checkpoint_id} for session {session_id} "
            f"(iterations={iterations}, tokens={token_count})"
        )

        # Persist to disk if configured
        if self.checkpoint_dir:
            self._persist_checkpoint(checkpoint)

        # Audit log
        self._audit_log_checkpoint(checkpoint)

        return checkpoint

    def get_checkpoint(self, checkpoint_id: str) -> Optional[SessionCheckpoint]:
        """Get a checkpoint by ID.

        Args:
            checkpoint_id: Checkpoint identifier

        Returns:
            SessionCheckpoint if found, None otherwise
        """
        return self.checkpoints.get(checkpoint_id)

    def get_latest_checkpoint(self, task_id: str) -> Optional[SessionCheckpoint]:
        """Get the latest checkpoint for a task.

        Args:
            task_id: Task identifier

        Returns:
            Latest SessionCheckpoint for task, or None if none exist
        """
        if task_id not in self.checkpoint_history:
            return None

        checkpoint_ids = self.checkpoint_history[task_id]
        if not checkpoint_ids:
            return None

        latest_id = checkpoint_ids[-1]
        return self.checkpoints.get(latest_id)

    def list_checkpoints_for_task(self, task_id: str) -> List[SessionCheckpoint]:
        """Get all checkpoints for a task.

        Args:
            task_id: Task identifier

        Returns:
            List of SessionCheckpoints (in chronological order)
        """
        if task_id not in self.checkpoint_history:
            return []

        checkpoints = []
        for checkpoint_id in self.checkpoint_history[task_id]:
            cp = self.checkpoints.get(checkpoint_id)
            if cp:
                checkpoints.append(cp)

        return checkpoints

    def restore_checkpoint(self, checkpoint_id: str) -> Optional[SessionCheckpoint]:
        """Restore a checkpoint for resumption.

        Can be called to resume from a specific checkpoint.

        Args:
            checkpoint_id: Checkpoint to restore

        Returns:
            SessionCheckpoint if found, None otherwise
        """
        checkpoint = self.checkpoints.get(checkpoint_id)
        if not checkpoint:
            # Try loading from disk
            if self.checkpoint_dir:
                checkpoint = self._load_checkpoint_from_disk(checkpoint_id)

        if checkpoint:
            logger.info(f"Restored checkpoint {checkpoint_id} for session {checkpoint.session_id}")
            return checkpoint

        logger.warning(f"Checkpoint {checkpoint_id} not found")
        return None

    def _persist_checkpoint(self, checkpoint: SessionCheckpoint) -> None:
        """Save checkpoint to disk as JSON.

        Args:
            checkpoint: SessionCheckpoint to persist
        """
        if not self.checkpoint_dir:
            return

        filepath = self.checkpoint_dir / f"{checkpoint.checkpoint_id}.json"
        try:
            with open(filepath, "w") as f:
                json.dump(checkpoint.to_dict(), f, indent=2, default=str)
            logger.debug(f"Persisted checkpoint to {filepath}")
        except Exception as e:
            logger.error(f"Failed to persist checkpoint {checkpoint.checkpoint_id}: {e}")

    def _load_checkpoint_from_disk(self, checkpoint_id: str) -> Optional[SessionCheckpoint]:
        """Load checkpoint from disk.

        Args:
            checkpoint_id: Checkpoint identifier

        Returns:
            SessionCheckpoint if found and valid, None otherwise
        """
        if not self.checkpoint_dir:
            return None

        filepath = self.checkpoint_dir / f"{checkpoint_id}.json"
        try:
            with open(filepath, "r") as f:
                data = json.load(f)
            checkpoint = SessionCheckpoint.from_dict(data)
            self.checkpoints[checkpoint_id] = checkpoint
            return checkpoint
        except Exception as e:
            logger.error(f"Failed to load checkpoint {checkpoint_id} from disk: {e}")
            return None

    def _audit_log_checkpoint(self, checkpoint: SessionCheckpoint) -> None:
        """Log checkpoint creation to audit trail (GDPR Art. 30, 32).

        Args:
            checkpoint: SessionCheckpoint to audit
        """
        audit_dict = checkpoint.to_audit_event()
        logger.info(f"AUDIT: {audit_dict}")

        # Publish via hub if available
        if self.hub:
            try:
                self.hub.publish_event("session.checkpoint_created", audit_dict)
            except Exception as e:
                logger.error(f"Failed to publish checkpoint event: {e}")
