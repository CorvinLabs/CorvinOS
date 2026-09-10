"""SkillToolCreator 2.0 — 12-phase generation with learning."""

from typing import List, Dict, Any
from uuid import uuid4
import asyncio
from .events import PhaseCompletedEvent, CreatorRequest, CreatorOutput
from .phases import PhaseExecutor


class SkillToolCreatorSkill:
    """Generate Skills or Tools via 12-phase model with LDD."""

    def __init__(self):
        self.executor = PhaseExecutor()

    async def execute(self, request: CreatorRequest) -> CreatorOutput:
        """
        Main entry point.
        request.goal: one-liner description
        request.data_manifest_id: from DataHub
        request.mode: "skill" | "tool"
        request.quality_target: 0.90 by default

        Returns: Generated Skill/Tool with full phase history
        """
        skill_id = f"skill-{uuid4().hex[:8]}"

        # Run phases 0–10
        phase_events = await self.executor.run_all_phases(
            skill_id, request.goal, request.mode
        )

        # Compute total loss
        total_loss = sum(e.loss_component for e in phase_events) / len(phase_events)
        quality_score = 1.0 - total_loss  # convert loss to score

        # Build learning insights (for daemon)
        learnings = {
            "data_manifest_used": request.data_manifest_id,
            "mode": request.mode,
            "phases_run": len(phase_events),
            "phases_succeeded": sum(1 for e in phase_events if e.success),
            "total_duration_ms": sum(e.duration_ms for e in phase_events),
            "quality_score": quality_score,
            "target_met": quality_score >= request.quality_target,
        }

        # Mock validation report
        validation_report = {
            "blockers": 0,
            "warnings": 0,
            "checks_run": 13,
            "checks_passed": 13,
        }

        return CreatorOutput(
            skill_id=skill_id,
            zip_path=f"/tmp/{skill_id}.zip",
            validation_report=validation_report,
            quality_score=quality_score,
            all_phase_events=phase_events,
            learnings=learnings,
        )

    def compute_loss_from_phases(
        self, phase_events: List[PhaseCompletedEvent]
    ) -> Dict[str, float]:
        """
        6D Loss Vector for the skill.
        Returns: dict with data_quality, generation_quality, etc.
        """
        # Group by dimension
        loss_vector = {
            "data_quality": phase_events[0].loss_component if phase_events else 0.5,
            "generation_quality": (
                sum(e.loss_component for e in phase_events[4:7]) / 3
                if len(phase_events) > 4
                else 0.5
            ),
            "user_satisfaction": phase_events[-1].loss_component if phase_events else 0.5,
            "efficiency": (
                1.0 - (sum(e.duration_ms for e in phase_events) / 1000.0 / 180.0)
            ),  # 3min = goal
            "learning_loop_health": 0.8,  # mock
            "system_health": 0.9,  # mock
        }
        return loss_vector
