"""Creator 2.0 Skill — Skill/Tool generator with 12-phase model."""

from dataclasses import asdict
from typing import Dict, Any, Optional, List
from .phase_model import PhaseModelOrchestrator, Creator20State


class Creator20Skill:
    """Creator 2.0 Skill — generates new skills/tools using 12-phase model."""

    def __init__(self, mode: str = "skill"):
        """Initialize Creator 2.0 Skill.

        Args:
            mode: "skill" to generate Skill subclasses, "tool" to generate tool functions.
        """
        self.mode = mode
        self.orchestrator = PhaseModelOrchestrator()

    def execute(
        self,
        query: str,
        clarifications: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Execute skill generation.

        Args:
            query: Natural language description of the skill/tool to generate.
            clarifications: Pre-provided answers to clarification questions.

        Returns:
            Dictionary with generated skill metadata, code, and manifest.
        """
        # Run full 12-phase pipeline
        state = self.orchestrator.execute(
            query=query,
            mode=self.mode,
            user_clarifications=clarifications,
        )

        # Build result
        result = {
            "skill_id": state.skill_id,
            "mode": self.mode,
            "success": self.orchestrator.validate_execution(state),
            "final_loss": state.final_loss,
            "phases_completed": len(self.orchestrator.get_events()),
            "output": self._serialize_state(state),
            "manifest": state.delivery_result.final_manifest if state.delivery_result else None,
            "artifacts": state.delivery_result.artifacts if state.delivery_result else None,
        }

        return result

    def get_events(self) -> List[Dict[str, Any]]:
        """Get all phase completion events (for learning integration)."""
        events = self.orchestrator.get_events()
        return [e.to_dict() for e in events]

    def validate_loss(self) -> bool:
        """Check that final loss is well-formed (no NaN, no inf)."""
        return self.orchestrator.is_loss_valid()

    @staticmethod
    def _serialize_state(state: Creator20State) -> Dict[str, Any]:
        """Convert Creator20State to serializable dict."""
        output = {
            "intake": asdict(state.intake_result) if state.intake_result else None,
            "ingestion": asdict(state.ingestion_result) if state.ingestion_result else None,
            "clarifications": state.clarifications,
            "planning": asdict(state.planning_result) if state.planning_result else None,
            "checkpoint": asdict(state.checkpoint_result) if state.checkpoint_result else None,
            "structure": asdict(state.structure_result) if state.structure_result else None,
            "content": asdict(state.content_result) if state.content_result else None,
            "hooks": asdict(state.hooks_result) if state.hooks_result else None,
            "optimization": asdict(state.optimization_result) if state.optimization_result else None,
            "validation": asdict(state.validation_result) if state.validation_result else None,
            "packaging": asdict(state.packaging_result) if state.packaging_result else None,
            "delivery": asdict(state.delivery_result) if state.delivery_result else None,
        }
        return output
