"""Model Selection Skill — Full Implementation (Phase 2.6, T4.1)"""

from core.learning.ldd_learning_loop import LddLearningLoop, ModelStrategy
from core.orchestration.subsystems.notification_daemon import emit_event, EventTopic, EventPriority

class ModelSelectionSkill:
    async def route_request(self, task_id: str, task_type: str, difficulty: str) -> str:
        loop = LddLearningLoop()
        strategy = await loop.optimize_next_strategy(task_difficulty=difficulty)
        await emit_event(EventTopic.SKILL_EXECUTED, EventPriority.NORMAL,
                        {"skill": "model_selection", "task_id": task_id, "model": strategy.value},
                        "model_selection_skill")
        return strategy.value
