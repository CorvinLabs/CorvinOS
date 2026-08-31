"""Session Checkpoint Management — Cross-Session Task Continuation (ADR-0367).

Enables resuming long-running tasks across session boundaries.
Captures ExecutionContext state at natural checkpoints (every 5 turns or
on budget threshold) and provides resume mechanism for continuation.

Key Mechanisms:
- SessionCheckpoint: Serializable snapshot of ExecutionContext + decision history
- SessionContinuationManager: Persistence layer (JSONL append-only for auditability)
- Loss function: Context loss on restart → checkpoint prevents this
"""

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)


class SessionCheckpointError(Exception):
    """Base exception for checkpoint operations."""
    pass


class CheckpointNotFoundError(SessionCheckpointError):
    """Raised when checkpoint cannot be loaded."""
    pass


class CheckpointPersistenceError(SessionCheckpointError):
    """Raised when persisting checkpoint fails."""
    pass


@dataclass(frozen=True)
class SessionCheckpoint:
    """Serializable snapshot of ExecutionContext state.

    Captures everything needed to resume a task in a new session:
    - Current execution state (budget, time, strategy)
    - Decision history (all prior decisions)
    - Checkpoints (internal recovery points)
    - Error recovery state (optional)
    - Original goal for context-drift prevention (ADR-0405)

    ADR-0405: Goal is persisted to enable cross-session integrity validation.
    """

    # Identity
    checkpoint_id: str
    task_id: str
    session_id: str  # Original session
    tenant_id: str

    # Execution state
    context_state: Dict[str, Any]  # Serialized ExecutionContext fields
    decision_history: List[Dict[str, Any]] = field(default_factory=list)
    checkpoints: List[Dict[str, Any]] = field(default_factory=list)

    # Timing
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    last_activity_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    # Metadata
    turn_number: int = 0  # Which turn created this checkpoint
    tokens_consumed: int = 0  # Total tokens up to this checkpoint
    cost_consumed_cents: float = 0.0  # Total cost up to this checkpoint
    error_recovery_state: Optional[Dict[str, Any]] = None

    # Context drift prevention (ADR-0405)
    original_goal: Optional[str] = None  # Task goal for similarity validation on resume
    goal_alignment_score: float = 1.0  # Last measured similarity [0.0-1.0]

    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return asdict(self)

    def to_json(self) -> str:
        """Serialize to JSON (for persistence)."""
        return json.dumps(self.to_dict(), default=str)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SessionCheckpoint":
        """Reconstruct from dict."""
        return cls(**data)

    @classmethod
    def from_json(cls, json_str: str) -> "SessionCheckpoint":
        """Reconstruct from JSON."""
        return cls.from_dict(json.loads(json_str))


class SessionContinuationManager:
    """Manages checkpoint persistence and session continuation.

    Responsibilities:
    - Save checkpoints to disk (JSONL for auditability)
    - Load checkpoints for resume
    - Track checkpoint history per task
    - Clean up stale checkpoints (>90 days)
    """

    def __init__(self, corvin_home: Optional[str] = None, tenant_id: str = "_default"):
        """Initialize SessionContinuationManager.

        Args:
            corvin_home: Path to CORVIN_HOME. If None, uses environment variable.
            tenant_id: Tenant identifier for multi-tenant isolation (default: "_default").

        Raises:
            ValueError: If corvin_home not provided and env var not set.
            ValueError: If tenant_id is invalid.
        """
        import os

        if corvin_home is None:
            corvin_home = os.environ.get("CORVIN_HOME")
            if not corvin_home:
                raise ValueError(
                    "corvin_home not provided and CORVIN_HOME environment variable not set"
                )

        # Validate tenant_id (fail-closed)
        if not isinstance(tenant_id, str) or len(tenant_id.strip()) == 0:
            raise ValueError(f"Invalid tenant_id: {tenant_id}")

        self.corvin_home = Path(corvin_home)
        self.tenant_id = tenant_id
        self._checkpoint_base = self.corvin_home / "tenants" / tenant_id / "checkpoints"
        self._checkpoint_base.mkdir(parents=True, exist_ok=True)

    def save_checkpoint(
        self,
        task_id: str,
        tenant_id: str,
        execution_context: Any,  # ExecutionContext v2
        session_id: str,
        turn_number: int = 0,
        tokens_consumed: int = 0,
        cost_consumed_cents: float = 0.0,
        error_recovery_state: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Save a checkpoint for task continuation.

        Creates a SessionCheckpoint from ExecutionContext state and persists it.
        Saves to both:
        - {task_id}/latest.json (latest checkpoint, for fast access)
        - {task_id}/history.jsonl (append-only history)

        Args:
            task_id: Unique task identifier
            tenant_id: Tenant identifier
            execution_context: Current ExecutionContext v2 instance
            session_id: Current session ID
            turn_number: Which turn this checkpoint is for
            tokens_consumed: Total tokens consumed so far
            cost_consumed_cents: Total cost incurred so far
            error_recovery_state: Optional error recovery metadata

        Returns:
            checkpoint_id (UUID string)

        Raises:
            ValueError: If tenant_id doesn't match manager's tenant_id (fail-closed).
            CheckpointPersistenceError: If persistence fails
        """
        # Validate tenant_id matches (fail-closed on mismatch)
        if tenant_id != self.tenant_id:
            raise ValueError(
                f"tenant_id mismatch: got {tenant_id}, expected {self.tenant_id}. "
                f"Create a new SessionContinuationManager for tenant {tenant_id}."
            )

        try:
            checkpoint_id = str(uuid4())
            task_checkpoint_dir = self._checkpoint_base / task_id
            task_checkpoint_dir.mkdir(parents=True, exist_ok=True)

            # Serialize ExecutionContext state
            context_state = self._serialize_execution_context(execution_context)

            # Serialize decision history and checkpoints
            decision_history = [
                asdict(d) if hasattr(d, "__dataclass_fields__") else d
                for d in execution_context.decision_history
            ]
            checkpoints = execution_context.checkpoints or []

            # Create SessionCheckpoint
            checkpoint = SessionCheckpoint(
                checkpoint_id=checkpoint_id,
                task_id=task_id,
                session_id=session_id,
                tenant_id=tenant_id,
                context_state=context_state,
                decision_history=decision_history,
                checkpoints=checkpoints,
                turn_number=turn_number,
                tokens_consumed=tokens_consumed,
                cost_consumed_cents=cost_consumed_cents,
                error_recovery_state=error_recovery_state,
            )

            # Save to latest.json
            latest_path = task_checkpoint_dir / "latest.json"
            with open(latest_path, "w") as f:
                f.write(checkpoint.to_json())

            # Append to history.jsonl
            history_path = task_checkpoint_dir / "history.jsonl"
            with open(history_path, "a") as f:
                f.write(checkpoint.to_json() + "\n")

            logger.info(
                f"Saved checkpoint '{checkpoint_id}' for task '{task_id}' "
                f"at turn {turn_number}"
            )
            return checkpoint_id

        except Exception as e:
            logger.error(f"Failed to save checkpoint for task '{task_id}': {e}")
            raise CheckpointPersistenceError(
                f"Failed to save checkpoint: {e}"
            ) from e

    def load_checkpoint(
        self,
        task_id: str,
        checkpoint_id: Optional[str] = None,
    ) -> SessionCheckpoint:
        """Load a checkpoint.

        If checkpoint_id is None, loads the latest checkpoint.

        Args:
            task_id: Unique task identifier
            checkpoint_id: Optional checkpoint ID. If None, loads latest.

        Returns:
            SessionCheckpoint instance

        Raises:
            CheckpointNotFoundError: If checkpoint not found
        """
        try:
            task_checkpoint_dir = self._checkpoint_base / task_id

            if not task_checkpoint_dir.exists():
                raise CheckpointNotFoundError(
                    f"No checkpoints found for task '{task_id}'"
                )

            # Load latest checkpoint if no ID specified
            if checkpoint_id is None:
                latest_path = task_checkpoint_dir / "latest.json"
                if not latest_path.exists():
                    raise CheckpointNotFoundError(
                        f"No latest checkpoint for task '{task_id}'"
                    )
                with open(latest_path) as f:
                    checkpoint = SessionCheckpoint.from_json(f.read())
            else:
                # Load specific checkpoint from history
                history_path = task_checkpoint_dir / "history.jsonl"
                if not history_path.exists():
                    raise CheckpointNotFoundError(
                        f"Checkpoint history not found for task '{task_id}'"
                    )

                checkpoint = None
                with open(history_path) as f:
                    for line in f:
                        cp = SessionCheckpoint.from_json(line)
                        if cp.checkpoint_id == checkpoint_id:
                            checkpoint = cp
                            break

                if checkpoint is None:
                    raise CheckpointNotFoundError(
                        f"Checkpoint '{checkpoint_id}' not found for task '{task_id}'"
                    )

            logger.info(
                f"Loaded checkpoint '{checkpoint.checkpoint_id}' for task '{task_id}' "
                f"from turn {checkpoint.turn_number}"
            )
            return checkpoint

        except CheckpointNotFoundError:
            raise
        except Exception as e:
            logger.error(f"Failed to load checkpoint for task '{task_id}': {e}")
            raise CheckpointNotFoundError(
                f"Failed to load checkpoint: {e}"
            ) from e

    def get_checkpoint_metadata(self, task_id: str) -> List[Dict[str, Any]]:
        """Get metadata for all checkpoints of a task.

        Returns list of checkpoint metadata (id, turn, timestamp, tokens, cost).

        Args:
            task_id: Unique task identifier

        Returns:
            List of checkpoint metadata dicts
        """
        metadata = []
        task_checkpoint_dir = self._checkpoint_base / task_id
        history_path = task_checkpoint_dir / "history.jsonl"

        if not history_path.exists():
            return metadata

        try:
            with open(history_path) as f:
                for line in f:
                    cp = SessionCheckpoint.from_json(line)
                    metadata.append({
                        "checkpoint_id": cp.checkpoint_id,
                        "turn_number": cp.turn_number,
                        "created_at": cp.created_at,
                        "tokens_consumed": cp.tokens_consumed,
                        "cost_consumed_cents": cp.cost_consumed_cents,
                    })
        except Exception as e:
            logger.error(f"Failed to read checkpoint metadata for '{task_id}': {e}")

        return metadata

    def resume_from_checkpoint(
        self,
        checkpoint: SessionCheckpoint,
        execution_context_cls: Any,  # ExecutionContext v2 class
    ) -> Any:
        """Reconstruct ExecutionContext from checkpoint for resuming task.

        Args:
            checkpoint: SessionCheckpoint to resume from
            execution_context_cls: ExecutionContext class for reconstruction

        Returns:
            New ExecutionContext instance with restored state

        Raises:
            SessionCheckpointError: If reconstruction fails
        """
        try:
            # Reconstruct ContextStack from serialized state
            from .execution_context import ContextStack, ContextStackFrame

            stack_str = checkpoint.context_state.get("context_stack", "root")
            context_stack = ContextStack()

            # Parse stack string back to frames (with graceful error handling)
            if stack_str and stack_str != "root":
                try:
                    for frame_str in stack_str.split(" → "):
                        if not frame_str.strip():
                            continue
                        # Simple parsing: "level:id" or "level:id [metadata]"
                        parts = frame_str.split(":", 1)
                        if len(parts) == 2:
                            level, id_part = parts
                            # Extract id and metadata
                            if "[" in id_part:
                                id_val, meta_str = id_part.split("[", 1)
                                meta_str = meta_str.rstrip("]")
                                metadata = {}
                                for pair in meta_str.split():
                                    if "=" in pair:
                                        k, v = pair.split("=", 1)
                                        metadata[k] = v
                                context_stack.push(level.strip(), id_val.strip(), **metadata)
                            else:
                                context_stack.push(level.strip(), id_part.strip())
                except Exception as e:
                    logger.warning(
                        f"Failed to parse context stack '{stack_str}' from checkpoint: {e}. "
                        f"Resuming with empty stack."
                    )
                    context_stack = ContextStack()

            # Reconstruct ExecutionContext
            ctx = execution_context_cls(
                task_id=checkpoint.task_id,
                tenant_id=checkpoint.tenant_id,
                task_template=checkpoint.context_state.get("task_template", {}),
                context_stack=context_stack,
                budget_remaining=checkpoint.context_state.get("budget_remaining", 0.0),
                time_remaining=checkpoint.context_state.get("time_remaining", 0),
                model=checkpoint.context_state.get("model", ""),
                strategy=checkpoint.context_state.get("strategy", ""),
                strategy_confidence=checkpoint.context_state.get("strategy_confidence", 0.5),
                guidance_overrides=checkpoint.context_state.get("guidance_overrides", {}),
                checkpoints=checkpoint.checkpoints,
            )

            # Restore decision history (with type safety)
            from .decision_record import DecisionRecord
            for dh in checkpoint.decision_history:
                try:
                    if isinstance(dh, dict):
                        # Safely reconstruct from dict, ignoring extra keys
                        valid_fields = {
                            k: v
                            for k, v in dh.items()
                            if k in ["timestamp", "subsystem", "decision_type", "value",
                                    "reasoning", "context_stack", "confidence", "guidance_applied"]
                        }
                        dr = DecisionRecord(**valid_fields)
                    elif isinstance(dh, DecisionRecord):
                        dr = dh
                    else:
                        logger.warning(
                            f"Skipping decision history entry of unknown type: {type(dh)}"
                        )
                        continue
                    ctx.decision_history.append(dr)
                except Exception as e:
                    logger.error(f"Failed to restore decision history entry: {e}")
                    continue

            logger.info(
                f"Resumed ExecutionContext for task '{checkpoint.task_id}' "
                f"from checkpoint '{checkpoint.checkpoint_id}' at turn {checkpoint.turn_number}"
            )
            return ctx

        except Exception as e:
            logger.error(f"Failed to resume from checkpoint: {e}")
            raise SessionCheckpointError(f"Failed to resume from checkpoint: {e}") from e

    @staticmethod
    def _serialize_execution_context(execution_context: Any) -> Dict[str, Any]:
        """Extract serializable state from ExecutionContext.

        Args:
            execution_context: ExecutionContext v2 instance

        Returns:
            Dict of serializable fields
        """
        return {
            "task_id": execution_context.task_id,
            "tenant_id": execution_context.tenant_id,
            "task_template": execution_context.task_template,
            "context_stack": str(execution_context.context_stack),
            "budget_remaining": execution_context.budget_remaining,
            "time_remaining": execution_context.time_remaining,
            "model": execution_context.model,
            "strategy": execution_context.strategy,
            "strategy_confidence": execution_context.strategy_confidence,
            "guidance_overrides": execution_context.guidance_overrides,
        }
