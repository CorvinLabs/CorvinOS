"""ExecutionContext — serializable task execution state (Phase 0).

Captures all context needed to:
1. Resume interrupted tasks
2. Replay tasks deterministically
3. Merge state across systems (CRDT operations)
4. Audit task execution history
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Any, Optional
from uuid import uuid4


class ExecutionState(str, Enum):
    """Task execution state."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class ExecutionContext:
    """Immutable execution context for reproducibility.

    Captures all state needed to:
    - Resume interrupted tasks
    - Replay deterministically
    - Audit execution
    - Debug failures
    """

    # Task identity
    task_id: str
    tenant_id: str
    session_id: str
    user_id: Optional[str] = None

    # Execution state
    state: ExecutionState = ExecutionState.PENDING
    engine_type: Optional[str] = None
    priority: int = 5  # 1-10, higher = more urgent

    # Input
    task_type: str = "general"
    system_prompt: Optional[str] = None
    user_prompt: str = ""
    temperature: float = 0.7
    max_tokens: int = 2048

    # Execution metadata
    start_time: Optional[str] = None  # ISO 8601
    end_time: Optional[str] = None
    duration_ms: int = 0

    # Results
    output: Optional[str] = None
    tokens_input: int = 0
    tokens_output: int = 0
    cost_cents: int = 0
    quality_score: float = 0.0

    # Error tracking
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    attempt_count: int = 0
    max_attempts: int = 3

    # Audit trail
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    modified_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    audit_hash: Optional[str] = None  # Hash for chain verification

    # Learning signals
    learning_event_id: Optional[str] = None  # Reference to learning event
    operator_feedback: Optional[str] = None

    # Metadata
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        """Serialize to JSON (deterministic for hashing)."""
        data = asdict(self)
        return json.dumps(data, sort_keys=True, separators=(",", ":"))

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)

    @classmethod
    def from_json(cls, json_str: str) -> ExecutionContext:
        """Deserialize from JSON."""
        data = json.loads(json_str)
        return cls(**data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExecutionContext:
        """Create from dictionary."""
        return cls(**data)

    def mark_started(self, engine_type: str) -> ExecutionContext:
        """Mark context as started."""
        return ExecutionContext(
            **{
                **asdict(self),
                "state": ExecutionState.IN_PROGRESS,
                "engine_type": engine_type,
                "start_time": datetime.utcnow().isoformat(),
                "attempt_count": self.attempt_count + 1,
                "modified_at": datetime.utcnow().isoformat(),
            }
        )

    def mark_completed(
        self,
        output: str,
        tokens_input: int,
        tokens_output: int,
        cost_cents: int,
        quality_score: float,
    ) -> ExecutionContext:
        """Mark context as completed with results."""
        end_time = datetime.utcnow()
        start = datetime.fromisoformat(self.start_time) if self.start_time else end_time
        duration_ms = int((end_time - start).total_seconds() * 1000)

        return ExecutionContext(
            **{
                **asdict(self),
                "state": ExecutionState.COMPLETED,
                "output": output,
                "tokens_input": tokens_input,
                "tokens_output": tokens_output,
                "cost_cents": cost_cents,
                "quality_score": quality_score,
                "end_time": end_time.isoformat(),
                "duration_ms": duration_ms,
                "modified_at": end_time.isoformat(),
            }
        )

    def mark_failed(self, error_type: str, error_message: str) -> ExecutionContext:
        """Mark context as failed."""
        return ExecutionContext(
            **{
                **asdict(self),
                "state": ExecutionState.FAILED,
                "error_type": error_type,
                "error_message": error_message,
                "end_time": datetime.utcnow().isoformat(),
                "modified_at": datetime.utcnow().isoformat(),
            }
        )

    def mark_paused(self) -> ExecutionContext:
        """Mark context as paused (for resumption later)."""
        return ExecutionContext(
            **{
                **asdict(self),
                "state": ExecutionState.PAUSED,
                "modified_at": datetime.utcnow().isoformat(),
            }
        )

    def add_learning_signal(self, learning_event_id: str, feedback: Optional[str] = None) -> ExecutionContext:
        """Add learning signal from operator feedback."""
        return ExecutionContext(
            **{
                **asdict(self),
                "learning_event_id": learning_event_id,
                "operator_feedback": feedback,
                "modified_at": datetime.utcnow().isoformat(),
            }
        )

    def is_retryable(self) -> bool:
        """Check if task can be retried."""
        return (
            self.state in [ExecutionState.FAILED, ExecutionState.PAUSED]
            and self.attempt_count < self.max_attempts
        )

    def get_retry_context(self) -> ExecutionContext:
        """Get a fresh context for retry."""
        return ExecutionContext(
            **{
                **asdict(self),
                "state": ExecutionState.PENDING,
                "output": None,
                "error_type": None,
                "error_message": None,
                "tokens_input": 0,
                "tokens_output": 0,
                "cost_cents": 0,
                "quality_score": 0.0,
                "start_time": None,
                "end_time": None,
                "duration_ms": 0,
                "modified_at": datetime.utcnow().isoformat(),
            }
        )

    def compute_hash(self) -> str:
        """Compute SHA256 hash for audit chain."""
        import hashlib
        return hashlib.sha256(self.to_json().encode()).hexdigest()


@dataclass
class ExecutionContextUpdate:
    """Atomic update to ExecutionContext (for CRDT merging)."""

    context_id: str
    update_id: str = field(default_factory=lambda: str(uuid4()))
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    operation: str = ""  # "mark_started", "mark_completed", etc.
    fields: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        """Serialize to JSON."""
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_json(cls, json_str: str) -> ExecutionContextUpdate:
        """Deserialize from JSON."""
        data = json.loads(json_str)
        return cls(**data)


class ExecutionContextStore:
    """In-memory store for ExecutionContext (phase 0 only, v1 uses persistent DB)."""

    def __init__(self):
        self.contexts: dict[str, ExecutionContext] = {}
        self.updates: list[ExecutionContextUpdate] = []

    def save(self, context: ExecutionContext) -> None:
        """Save context."""
        self.contexts[context.task_id] = context

    def load(self, task_id: str) -> Optional[ExecutionContext]:
        """Load context."""
        return self.contexts.get(task_id)

    def delete(self, task_id: str) -> None:
        """Delete context."""
        self.contexts.pop(task_id, None)

    def list_all(self, tenant_id: Optional[str] = None) -> list[ExecutionContext]:
        """List all contexts, optionally filtered by tenant."""
        contexts = list(self.contexts.values())
        if tenant_id:
            contexts = [c for c in contexts if c.tenant_id == tenant_id]
        return contexts

    def record_update(self, update: ExecutionContextUpdate) -> None:
        """Record an update for audit trail."""
        self.updates.append(update)

    def get_updates_for(self, task_id: str) -> list[ExecutionContextUpdate]:
        """Get all updates for a specific context."""
        return [u for u in self.updates if u.context_id == task_id]
