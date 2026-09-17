"""ProjectSkill — SkillBase subclass orchestrating the 6-phase DataHub Creator flow.

Implements the critical path (Create → Execute → Metrics → Export) with optional
power-user phases (Optimization Config, Collaboration).

Emits LearningEvent for each phase completion + convergence detection.
Audit-first: all phase transitions logged to Track B's audit chain.
"""

from typing import Dict, List, Optional, Any
from datetime import datetime
import logging

from core.skills.skill_base import SkillBase, SkillResponse
from core.learning.learning_events import LearningEvent, EventType
from .models import ProjectModel, ProjectPhase, ProjectStatus
from .metrics_aggregator import MetricsAggregator

logger = logging.getLogger(__name__)


class ProjectSkill(SkillBase):
    """Orchestrates DataHub Creator 6-phase flow."""

    def __init__(self):
        super().__init__(
            skill_id="datahub.project_creator",
            name="DataHub Project Creator",
            description="6-phase project workspace with skill metrics + learning visualization",
            version="1.0.0",
        )
        self.event_store = None  # Injected from Track B
        self.project_store = None  # Injected

    async def execute(self, input_: Dict[str, Any]) -> SkillResponse:
        """Execute the DataHub project skill.

        Handles phase transitions, metrics aggregation, feedback processing.

        Input:
        {
            "action": "create" | "navigate_phase" | "submit_feedback" | "get_metrics",
            "project_id": (optional),
            "project_data": (optional),
            "phase": (optional),
            "feedback": (optional),
        }
        """
        try:
            action = input_.get("action", "unknown")

            if action == "create":
                return await self._phase_1_create(input_)
            elif action == "execute":
                return await self._phase_2_execute(input_)
            elif action == "get_metrics":
                return await self._phase_3_metrics_dashboard(input_)
            elif action == "submit_feedback":
                return await self._phase_2_submit_feedback(input_)
            elif action == "navigate_phase":
                return await self._navigate_phase(input_)
            elif action == "configure_optimizer":
                return await self._phase_4_optimization_config(input_)
            elif action == "add_collaborator":
                return await self._phase_5_collaboration(input_)
            elif action == "export":
                return await self._phase_6_export(input_)
            else:
                return SkillResponse(
                    success=False,
                    output={"error": f"Unknown action: {action}"},
                )

        except Exception as e:
            logger.error(f"ProjectSkill error: {e}")
            return SkillResponse(
                success=False,
                output={"error": str(e)},
            )

    async def _phase_1_create(self, input_: Dict[str, Any]) -> SkillResponse:
        """Phase 1: Create project (name, skills, goals)."""
        project_data = input_.get("project_data", {})

        try:
            project = ProjectModel(
                name=project_data.get("name", "Untitled Project"),
                description=project_data.get("description"),
                goals=project_data.get("goals", []),
                selected_skills=project_data.get("selected_skills", []),
                tenant_id=project_data.get("tenant_id", "default"),
            )

            # Persist
            await self.project_store.save(project)

            # Emit learning event
            await self._emit_phase_event(
                project,
                EventType.METRIC,
                {
                    "phase": "create",
                    "status": "complete",
                    "skills_count": len(project.selected_skills),
                },
            )

            logger.info(f"Created project {project.project_id}")

            return SkillResponse(
                success=True,
                output={
                    "project_id": project.project_id,
                    "name": project.name,
                    "current_phase": "create",
                    "status": "ready_for_execution",
                },
            )

        except Exception as e:
            logger.error(f"Phase 1 error: {e}")
            return SkillResponse(success=False, output={"error": str(e)})

    async def _phase_2_execute(self, input_: Dict[str, Any]) -> SkillResponse:
        """Phase 2: Execute (run skills, implicit feedback collection)."""
        project_id = input_.get("project_id")

        try:
            project = await self.project_store.get(project_id)
            if not project:
                return SkillResponse(success=False, output={"error": "Project not found"})

            project.mark_phase_complete(ProjectPhase.EXECUTE)
            project.status = ProjectStatus.EXECUTING

            await self.project_store.save(project)

            # Emit event
            await self._emit_phase_event(
                project,
                EventType.METRIC,
                {
                    "phase": "execute",
                    "status": "active",
                    "skills_ready": len(project.selected_skills),
                },
            )

            return SkillResponse(
                success=True,
                output={
                    "project_id": project.project_id,
                    "current_phase": "execute",
                    "ready_for_feedback": True,
                },
            )

        except Exception as e:
            return SkillResponse(success=False, output={"error": str(e)})

    async def _phase_2_submit_feedback(self, input_: Dict[str, Any]) -> SkillResponse:
        """Submit feedback on a skill execution (inline during Phase 2)."""
        project_id = input_.get("project_id")
        feedback = input_.get("feedback", {})

        try:
            project = await self.project_store.get(project_id)
            if not project:
                return SkillResponse(success=False, output={"error": "Project not found"})

            # Record feedback in project
            project.feedback_events.append({
                "timestamp": datetime.utcnow().isoformat() + "Z",
                **feedback,
            })

            await self.project_store.save(project)

            # Emit to Track B's feedback sink
            if self.event_store:
                aggregator = MetricsAggregator(project, event_store=self.event_store)
                await aggregator.record_feedback(
                    skill_id=feedback.get("skill_id"),
                    feedback_type=feedback.get("feedback_type", "outcome"),
                    signal=feedback.get("signal", {}),
                )

            return SkillResponse(
                success=True,
                output={"feedback_recorded": True},
            )

        except Exception as e:
            return SkillResponse(success=False, output={"error": str(e)})

    async def _phase_3_metrics_dashboard(self, input_: Dict[str, Any]) -> SkillResponse:
        """Phase 3: Metrics Dashboard (view convergence + recommendations)."""
        project_id = input_.get("project_id")

        try:
            project = await self.project_store.get(project_id)
            if not project:
                return SkillResponse(success=False, output={"error": "Project not found"})

            project.mark_phase_complete(ProjectPhase.METRICS_DASHBOARD)

            # Aggregate metrics from Track B
            aggregator = MetricsAggregator(project, event_store=self.event_store)
            metrics = await aggregator.aggregate_metrics()
            project.latest_metrics = metrics

            await self.project_store.save(project)

            # Detect convergence
            is_converged = project.is_convergence_detected()
            if is_converged:
                project.status = ProjectStatus.CONVERGED
                await self.project_store.save(project)

            # Emit event
            await self._emit_phase_event(
                project,
                EventType.METRIC,
                {
                    "phase": "metrics_dashboard",
                    "convergence_status": metrics.convergence_status,
                    "avg_confidence": metrics.avg_confidence,
                    "skills_improved": metrics.skills_improved,
                },
            )

            return SkillResponse(
                success=True,
                output={
                    "project_id": project.project_id,
                    "current_phase": "metrics_dashboard",
                    "metrics": {
                        "avg_confidence": metrics.avg_confidence,
                        "convergence_status": metrics.convergence_status,
                        "skills_improved": metrics.skills_improved,
                    },
                    "is_converged": is_converged,
                    "next_action": "export" if is_converged else "continue_executing",
                },
            )

        except Exception as e:
            return SkillResponse(success=False, output={"error": str(e)})

    async def _phase_4_optimization_config(self, input_: Dict[str, Any]) -> SkillResponse:
        """Phase 4: Optimization Config (power users)."""
        project_id = input_.get("project_id")
        config = input_.get("config", {})

        try:
            project = await self.project_store.get(project_id)
            if not project:
                return SkillResponse(success=False, output={"error": "Project not found"})

            project.mark_phase_complete(ProjectPhase.OPTIMIZATION_CONFIG)

            # Update optimizer config
            if "convergence_threshold" in config:
                project.optimizer_config["convergence_threshold"] = config["convergence_threshold"]
            if "min_feedback_samples" in config:
                project.optimizer_config["min_feedback_samples"] = config["min_feedback_samples"]
            if "learning_rate" in config:
                project.optimizer_config["learning_rate"] = config["learning_rate"]

            await self.project_store.save(project)

            # Emit event
            await self._emit_phase_event(
                project,
                EventType.CONFIG_UPDATED,
                {"phase": "optimization_config", "config": project.optimizer_config},
            )

            return SkillResponse(
                success=True,
                output={
                    "project_id": project.project_id,
                    "current_phase": "optimization_config",
                    "config_updated": project.optimizer_config,
                },
            )

        except Exception as e:
            return SkillResponse(success=False, output={"error": str(e)})

    async def _phase_5_collaboration(self, input_: Dict[str, Any]) -> SkillResponse:
        """Phase 5: Collaboration (share project, invite feedback)."""
        project_id = input_.get("project_id")
        collaborator_id = input_.get("collaborator_id")

        try:
            project = await self.project_store.get(project_id)
            if not project:
                return SkillResponse(success=False, output={"error": "Project not found"})

            project.mark_phase_complete(ProjectPhase.COLLABORATION)

            if collaborator_id and collaborator_id not in project.collaborators:
                project.collaborators.append(collaborator_id)

            await self.project_store.save(project)

            # Emit event
            await self._emit_phase_event(
                project,
                EventType.METRIC,
                {
                    "phase": "collaboration",
                    "collaborators_count": len(project.collaborators),
                },
            )

            return SkillResponse(
                success=True,
                output={
                    "project_id": project.project_id,
                    "current_phase": "collaboration",
                    "collaborators": project.collaborators,
                },
            )

        except Exception as e:
            return SkillResponse(success=False, output={"error": str(e)})

    async def _phase_6_export(self, input_: Dict[str, Any]) -> SkillResponse:
        """Phase 6: Export (archive report, download metrics)."""
        project_id = input_.get("project_id")
        format_ = input_.get("format", "jsonl")

        try:
            project = await self.project_store.get(project_id)
            if not project:
                return SkillResponse(success=False, output={"error": "Project not found"})

            project.mark_phase_complete(ProjectPhase.EXPORT)
            project.status = ProjectStatus.ARCHIVED

            await self.project_store.save(project)

            # Emit final event
            await self._emit_phase_event(
                project,
                EventType.METRIC,
                {
                    "phase": "export",
                    "status": "archived",
                    "format": format_,
                    "phases_completed": project.get_phases_completed_count(),
                },
            )

            return SkillResponse(
                success=True,
                output={
                    "project_id": project.project_id,
                    "current_phase": "export",
                    "status": "archived",
                    "export_format": format_,
                },
            )

        except Exception as e:
            return SkillResponse(success=False, output={"error": str(e)})

    async def _navigate_phase(self, input_: Dict[str, Any]) -> SkillResponse:
        """Navigate to a different phase."""
        project_id = input_.get("project_id")
        phase_name = input_.get("phase")

        try:
            project = await self.project_store.get(project_id)
            if not project:
                return SkillResponse(success=False, output={"error": "Project not found"})

            # Map phase name to enum
            phase = ProjectPhase(phase_name)
            project.mark_phase_complete(phase)

            await self.project_store.save(project)

            return SkillResponse(
                success=True,
                output={
                    "project_id": project.project_id,
                    "current_phase": phase.value,
                },
            )

        except ValueError:
            return SkillResponse(success=False, output={"error": f"Invalid phase: {phase_name}"})
        except Exception as e:
            return SkillResponse(success=False, output={"error": str(e)})

    async def _emit_phase_event(
        self,
        project: ProjectModel,
        event_type: EventType,
        signal: Dict[str, Any],
    ) -> None:
        """Emit a learning event for this phase (audit-first)."""
        if not self.event_store:
            return

        try:
            event = LearningEvent.create(
                event_type=event_type,
                skill_id=self.skill_id,
                tenant_id=project.tenant_id,
                signal=signal,
                lom="datahub_creator.ProjectSkill._emit_phase_event",
            )

            await self.event_store.write_event(event)

        except Exception as e:
            logger.error(f"Failed to emit phase event: {e}")
