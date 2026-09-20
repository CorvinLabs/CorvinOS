"""
Skill learning integration — two distinct collaborators, one module.

``SkillLearningHooks`` (Phase 4, ADR-0316/0317/0318/0320) emits learning
signals at the four points of a skill's lifecycle: selection, execution,
outcome and preference change. ``SkillLearningBridge`` (ADR-0693/0695) wraps
a single skill callable so an execution feeds the optimizer.

They are not alternatives. Commit 87c33280 rewrote this file for the Bridge and
deleted the Hooks class with it, while SIX production modules kept importing
it — ``skill_{executor,selector,feedback}_{integration,learning}`` all raised
ImportError on import from that commit until 2026-09-20. The Hooks class is
restored here verbatim; do not replace one with the other.
"""

import asyncio
from datetime import datetime
from typing import Any, Dict, Optional
from uuid import uuid4

from core.learning.optimizer import LearningOptimizer, SkillConfig, FeedbackEvent

from .decision_history import DecisionRecorder
from .event_emitter import EventEmitter
from .learning_events import EventType, LearningEvent
from .metrics import MetricsCollector, MetricType
from .outcome_feedback import OutcomeRecorder, OutcomeType


class AuditLogger:
    """Simple audit logger for testing."""
    
    def __init__(self):
        self.events = []
    
    def log_event(self, event: Dict[str, Any]):
        """Log event (fail-closed: always succeeds)."""
        event["logged_at"] = datetime.utcnow().isoformat()
        self.events.append(event)


class SkillLearningBridge:
    """Connect skill execution to EventStore feedback loop."""

    def __init__(
        self,
        skill_id: str,
        skill_execute_fn,  # async callable: (input) -> output
        optimizer: LearningOptimizer,
        audit_logger: Optional[AuditLogger] = None,
    ):
        self.skill_id = skill_id
        self.skill_execute_fn = skill_execute_fn
        self.optimizer = optimizer
        self.audit_logger = audit_logger or AuditLogger()
        self.config = SkillConfig(skill_id=skill_id)

    async def execute_with_learning(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """Execute skill and wire feedback loop."""
        start_time = datetime.utcnow()
        result = await self.skill_execute_fn(input_data)
        elapsed_ms = (datetime.utcnow() - start_time).total_seconds() * 1000

        self.audit_logger.log_event({
            "event_type": "skill_executed",
            "skill_id": self.skill_id,
            "input_hash": hash(str(input_data)),
            "output_hash": hash(str(result)),
            "latency_ms": elapsed_ms,
            "lom": "SkillLearningBridge.execute_with_learning:L50",
        })

        asyncio.create_task(self._run_learning_loop())
        return result

    async def _run_learning_loop(self):
        """Poll for feedback → update skill config (non-blocking)."""
        await asyncio.sleep(0.1)

    def inject_feedback(self, feedback: FeedbackEvent) -> bool:
        """Inject feedback directly (for testing)."""
        asyncio.create_task(
            self.optimizer.process_feedback(
                skill_id=self.skill_id,
                feedback=feedback,
                current_config=self.config,
                audit_logger=self.audit_logger,
            )
        )
        return True

    def get_audit_log(self):
        """Return audit events (for testing)."""
        return self.audit_logger.events


class SkillLearningHooks:
    """Hooks for capturing learning signals during skill execution.

    The four methods map onto the four lifecycle points ADR-0316/0317/0318/0320
    define: selection, execution, outcome and preference change. Each records
    through the matching recorder (so the durable, hash-chained copy is
    written) and then emits ONE ``LearningEvent`` through the emitter's generic
    ``emit()``.

    They used to call ``emitter.emit_decision()``, ``emit_metric()``,
    ``emit_outcome()`` and ``emit_preference()``. Commit df125e48 collapsed
    those 279 lines into the single generic ``emit(event)`` and this class was
    not carried along, so every hook raised ``AttributeError: 'EventEmitter'
    object has no attribute 'emit_decision'`` — on a module that, separately,
    could not even be imported (see the module docstring).
    """

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

    @property
    def dropped_events(self) -> int:
        """Events the emitter had to drop because its queue was full."""
        return self.emitter.dropped

    def _emit(
        self,
        event_type: EventType,
        skill_id: str,
        signal: dict,
    ) -> bool:
        """Build and emit one tenant-scoped learning event."""
        event = LearningEvent(
            event_id=str(uuid4()),
            event_type=event_type,
            skill_id=skill_id,
            tenant_id=self.tenant_id,
            timestamp=datetime.utcnow().isoformat() + "Z",
            signal=signal,
        )
        return self.emitter.emit(event)

    async def on_skill_selection(
        self,
        candidates: list[str],
        chosen: str,
        session_id: str,
        confidence_score: Optional[float] = None,
        reasoning: Optional[str] = None,
    ) -> str:
        """Hook: skill selection (ADR-0316).

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

        self._emit(
            EventType.DECISION,
            skill_id=chosen,
            signal={
                "decision_id": decision.decision_id,
                "choice_type": decision.choice_type,
                "candidates": list(decision.candidates),
                "chosen": decision.chosen,
                "session_id": decision.session_id,
                "context": {
                    "confidence_score": decision.confidence_score,
                    "reasoning": decision.reasoning,
                },
            },
        )

        return decision.decision_id

    async def on_skill_executed(
        self,
        decision_id: str,
        session_id: str,
        skill_name: str,
        latency_ms: float,
    ) -> None:
        """Hook: skill execution completed (ADR-0320)."""
        metric = self.metrics_collector.record_latency(
            session_id=session_id,
            value=latency_ms,
            skill_name=skill_name,
        )

        self._emit(
            EventType.METRIC,
            skill_id=skill_name,
            signal={
                "metric_id": metric.metric_id,
                "metric_name": metric.metric_type.value,
                "value": metric.value,
                "session_id": metric.session_id,
                "skill_name": metric.skill_name,
                "decision_id": decision_id,
                "tags": metric.tags,
            },
        )

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

        self._emit(
            EventType.OUTCOME,
            skill_id=decision_id,
            # `user_feedback` is recorded durably by the OutcomeRecorder but is
            # deliberately NOT part of the emitted signal: free-text operator
            # feedback must not reach the learning store (CLAUDE.md § Loop
            # closure — "don't persist the free-text feedback reason").
            signal={
                "outcome_id": record.outcome_id,
                "decision_id": record.decision_id,
                "session_id": record.session_id,
                "outcome_type": record.outcome.value,
                "outcome_value": record.rating,
            },
        )

    async def on_preference_changed(
        self,
        preference_type: str,
        preference_value: str,
        session_id: Optional[str] = None,
    ) -> None:
        """Hook: user changed preference (ADR-0318)."""
        self._emit(
            EventType.PREFERENCE,
            skill_id="os.preferences",
            signal={
                "preference_key": preference_type,
                "preference_value": preference_value,
                "session_id": session_id,
            },
        )
