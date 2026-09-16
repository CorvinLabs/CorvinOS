"""Model Selection Skill (Phase 2 Tier 4 Integration)

Uses Learning Loop to route tasks to optimal model.
"""

from core.learning.ldd_learning_loop import LddLearningLoop, ModelStrategy
from core.orchestration.subsystems.notification_daemon import EventTopic, EventPriority, emit_event

class ModelSelectionSkill:
    """Production skill for intelligent model routing."""
    
    def __init__(self):
        self.learning_loop = LddLearningLoop()
    
    async def route_request(self, task_id: str, task_type: str, difficulty: str) -> str:
        """Route task to model based on learning history."""
        strategy = await self.learning_loop.optimize_next_strategy(task_difficulty=difficulty)
        
        await emit_event(
            EventTopic.SKILL_EXECUTED,
            EventPriority.NORMAL,
            {"skill": "model_selection", "task_id": task_id, "model": strategy.value},
            "model_selection_skill"
        )
        
        return strategy.value
