"""VibeEngine: Orchestration pipeline (Phase 3: checkpoint/resume-aware)."""

import asyncio
import inspect
import logging
import uuid
from typing import Dict, List, Any, Callable, Optional
from datetime import datetime
from pathlib import Path

from .memory_palace import MemoryPalace
from .skills_engine import SkillsEngine
from .brain import Brain
from .context import TaskContext, ContextEnricher, TaskProgress
from .plugin_manager import PluginRegistry
from .state_contract import (
    SerializableTaskContext, SerializableTaskProgress, CheckpointState,
    InMemoryStateStore, serialize_for_spawn, deserialize_from_spawn
)
from .hermes_bridge import HermesBridge
from .event_broadcaster import EventBroadcaster, StatusLevel
from .status_snapshot import StatusSnapshot, StatusPublisher, TaskState, get_publisher

logger = logging.getLogger(__name__)

class VibeEngine:
    """Autonomous task executor (Phase 3: checkpoint/resume/Hermes/Event Bus)."""

    def __init__(self, state_store=None, hermes_client=None, event_bus=None, publisher: Optional[StatusPublisher] = None):
        self.memory = MemoryPalace()
        self.skills = SkillsEngine()
        self.brain = Brain(self.memory, self.skills)
        self.context_enricher = ContextEnricher(self.memory, self.skills)
        self.state_store = state_store or InMemoryStateStore()
        self.hermes = HermesBridge(hermes_client)
        self.broadcaster = EventBroadcaster(event_bus)
        self.publisher = publisher or get_publisher()
        self.status_listeners: List[Callable] = []  # Legacy support

    def add_status_listener(self, listener: Callable):
        """Register status update listener (e.g., Discord notifier)."""
        if not inspect.iscoroutinefunction(listener):
            raise TypeError(f"Status listener must be async, got {type(listener).__name__}")
        self.status_listeners.append(listener)

    async def _broadcast_status(self, level: str, message: str, metadata: Dict = None, task_id: str = "unknown", persona_id: str = "default"):
        """Publish status to Event Bus + legacy listeners (Phase 3d)."""
        # Phase 3d: Use Event Bus if available
        status_level = StatusLevel(level) if isinstance(level, str) else level
        await self.broadcaster.broadcast(
            status_level,
            message,
            task_id=metadata.get("task_id", task_id) if metadata else task_id,
            persona_id=persona_id,
            metadata=metadata or {}
        )

        # Legacy: direct listeners
        for listener in self.status_listeners:
            try:
                await listener(level, message, metadata or {})
            except Exception as e:
                logger.warn(f"Status listener failed: {e}")

    async def _publish_status_snapshot(self, task_id: str, state: TaskState, iteration: int, max_iterations: int, context: TaskContext, current_action: str, checkpoint_id: Optional[str] = None):
        """Publish StatusSnapshot to all bridges (Phase 3.1)."""
        snapshot = StatusSnapshot(
            task_id=task_id,
            session_id="current",  # TODO: inject session_id from context
            state=state,
            progress_percent=context.progress_percent(),
            iteration_num=iteration,
            total_iterations=max_iterations,
            current_action=current_action,
            latest_message=f"Iteration {iteration} of {max_iterations}",
            can_resume=checkpoint_id is not None,
            last_checkpoint_id=checkpoint_id,
            expected_next_step=f"Executing skill {iteration + 1}" if iteration < max_iterations else "Task complete"
        )
        await self.publisher.publish(snapshot)

    async def execute_task(self, task: Dict, persona_id: str = "default", resume_from_checkpoint: Optional[str] = None) -> Dict:
        """
        Main execution loop: autonomous task processing (Phase 3: checkpoint/resume).

        Pipeline:
        1. Load checkpoint if resuming
        2. Enrich context (from memory, skills, persona)
        3. Brain decides strategy
        4. Skills execute
        5. Checkpoint state (Phase 3c)
        6. Memory learns (update weights)
        7. Status broadcast
        8. Loop or complete
        """
        task_id = task.get("id", str(uuid.uuid4()))

        # Step 0: Resume from checkpoint if requested
        if resume_from_checkpoint:
            checkpoint = await self.state_store.load_checkpoint(resume_from_checkpoint)
            if checkpoint:
                logger.info(f"Resuming from checkpoint {resume_from_checkpoint} at iteration {checkpoint.iteration_num}")
                await self._broadcast_status(
                    "info",
                    f"Resuming task from checkpoint (iteration {checkpoint.iteration_num})",
                    {"checkpoint_id": resume_from_checkpoint}
                )
                # Restore context from checkpoint
                context = TaskContext(
                    task_id=checkpoint.task_id,
                    goal=task.get("goal", ""),
                    persona_id=persona_id,
                    progress=TaskProgress(
                        items_completed=checkpoint.context_state.get("progress", {}).get("items_completed", 0),
                        total_items=task.get("item_count", 0)
                    )
                )
                iteration = checkpoint.iteration_num
            else:
                logger.warn(f"Checkpoint {resume_from_checkpoint} not found, starting fresh")
                resume_from_checkpoint = None

        # Step 1: Enrich context (if not resumed)
        if not resume_from_checkpoint:
            context = await self.context_enricher.enrich(task, persona_id)
            await self.memory.store(
                "task_start",
                f"Task started: {task.get('goal', '')}",
                task.get("type", "generic"),
                persona_id
            )
            iteration = 0

        await self._broadcast_status(
            "info",
            f"🚀 Starting task: {context.goal}",
            context.to_dict(),
            task_id=task_id,
            persona_id=persona_id
        )

        # Main loop
        max_iterations = task.get("max_iterations", 100)

        while not context.is_complete() and iteration < max_iterations:
            iteration += 1
            logger.info(f"Iteration {iteration}: {context.progress_percent():.1f}%")

            try:
                # Step 2: Brain decides
                decision = await self.brain.decide(task, context.to_dict())
                context.current_skill = decision.skill_id
                context.fallback_skills = decision.fallback or []

                await self._broadcast_status(
                    "info",
                    f"Strategy: {decision.skill_id} ({decision.confidence:.1%} confidence)",
                    {"skill": decision.skill_id, "confidence": decision.confidence}
                )

                # Step 3: Skills execute
                skill_result = await self.skills.invoke(
                    decision.skill_id,
                    context
                )

                if skill_result.status == "success":
                    # Step 4a: Memory learns (success)
                    await self.memory.update_strategy_weight(
                        persona_id,
                        task.get("type", "generic"),
                        decision.skill_id,
                        success=True
                    )

                    # Update progress
                    context.progress.items_completed += skill_result.output.get("items_processed", 1)
                    context.progress.strategies_tried.append(decision.skill_id)

                    # Phase 3c: Save checkpoint after successful iteration
                    checkpoint = CheckpointState(
                        checkpoint_id=str(uuid.uuid4()),
                        task_id=task_id,
                        iteration_num=iteration,
                        timestamp_iso=datetime.now().isoformat(),
                        context_state={
                            "task_id": context.task_id,
                            "goal": context.goal,
                            "persona_id": persona_id,
                            "progress": {
                                "items_completed": context.progress.items_completed,
                                "total_items": context.progress.total_items,
                                "error_count": len(context.progress.errors_encountered)
                            },
                            "current_skill": context.current_skill
                        },
                        last_skill_result=skill_result.output
                    )
                    await self.state_store.save_checkpoint(checkpoint)

                    await self._broadcast_status(
                        "info",
                        f"✅ Skill succeeded. Progress: {context.progress_percent():.1f}%",
                        {
                            "items_completed": context.progress.items_completed,
                            "total_items": context.progress.total_items,
                            "cost": skill_result.cost_actual,
                            "checkpoint_id": checkpoint.checkpoint_id
                        }
                    )

                    # Phase 3.1: Publish status snapshot to all bridges
                    await self._publish_status_snapshot(
                        task_id=task_id,
                        state=TaskState.RUNNING,
                        iteration=iteration,
                        max_iterations=max_iterations,
                        context=context,
                        current_action=f"✅ Skill '{decision.skill_id}' succeeded",
                        checkpoint_id=checkpoint.checkpoint_id
                    )

                else:
                    # Step 4b: Error recovery (Phase 3d: Hermes-Healing)
                    error = Exception(skill_result.error_trace or "Unknown error")

                    # Try Hermes diagnosis first, fall back to heuristics
                    hermes_response = await self.hermes.diagnose(error, context.to_dict())
                    recovery = await self.brain.recover(task, error, context.to_dict())

                    # Update recovery strategy if Hermes provided better diagnosis
                    if hermes_response and hermes_response.confidence > 0.6:
                        recovery.strategy = self.hermes.map_to_recovery_strategy(hermes_response)
                        recovery.reason = hermes_response.reason
                        logger.info(f"Using Hermes diagnosis: {recovery.strategy} ({hermes_response.confidence:.1%})")

                    await self._broadcast_status(
                        "warning",
                        f"⚠️ Error: {recovery.reason}. Recovery: {recovery.strategy}",
                        {"recovery_strategy": recovery.strategy}
                    )

                    # Phase 3.1: Publish error snapshot
                    await self._publish_status_snapshot(
                        task_id=task_id,
                        state=TaskState.RUNNING,
                        iteration=iteration,
                        max_iterations=max_iterations,
                        context=context,
                        current_action=f"⚠️ Error (recovery: {recovery.strategy})",
                        checkpoint_id=None
                    )

                    # Memory learns (failure)
                    await self.memory.update_strategy_weight(
                        persona_id,
                        task.get("type", "generic"),
                        decision.skill_id,
                        success=False
                    )

                    # Apply recovery
                    if recovery.strategy == "retry":
                        logger.info("Retrying same skill...")
                        continue

                    elif recovery.strategy == "fallback":
                        logger.info(f"Trying fallback: {recovery.skill_id}")
                        context.current_skill = recovery.skill_id
                        continue

                    elif recovery.strategy == "decompose":
                        logger.info("Decomposing task into subtasks...")
                        subtasks = await self.brain.decompose(task)
                        for subtask in subtasks:
                            if subtask.type != "merge":
                                # Recursive execution - convert Subtask to dict for execute_task
                                subtask_dict = subtask.to_spawn_manifest()
                                subtask_result = await self.execute_task(subtask_dict, persona_id)
                                # Check if subtask failed and propagate error
                                if subtask_result.get("status") in ["failed", "escalated"]:
                                    logger.warning(f"Subtask {subtask.id} failed: {subtask_result.get('reason')}")
                                    # Continue with other subtasks but don't mark complete
                                    continue
                        context.progress.items_completed = context.progress.total_items
                        break

                    else:  # escalate
                        # Phase 3c: Save escalation checkpoint
                        escalation_checkpoint = CheckpointState(
                            checkpoint_id=str(uuid.uuid4()),
                            task_id=task_id,
                            iteration_num=iteration,
                            timestamp_iso=datetime.now().isoformat(),
                            context_state={
                                "task_id": context.task_id,
                                "goal": context.goal,
                                "persona_id": persona_id,
                                "progress": {
                                    "items_completed": context.progress.items_completed,
                                    "total_items": context.progress.total_items,
                                    "error_count": len(context.progress.errors_encountered)
                                }
                            },
                            recovery_reason=recovery.reason
                        )
                        await self.state_store.save_checkpoint(escalation_checkpoint)

                        await self._broadcast_status(
                            "error",
                            f"❌ Task blocked: {recovery.reason}",
                            {"error": recovery.reason, "checkpoint_id": escalation_checkpoint.checkpoint_id}
                        )
                        return {"status": "escalated", "reason": recovery.reason, "checkpoint_id": escalation_checkpoint.checkpoint_id}

            except Exception as e:
                logger.error(f"Unexpected error: {e}", exc_info=True)
                await self._broadcast_status(
                    "error",
                    f"❌ Unexpected error: {str(e)}",
                    {"error": str(e)}
                )
                return {"status": "failed", "error": str(e)}

        # Task complete
        await self._broadcast_status(
            "success",
            f"✅ Task complete! {context.progress_percent():.1f}% done",
            context.to_dict()
        )

        summary = {
            "status": "complete" if context.is_complete() else "partial",
            "task_id": context.task_id,
            "items_completed": context.progress.items_completed,
            "total_items": context.progress.total_items,
            "strategies_tried": context.progress.strategies_tried,
            "error_count": len(context.progress.errors_encountered)
        }

        return summary
