"""Memory Palace: Semantic recall + episodic logging + strategy weights."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Optional
from uuid import UUID, uuid4
import json

@dataclass
class MemoryEntry:
    """Immutable memory entry (semantic, episodic, or decision record)."""
    id: UUID
    timestamp: datetime
    entry_type: str  # "semantic", "episodic", "decision", "learning"
    task_type: str  # "refactoring", "testing", "debugging"
    content: str  # The actual memory
    persona_id: str  # Whose memory is this?
    hash_previous: str = ""  # Hash chain (ADR-0278)

    def to_dict(self):
        return {
            "id": str(self.id),
            "timestamp": self.timestamp.isoformat(),
            "type": self.entry_type,
            "task_type": self.task_type,
            "content": self.content,
            "persona_id": self.persona_id,
        }

@dataclass
class StrategyWeights:
    """Strategy success rates (learned from past tasks)."""
    persona_id: str
    task_type: str
    weights: Dict[str, float] = field(default_factory=lambda: {
        "decompose": 0.33,
        "direct_fix": 0.33,
        "backtrack": 0.34,
    })
    confidence: int = 0  # samples seen
    last_updated: datetime = field(default_factory=datetime.now)

class MemoryPalace:
    """Hierarchical memory system: semantic, episodic, strategy weights."""

    def __init__(self, store_path: str = "~/.corvin/memory_palace"):
        self.store_path = store_path
        self.entries: List[MemoryEntry] = []
        self.weights: Dict[str, StrategyWeights] = {}
        self.last_hash = ""

    async def recall(self, query: str, task_type: str = None, limit: int = 5) -> List[MemoryEntry]:
        """Semantic search: return relevant memories."""
        # MVP: simple keyword matching (v1.1: vector DB)
        results = []
        for entry in self.entries:
            if task_type and entry.task_type != task_type:
                continue
            if any(word in entry.content.lower() for word in query.lower().split()):
                results.append(entry)
        return results[:limit]

    async def store(self, entry_type: str, content: str, task_type: str,
                   persona_id: str) -> UUID:
        """Store memory entry (immutable + hash-chained)."""
        entry = MemoryEntry(
            id=uuid4(),
            timestamp=datetime.now(),
            entry_type=entry_type,
            content=content,
            task_type=task_type,
            persona_id=persona_id,
            hash_previous=self.last_hash
        )
        self.entries.append(entry)
        # Update hash chain (ADR-0278) - use string representation for consistency
        self.last_hash = str(hash(str(entry.id) + self.last_hash))
        return entry.id

    async def get_strategy_weights(self, persona_id: str, task_type: str) -> Dict[str, float]:
        """Retrieve learned strategy weights for persona + task type.

        Returns a SNAPSHOT. Returning `self.weights[key].weights` directly
        handed every caller a live reference into the store: the "weights I
        decided on" silently changed the moment anything learned, and code that
        compares a before against an after — which is what a learning system
        does — was comparing one object with itself and could never observe a
        change. A copy is also what makes a decision reproducible: the ranking
        that produced a Decision is the ranking that was in effect.
        """
        key = f"{persona_id}:{task_type}"
        if key not in self.weights:
            # Default uniform
            self.weights[key] = StrategyWeights(persona_id, task_type)
        return dict(self.weights[key].weights)

    async def update_strategy_weight(self, persona_id: str, task_type: str,
                                    strategy: str, success: bool):
        """Observe outcome → update weights (exponential moving average)."""
        key = f"{persona_id}:{task_type}"
        if key not in self.weights:
            self.weights[key] = StrategyWeights(persona_id, task_type)

        w = self.weights[key]
        success_val = 1.0 if success else 0.0

        # Exponential moving average (conservative α=0.3)
        old_weight = w.weights.get(strategy, 0.33)
        new_weight = old_weight * 0.7 + success_val * 0.3
        w.weights[strategy] = new_weight
        w.confidence += 1
        w.last_updated = datetime.now()

        # Log learning event
        await self.store(
            "learning",
            f"Strategy {strategy} for {task_type}: success={success}, "
            f"old_weight={old_weight:.2f}, new_weight={new_weight:.2f}",
            task_type,
            persona_id
        )
