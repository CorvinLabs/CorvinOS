"""Phase 3: State Contract Layer for CorvinOS Integration.

Serializable TaskContext + CheckpointState for distributed execution.
No lambdas, no async generators, pure JSON-safe dataclasses.
"""

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Dict, List, Any, Optional
from abc import ABC, abstractmethod

@dataclass
class SerializableTaskProgress:
    """JSON-safe task progress (Phase 3)."""
    items_completed: int = 0
    total_items: int = 0
    error_count: int = 0
    strategy_count: int = 0
    learning_count: int = 0

    def to_dict(self) -> Dict:
        return asdict(self)

@dataclass
class SerializableTaskContext:
    """JSON-safe task context for CorvinOS state layer.

    All fields must be serializable (no lambdas, no async generators, no objects).
    """
    task_id: str
    goal: str
    persona_id: str
    task_type: str
    item_count: int
    created_at_iso: str  # ISO 8601

    # Mutable state
    progress: SerializableTaskProgress = field(default_factory=SerializableTaskProgress)
    current_skill: Optional[str] = None
    fallback_skills: List[str] = field(default_factory=list)

    # Enriched (computed from memory/skills)
    strategy_weights: Dict[str, float] = field(default_factory=dict)
    available_skills: List[str] = field(default_factory=list)
    recalled_memory_ids: List[str] = field(default_factory=list)

    # Session metadata
    artifacts: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict:
        """Serialize to CorvinOS state layer (pure JSON)."""
        return {
            "task_id": self.task_id,
            "goal": self.goal,
            "persona_id": self.persona_id,
            "task_type": self.task_type,
            "item_count": self.item_count,
            "created_at": self.created_at_iso,
            "progress": self.progress.to_dict(),
            "current_skill": self.current_skill,
            "fallback_skills": self.fallback_skills,
            "strategy_weights": self.strategy_weights,
            "available_skills": self.available_skills,
            "artifacts": self.artifacts
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "SerializableTaskContext":
        """Deserialize from CorvinOS state layer."""
        progress_data = data.get("progress", {})
        return cls(
            task_id=data["task_id"],
            goal=data["goal"],
            persona_id=data["persona_id"],
            task_type=data.get("task_type", "generic"),
            item_count=data.get("item_count", 0),
            created_at_iso=data.get("created_at", datetime.now().isoformat()),
            progress=SerializableTaskProgress(
                items_completed=progress_data.get("items_completed", 0),
                total_items=progress_data.get("total_items", 0),
                error_count=progress_data.get("error_count", 0),
                strategy_count=progress_data.get("strategy_count", 0),
                learning_count=progress_data.get("learning_count", 0)
            ),
            current_skill=data.get("current_skill"),
            fallback_skills=data.get("fallback_skills", []),
            strategy_weights=data.get("strategy_weights", {}),
            available_skills=data.get("available_skills", []),
            recalled_memory_ids=data.get("recalled_memory_ids", []),
            artifacts=data.get("artifacts", [])
        )

@dataclass
class CheckpointState:
    """Checkpoint for recovery (saved after each iteration)."""
    checkpoint_id: str
    task_id: str
    iteration_num: int
    timestamp_iso: str
    context_state: Dict  # serialized SerializableTaskContext
    recovery_reason: Optional[str] = None  # reason we checkpointed (None = normal progress)
    last_skill_result: Optional[Dict] = None  # SkillResult.to_dict()

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict) -> "CheckpointState":
        return cls(
            checkpoint_id=data["checkpoint_id"],
            task_id=data["task_id"],
            iteration_num=data["iteration_num"],
            timestamp_iso=data["timestamp_iso"],
            context_state=data["context_state"],
            recovery_reason=data.get("recovery_reason"),
            last_skill_result=data.get("last_skill_result")
        )

class StateStore(ABC):
    """Abstract state store interface (CorvinOS integration point)."""

    @abstractmethod
    async def save_state(self, context: SerializableTaskContext) -> str:
        """Save task state, return state_id."""
        pass

    @abstractmethod
    async def load_state(self, task_id: str) -> Optional[SerializableTaskContext]:
        """Load latest state for task."""
        pass

    @abstractmethod
    async def save_checkpoint(self, checkpoint: CheckpointState) -> str:
        """Save checkpoint, return checkpoint_id."""
        pass

    @abstractmethod
    async def load_checkpoint(self, checkpoint_id: str) -> Optional[CheckpointState]:
        """Load checkpoint by ID."""
        pass

    @abstractmethod
    async def list_checkpoints(self, task_id: str) -> List[CheckpointState]:
        """List all checkpoints for task (for GC)."""
        pass

    @abstractmethod
    async def delete_checkpoint(self, checkpoint_id: str) -> None:
        """Delete checkpoint (for GC)."""
        pass

class InMemoryStateStore(StateStore):
    """MVP: In-memory state store (Phase 3a; Phase 3d → corvinOS.get_task_state())."""

    def __init__(self):
        self.states: Dict[str, SerializableTaskContext] = {}
        self.checkpoints: Dict[str, CheckpointState] = {}

    async def save_state(self, context: SerializableTaskContext) -> str:
        self.states[context.task_id] = context
        return context.task_id

    async def load_state(self, task_id: str) -> Optional[SerializableTaskContext]:
        return self.states.get(task_id)

    async def save_checkpoint(self, checkpoint: CheckpointState) -> str:
        self.checkpoints[checkpoint.checkpoint_id] = checkpoint
        return checkpoint.checkpoint_id

    async def load_checkpoint(self, checkpoint_id: str) -> Optional[CheckpointState]:
        return self.checkpoints.get(checkpoint_id)

    async def list_checkpoints(self, task_id: str) -> List[CheckpointState]:
        return sorted(
            [c for c in self.checkpoints.values() if c.task_id == task_id],
            key=lambda c: c.iteration_num
        )

    async def delete_checkpoint(self, checkpoint_id: str) -> None:
        if checkpoint_id in self.checkpoints:
            del self.checkpoints[checkpoint_id]

def serialize_for_spawn(context: SerializableTaskContext) -> str:
    """Serialize context for subprocess spawn (JSON-safe)."""
    return json.dumps(context.to_dict(), default=str)

def deserialize_from_spawn(json_str: str) -> SerializableTaskContext:
    """Deserialize context from subprocess result."""
    data = json.loads(json_str)
    return SerializableTaskContext.from_dict(data)
