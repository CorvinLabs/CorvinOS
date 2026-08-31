"""Brain: Strategy selection + error recovery + task decomposition (Phase 3: async-ready)."""

import uuid
from dataclasses import dataclass
from typing import List, Optional, Dict, Any
from enum import Enum

@dataclass
class Decision:
    """Strategy decision: what skill to use."""
    skill_id: str
    confidence: float  # 0-1
    parameters: Dict[str, Any] = None
    fallback: List[str] = None  # alternative skills

@dataclass
class Recovery:
    """Error recovery strategy."""
    strategy: str  # "retry", "decompose", "fallback", "backtrack", "escalate"
    skill_id: Optional[str] = None
    subtasks: Optional[List[Dict]] = None
    reason: str = ""

@dataclass
class Subtask:
    """Spawnable subtask (Phase 3b: async execution)."""
    id: str
    task_id: str
    goal: str
    item_indices: List[int]  # which items does this subtask handle?
    type: str = "work"  # "work", "merge", "checkpoint"

    def to_spawn_manifest(self) -> Dict:
        """Format for corvinOS.spawn_tasks()."""
        return {
            "id": self.id,
            "parent_task_id": self.task_id,
            "goal": self.goal,
            "item_indices": self.item_indices,
            "type": self.type
        }

class Brain:
    """Orchestration: decide what skill to use, recover from errors (Phase 3: spawn-aware)."""

    def __init__(self, memory_palace, skills_engine):
        self.memory = memory_palace
        self.skills = skills_engine
        self.last_checkpoint = None
        self.spawn_threshold = 10  # spawn if item_count > this

    async def decide(self, task: Dict, context: Any) -> Decision:
        """Choose best strategy for this task."""
        task_type = task.get("type", "generic")
        persona_id = context.get("persona_id", "default")

        # Recall learned strategy weights
        weights = await self.memory.get_strategy_weights(persona_id, task_type)

        # Rank skills by weight
        available = self.skills.list_skills(task_type)
        if not available:
            available = self.skills.list_skills()  # fallback to all

        # Rank by the weight of the STRATEGY each skill realises. Looking the
        # weight up under `s.id` (the original) always missed — weights are
        # keyed "decompose" / "direct_fix" / "backtrack", skills are
        # "decompose_task" / "code_analysis" / "direct_fix" — so every skill
        # scored the uniform default, the sort was a no-op, and nothing the
        # memory learned ever reached this decision.
        ranked = sorted(
            available,
            key=lambda s: weights.get(self._strategy_of(s), 0.33),
            reverse=True
        )

        if not ranked:
            # No skills available, default decision
            return Decision(
                skill_id="decompose_task",
                confidence=0.5,
                fallback=["code_analysis"]
            )

        top_skill = ranked[0]
        confidence = weights.get(self._strategy_of(top_skill), 0.33)

        return Decision(
            skill_id=top_skill.id,
            confidence=confidence,
            fallback=[s.id for s in ranked[1:3]],
            parameters={"task_type": task_type}
        )

    @staticmethod
    def _strategy_of(skill) -> str:
        """The learned-weight key for *skill*.

        A skill declares the strategy it realises (`Skill.strategy`); falling
        back to its id keeps a skill whose id already IS a strategy name
        (`direct_fix`) working, and keeps a third-party skill that declares
        nothing scoring the uniform default rather than crashing.
        """
        return getattr(skill, "strategy", None) or skill.id

    async def recover(self, task: Dict, error: Exception, context: Any) -> Recovery:
        """Decide recovery strategy from error."""
        error_msg = str(error).lower()

        # Simple heuristics (MVP; v1.1: Hermes-Healing integration)
        if "timeout" in error_msg or "network" in error_msg:
            return Recovery(
                strategy="retry",
                reason="Transient error (timeout/network)"
            )

        elif "complexity" in error_msg or "too large" in error_msg:
            return Recovery(
                strategy="decompose",
                reason="Task too complex for single skill"
            )

        elif "not found" in error_msg:
            current_skill = context.get("current_skill")
            fallback = context.get("fallback_skills", [])
            if fallback:
                return Recovery(
                    strategy="fallback",
                    skill_id=fallback[0],
                    reason="Primary skill failed, trying fallback"
                )

        # Default: escalate
        return Recovery(
            strategy="escalate",
            reason=f"Unable to recover from: {error_msg}"
        )

    async def decompose(self, task: Dict, use_spawn: bool = False) -> List[Subtask]:
        """Break task into subtasks (Phase 3b: spawn-aware).

        Args:
            task: Task definition
            use_spawn: If True, return spawn-ready subtasks (for distributed execution)

        Returns:
            List of Subtask objects (spawn-ready if use_spawn=True)
        """
        task_id = task.get("id", str(uuid.uuid4()))
        task_type = task.get("type", "generic")
        total_items = task.get("item_count", 10)

        # Determine batch size based on spawn strategy
        if use_spawn and total_items > self.spawn_threshold:
            # Larger batches for distributed work, because SPAWNING is the
            # expensive part. `max(5, total_items // 10)` computed the number of
            # TASKS, not the size of a batch — inverted against its own comment:
            # for 20 items it yielded batches of 5 (four spawns) where the rule
            # says two. Floor at 10 items per spawned task, and for large inputs
            # let the batch grow so the spawn COUNT stays ~10 rather than the
            # batch count growing without bound.
            batch_size = max(10, total_items // 10)
        else:
            # Smaller batches for sequential: 5 items per batch
            batch_size = 5

        subtasks = []
        for i in range(0, total_items, batch_size):
            end = min(i + batch_size, total_items)
            subtask = Subtask(
                id=str(uuid.uuid4()),
                task_id=task_id,
                goal=f"{task.get('goal', 'Process')} [items {i}–{end-1}]",
                item_indices=list(range(i, end)),
                type="work"
            )
            subtasks.append(subtask)

        # Add integration phase
        if subtasks:
            merge_subtask = Subtask(
                id=str(uuid.uuid4()),
                task_id=task_id,
                goal="Integrate results",
                item_indices=[],
                type="merge"
            )
            subtasks.append(merge_subtask)

        return subtasks

    async def should_spawn(self, task: Dict) -> bool:
        """Decide if task should use distributed spawning (Phase 3b)."""
        total_items = task.get("item_count", 0)
        return total_items > self.spawn_threshold
