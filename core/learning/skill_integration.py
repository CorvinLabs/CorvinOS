"""Skill System Integration — wire learning events into skill lifecycle (Phase 4).

Adversarial review 2026-09-03: the hooks called ``emitter.emit_decision`` /
``emit_metric`` / ``emit_outcome`` / ``emit_preference`` — none of which
``EventEmitter`` has ever had (its one method is ``emit(LearningEvent)``). Every
hook therefore raised ``AttributeError`` on first use, and the three wrapper
classes around them were dead. The hooks now build typed ``LearningEvent``s
(ADR-0314 schema) and hand them to the real emitter.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Optional

from .decision_history import DecisionRecorder
from .event_emitter import EventEmitter
from .event_schema import (
    DecisionRecordPayload,
    MetricAggregatedPayload,
    OutcomeObservedPayload,
    PreferenceSetPayload,
)
from .learning_events import EventType, LearningEvent
from .metrics import MetricsCollector
from .outcome_feedback import OutcomeRecorder, OutcomeType


class SkillLearningHooks:
    """Hooks for capturing learning signals during skill execution."""

    def __init__(
        self,
        tenant_id: str,
        emitter: EventEmitter,
        instance_id: str = "corvinos",
    ):
        """Initialize learning hooks.

        Args:
            tenant_id: Tenant ID (every event is scoped to it — GDPR Art. 32)
            emitter: Event emitter for learning signals (``emit(LearningEvent)``)
            instance_id: Emitting instance identifier
        """
        if not tenant_id:
            raise ValueError("tenant_id required (fail-closed)")
        self.tenant_id = tenant_id
        self.emitter = emitter
        self.instance_id = instance_id
        self.decision_recorder = DecisionRecorder(tenant_id)
        self.outcome_recorder = OutcomeRecorder(tenant_id)
        self.metrics_collector = MetricsCollector(tenant_id)
        self.dropped_events = 0

    def _emit(
        self,
        event_type: EventType,
        session_id: str,
        payload: dict,
        skill_name: Optional[str] = None,
    ) -> bool:
        """Build the event the emitter/store pair persists and hand it over.

        Typed payload dataclasses (ADR-0314 ``event_schema``) shape ``signal``;
        the envelope is ``learning_events.LearningEvent`` because that is what
        ``EventEmitter`` → ``EventStore.write_event`` serialises (a canonical
        ``event_schema.LearningEvent`` is silently LOST by that store).
        """
        signal = dict(payload)
        signal["session_id"] = session_id
        signal["instance_id"] = self.instance_id
        event = LearningEvent.create(
            event_type=event_type,
            skill_id=skill_name or "os.skill_system",
            tenant_id=self.tenant_id,
            signal=signal,
        )
        ok = bool(self.emitter.emit(event))
        if not ok:
            self.dropped_events += 1  # queue full — observable, never silent
        return ok

    async def on_skill_selection(
        self,
        candidates: list[str],
        chosen: str,
        session_id: str,
        confidence_score: Optional[float] = None,
        reasoning: Optional[str] = None,
    ) -> str:
        """Hook: skill selection (ADR-0316). Returns decision_id for outcome linking."""
        decision = self.decision_recorder.create_decision(
            choice_type="skill_selection",
            candidates=candidates,
            chosen=chosen,
            session_id=session_id,
            confidence_score=confidence_score,
            reasoning=reasoning,
        )
        payload = asdict(DecisionRecordPayload(
            decision_id=decision.decision_id,
            choice_type=decision.choice_type,
            candidates=list(decision.candidates),
            chosen=decision.chosen,
            context={"confidence_score": decision.confidence_score, "reasoning": decision.reasoning},
        ))
        self._emit(EventType.DECISION, session_id, payload, skill_name=chosen)
        return decision.decision_id

    async def on_skill_executed(
        self,
        decision_id: str,
        session_id: str,
        skill_name: str,
        latency_ms: float,
    ) -> None:
        """Hook: skill execution completed (ADR-0320) — latency metric."""
        metric = self.metrics_collector.record_latency(
            session_id=session_id,
            value=latency_ms,
            skill_name=skill_name,
        )
        payload = asdict(MetricAggregatedPayload(
            metric_name=metric.metric_type.value,
            window_seconds=0,
            value=float(metric.value),
            sample_count=1,
        ))
        payload["decision_id"] = decision_id
        payload["metric_id"] = metric.metric_id
        self._emit(EventType.METRIC, session_id, payload, skill_name=skill_name)

    async def on_skill_outcome(
        self,
        decision_id: str,
        session_id: str,
        outcome: OutcomeType,
        user_feedback: Optional[str] = None,
        rating: Optional[int] = None,
    ) -> None:
        """Hook: user confirms/refutes skill output (ADR-0317)."""
        record = self.outcome_recorder.record_outcome(
            decision_id=decision_id,
            session_id=session_id,
            outcome=outcome,
            feedback_text=user_feedback,
            rating=rating,
        )
        payload = asdict(OutcomeObservedPayload(
            decision_id=record.decision_id,
            outcome_type=record.outcome.value,
            outcome_value=record.rating,
            window_seconds=0,
        ))
        payload["outcome_id"] = record.outcome_id
        self._emit(EventType.OUTCOME, session_id, payload)

    async def on_preference_changed(
        self,
        preference_type: str,
        preference_value: str,
        session_id: Optional[str] = None,
    ) -> None:
        """Hook: user changed preference (ADR-0318)."""
        payload = asdict(PreferenceSetPayload(
            preference_key=preference_type,
            preference_value=preference_value,
        ))
        self._emit(EventType.PREFERENCE, session_id or "none", payload)
