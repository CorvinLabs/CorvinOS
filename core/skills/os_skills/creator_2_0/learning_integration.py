"""Learning infrastructure integration for Creator 2.0 (ADR-0661 Phase 2)."""

from dataclasses import asdict
from typing import List, Optional
from datetime import datetime

from core.learning.learning_events import LearningEvent, EventType
from .events import PhaseCompletedEvent


class Creator20LearningBridge:
    """Bridge between Creator 2.0 internal events and ADR-0314 learning infrastructure.

    Maps PhaseCompletedEvent → LearningEvent for audit trail and learning system integration.
    """

    @staticmethod
    def convert_phase_event_to_learning_event(
        phase_event: PhaseCompletedEvent,
        tenant_id: str = "_default",
        skill_version: str = "1.0",
    ) -> LearningEvent:
        """Convert Creator 2.0 PhaseCompletedEvent to ADR-0314 LearningEvent.

        Args:
            phase_event: PhaseCompletedEvent from Creator 2.0 phase execution
            tenant_id: Tenant scope (GDPR Art. 32)
            skill_version: Version of the skill being created

        Returns:
            LearningEvent suitable for audit trail and event store
        """
        # Map loss components to signal payload
        signal = {
            "phase_num": phase_event.phase_num,
            "duration_ms": phase_event.duration_ms,
            "errors": phase_event.errors,
            "metadata": phase_event.metadata,
        }

        # Add loss components if present
        if phase_event.loss_components:
            signal["loss_components"] = {
                "relevance": phase_event.loss_components.relevance,
                "completeness": phase_event.loss_components.completeness,
                "performance": phase_event.loss_components.performance,
                "maintainability": phase_event.loss_components.maintainability,
                "overall": phase_event.loss_components.overall(),
            }

        # Create learning event with skill_id prefixed for Creator subsystem
        learning_event = LearningEvent.create(
            event_type=EventType.SKILL_EXECUTED,
            skill_id=f"creator_2_0.{phase_event.skill_id}",
            tenant_id=tenant_id,
            signal=signal,
            skill_version=skill_version,
            lom=f"creator_2_0/phases/phase_{phase_event.phase_num}.py:execute",
        )

        return learning_event

    @staticmethod
    def convert_phase_events_to_learning_events(
        phase_events: List[PhaseCompletedEvent],
        tenant_id: str = "_default",
        skill_version: str = "1.0",
    ) -> List[LearningEvent]:
        """Convert multiple PhaseCompletedEvents to LearningEvents.

        Args:
            phase_events: List of PhaseCompletedEvent from full skill generation
            tenant_id: Tenant scope
            skill_version: Version of skill being created

        Returns:
            List of LearningEvent objects ready for event store
        """
        return [
            Creator20LearningBridge.convert_phase_event_to_learning_event(
                event,
                tenant_id=tenant_id,
                skill_version=skill_version,
            )
            for event in phase_events
        ]

    @staticmethod
    def compute_overall_skill_loss(
        phase_events: List[PhaseCompletedEvent],
        weights: Optional[dict] = None,
    ) -> float:
        """Compute overall skill creation loss across all phases.

        Args:
            phase_events: All PhaseCompletedEvent from skill generation
            weights: Optional custom loss component weights

        Returns:
            Overall loss score (0 = perfect, 1 = failed)
        """
        if not phase_events:
            return 1.0  # No phases = total failure

        # Default weights for phase importance
        if weights is None:
            weights = {
                "validation": 0.25,  # Phase 8 most important
                "planning": 0.20,
                "content": 0.20,
                "packaging": 0.15,
                "structure": 0.10,
                "hooks": 0.05,
                "optimization": 0.03,
                "intake": 0.02,
            }

        # Map phase numbers to names
        phase_names = {
            0: "intake",
            1: "ingestion",
            2: "clarification",
            3: "planning",
            4: "structure",
            5: "content",
            6: "hooks",
            7: "optimization",
            8: "validation",
            9: "packaging",
            10: "delivery",
        }

        total_weighted_loss = 0.0
        total_weight = 0.0

        for event in phase_events:
            phase_name = phase_names.get(event.phase_num, "unknown")
            weight = weights.get(phase_name, 0.01)  # Default low weight for unknown phases

            if event.loss_components:
                overall = event.loss_components.overall()
                total_weighted_loss += overall * weight
                total_weight += weight
            else:
                # Penalize phases without loss components
                total_weighted_loss += 1.0 * weight
                total_weight += weight

        if total_weight == 0:
            return 1.0

        return total_weighted_loss / total_weight


class Creator20AuditLogger:
    """Audit logging for Creator 2.0 skill generation (ADR-0232/0233 compliance)."""

    @staticmethod
    def log_skill_generation_started(
        skill_id: str,
        query: str,
        mode: str,
        tenant_id: str = "_default",
    ) -> dict:
        """Log the start of skill generation for audit trail.

        Returns:
            Session metadata for tracking throughout generation
        """
        return {
            "skill_id": skill_id,
            "query": query,
            "mode": mode,
            "tenant_id": tenant_id,
            "started_at": datetime.utcnow().isoformat(),
        }

    @staticmethod
    def log_skill_generation_completed(
        skill_id: str,
        final_loss: float,
        phases_completed: int,
        success: bool,
        tenant_id: str = "_default",
    ) -> dict:
        """Log completion of skill generation for audit trail.

        Returns:
            Completion summary for audit record
        """
        return {
            "skill_id": skill_id,
            "final_loss": final_loss,
            "phases_completed": phases_completed,
            "success": success,
            "tenant_id": tenant_id,
            "completed_at": datetime.utcnow().isoformat(),
        }
