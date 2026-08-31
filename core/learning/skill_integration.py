"""Skill System Integration — wire learning events into skill lifecycle (Phase 4)."""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import uuid4

from .decision_history import DecisionRecorder
from .outcome_feedback import OutcomeRecorder, OutcomeType
from .metrics import MetricsCollector, MetricType
from .event_emitter import EventEmitter


class SkillLearningHooks:
    """Hooks for capturing learning signals during skill execution."""

    def __init__(
        self,
        tenant_id: str,
        emitter: EventEmitter,
    ):
        """Initialize learning hooks.

        Args:
            tenant_id: Tenant ID
            emitter: Event emitter for learning signals
        """
        self.tenant_id = tenant_id
        self.emitter = emitter
        self.decision_recorder = DecisionRecorder(tenant_id)
        self.outcome_recorder = OutcomeRecorder(tenant_id)
        self.metrics_collector = MetricsCollector(tenant_id)

    async def on_skill_selection(
        self,
        candidates: list[str],
        chosen: str,
        session_id: str,
        confidence_score: Optional[float] = None,
        reasoning: Optional[str] = None,
    ) -> str:
        """Hook: skill selection (ADR-0316).

        Called when skill system selects a skill from candidates.

        Args:
            candidates: Available skill names
            chosen: Selected skill name
            session_id: Session ID
            confidence_score: Score (0.0-1.0) if available
            reasoning: Why this skill was chosen

        Returns:
            decision_id (for later outcome linking)
        """
        decision = self.decision_recorder.create_decision(
            choice_type="skill_selection",
            candidates=candidates,
            chosen=chosen,
            session_id=session_id,
            confidence_score=confidence_score,
            reasoning=reasoning,
        )

        await self.emitter.emit_decision(
            decision_id=decision.decision_id,
            choice_type=decision.choice_type,
            candidates=decision.candidates,
            chosen=decision.chosen,
            session_id=decision.session_id,
            confidence_score=decision.confidence_score,
            reasoning=decision.reasoning,
        )

        return decision.decision_id

    async def on_skill_executed(
        self,
        decision_id: str,
        session_id: str,
        skill_name: str,
        latency_ms: float,
    ) -> None:
        """Hook: skill execution completed (ADR-0320).

        Called after skill finishes executing.

        Args:
            decision_id: Decision ID from selection
            session_id: Session ID
            skill_name: Skill that executed
            latency_ms: Execution time in milliseconds
        """
        metric = self.metrics_collector.record_latency(
            session_id=session_id,
            value=latency_ms,
            skill_name=skill_name,
        )

        await self.emitter.emit_metric(
            metric_id=metric.metric_id,
            metric_type=metric.metric_type.value,
            value=metric.value,
            session_id=metric.session_id,
            skill_name=metric.skill_name,
            tags=metric.tags,
        )

    async def on_skill_outcome(
        self,
        decision_id: str,
        session_id: str,
        outcome: OutcomeType,
        user_feedback: Optional[str] = None,
        rating: Optional[int] = None,
    ) -> None:
        """Hook: user confirms/refutes skill output (ADR-0317).

        Called when user provides feedback on skill execution.

        Args:
            decision_id: Decision ID from selection
            session_id: Session ID
            outcome: "success", "partial", or "failure"
            user_feedback: User's text feedback
            rating: User's numeric rating (1-5)
        """
        record = self.outcome_recorder.record_outcome(
            decision_id=decision_id,
            session_id=session_id,
            outcome=outcome,
            feedback_text=user_feedback,
            rating=rating,
        )

        await self.emitter.emit_outcome(
            outcome_id=record.outcome_id,
            decision_id=record.decision_id,
            session_id=record.session_id,
            outcome=record.outcome.value,
            feedback_text=record.feedback_text,
            rating=record.rating,
        )

    async def on_preference_changed(
        self,
        preference_type: str,
        preference_value: str,
        session_id: Optional[str] = None,
    ) -> None:
        """Hook: user changed preference (ADR-0318).

        Called when user changes a style preference.

        Args:
            preference_type: Type of preference (decision_style, verbosity, etc.)
            preference_value: New value
            session_id: Optional session ID
        """
        await self.emitter.emit_preference(
            preference_type=preference_type,
            preference_value=preference_value,
            session_id=session_id,
        )
