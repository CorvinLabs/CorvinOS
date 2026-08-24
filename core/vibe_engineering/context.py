"""TaskContext: Canonical task state + enrichment."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Any, Optional

@dataclass
class TaskProgress:
    """Mutable task progress tracking."""
    items_completed: int = 0
    total_items: int = 0
    errors_encountered: List[str] = field(default_factory=list)
    strategies_tried: List[str] = field(default_factory=list)
    learnings: List[str] = field(default_factory=list)

@dataclass
class TaskContext:
    """Canonical task state (enriched on-the-fly)."""
    task_id: str
    goal: str  # "Refactor 50 files for clarity"
    persona_id: str  # who's running this?
    created_at: datetime = field(default_factory=datetime.now)

    # Mutable
    progress: TaskProgress = field(default_factory=TaskProgress)
    current_skill: Optional[str] = None
    fallback_skills: List[str] = field(default_factory=list)

    # Enriched (computed)
    recalled_memories: List[Dict] = field(default_factory=list)
    persona_preferences: Dict[str, Any] = field(default_factory=dict)
    strategy_weights: Dict[str, float] = field(default_factory=dict)
    available_skills: List[str] = field(default_factory=list)

    # Session
    checkpoints: List[Dict] = field(default_factory=list)
    artifacts: List[Dict] = field(default_factory=list)

    def is_complete(self) -> bool:
        """Task complete?"""
        return self.progress.items_completed >= self.progress.total_items

    def progress_percent(self) -> float:
        """Percent complete."""
        if self.progress.total_items == 0:
            return 0.0
        return 100.0 * self.progress.items_completed / self.progress.total_items

    def to_dict(self) -> Dict:
        """Serialize for persistence."""
        return {
            "task_id": self.task_id,
            "goal": self.goal,
            "persona_id": self.persona_id,
            "progress": {
                "items_completed": self.progress.items_completed,
                "total_items": self.progress.total_items,
                "error_count": len(self.progress.errors_encountered)
            },
            "current_skill": self.current_skill,
            "progress_percent": self.progress_percent()
        }

class ContextEnricher:
    """Enrich context from multiple sources."""

    def __init__(self, memory_palace, skills_engine):
        self.memory = memory_palace
        self.skills = skills_engine

    async def enrich(self, task: Dict, persona_id: str) -> TaskContext:
        """Assemble enriched context."""
        context = TaskContext(
            task_id=task.get("id", "unknown"),
            goal=task.get("goal", ""),
            persona_id=persona_id,
            progress=TaskProgress(
                total_items=task.get("item_count", 0)
            )
        )

        # Enrich from Memory
        goal_keywords = context.goal.split()[:3]
        query = " ".join(goal_keywords)
        task_type = task.get("type", "generic")
        context.recalled_memories = [
            m.to_dict() for m in
            await self.memory.recall(query, task_type, limit=3)
        ]
        context.strategy_weights = await self.memory.get_strategy_weights(
            persona_id, task_type
        )

        # Enrich from Skills
        context.available_skills = [
            s.id for s in self.skills.list_skills(task_type)
        ]

        return context
