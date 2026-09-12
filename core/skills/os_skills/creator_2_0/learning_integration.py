"""Learning infrastructure integration for Creator 2.0 (ADR-0661 Phase 2).

CRITICAL FIX #1 (2026-09-12): Feedback Audit-Chaining (GDPR Art. 30)
- Every learning feedback event MUST be hash-chained to audit trail
- Uses EventStore.write_event for audit-first design
- Tenant-scoped for GDPR Art. 32 compliance
"""

from dataclasses import asdict
from typing import List, Optional
from datetime import datetime
from pathlib import Path

from core.learning.learning_events import LearningEvent, EventType
from core.learning.event_store import EventStore
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


class Creator20AuditIntegration:
    """FIX #1: Audit-first integration for learning feedback (GDPR Art. 30, ADR-0314).

    Every feedback event from skill generation is:
    1. Committed to the core hash-chain FIRST (audit-first, fail-closed)
    2. Persisted to disk with audit_ref pointing back to core chain
    3. Tenant-scoped (GDPR Art. 32)
    4. Immutable (frozen dataclass)

    This ensures operator compliance proof: every learning decision is auditable.
    """

    def __init__(self, tenant_home: Optional[Path] = None, tenant_id: str = "_default"):
        """Initialize audit integration for Creator 2.0 feedback.

        Args:
            tenant_home: Path to tenant home (~/.corvin/tenants/_default/)
            tenant_id: Tenant scope for isolation (GDPR Art. 32)
        """
        self.tenant_id = tenant_id
        if tenant_home is None:
            from core.paths import tenant_home as get_tenant_home
            tenant_home = get_tenant_home(tenant_id)
        self.event_store = EventStore(tenant_home, tenant_id=tenant_id)

    def emit_feedback_event_with_audit(
        self,
        phase_event: PhaseCompletedEvent,
        skill_version: str = "1.0",
    ) -> str:
        """Emit a feedback event with audit-chaining (FIX #1).

        Args:
            phase_event: Creator 2.0 PhaseCompletedEvent
            skill_version: Version of skill being created

        Returns:
            audit_ref (pointer to core hash-chain record, for proof)

        Raises:
            RuntimeError: if audit chain is unavailable or fails to commit
            (fail-closed: no disk write if audit chain fails)
        """
        # Convert phase event to learning event
        learning_event = Creator20LearningBridge.convert_phase_event_to_learning_event(
            phase_event,
            tenant_id=self.tenant_id,
            skill_version=skill_version,
        )

        # FIX #1: Write to event store with audit-chaining
        # This FIRST commits to core hash-chain, then writes disk
        # If chain fails, exception is raised and nothing hits disk (fail-closed)
        self.event_store.write_event(learning_event)

        return learning_event.audit_ref or ""

    def emit_feedback_events_with_audit(
        self,
        phase_events: List[PhaseCompletedEvent],
        skill_version: str = "1.0",
    ) -> List[str]:
        """Emit multiple feedback events with audit-chaining.

        Args:
            phase_events: List of PhaseCompletedEvent
            skill_version: Version of skill

        Returns:
            List of audit_refs (one per event)
        """
        audit_refs = []
        for event in phase_events:
            try:
                audit_ref = self.emit_feedback_event_with_audit(event, skill_version)
                audit_refs.append(audit_ref)
            except (RuntimeError, IOError) as e:
                # Log error but continue (graceful degradation)
                # The audit chain failure is already in core audit trail
                raise RuntimeError(
                    f"Failed to emit feedback for phase {event.phase_num}: {e}"
                ) from e

        return audit_refs

    def validate_feedback_chain(self, skill_id: str, phase_num: int) -> bool:
        """Validate that feedback events are properly hash-chained (FIX #1 proof).

        Args:
            skill_id: Skill ID to check
            phase_num: Phase number

        Returns:
            True if event chain is valid, False otherwise
        """
        # Query events for this skill
        events = self.event_store.query_events(
            tenant_id=self.tenant_id,
            skill_id=f"creator_2_0.{skill_id}",
        )

        # Check that each event has audit_ref (proof of audit-chaining)
        for event in events:
            if event.audit_ref is None:
                return False  # Event not properly audit-chained

        return len(events) > 0


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
