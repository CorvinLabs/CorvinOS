"""Skill Learning Loop for Model Selection Phase 2 k=1 (ADR-0683)."""

from datetime import datetime
from typing import Dict, Any, Optional, List
from core.skills.models.learning_event import (
    LearningEvent, LearningEventType, LearningEventStore,
    SkillExecutedEvent, OutcomeFeedbackEvent, ConfidenceScoreEvent,
    FeedbackOutcome
)
import logging

logger = logging.getLogger(__name__)


class SkillLearningLoop:
    """
    Learning loop for Skill optimization.
    
    - Collects execution events (latency, errors)
    - Collects user feedback (was the output correct?)
    - Computes confidence scores
    - Ready for k=2 (confidence aggregation)
    
    ADR-0683 k=1: Event schema + collection only.
    """
    
    def __init__(self, skill_id: str, tenant_id: str):
        self.skill_id = skill_id
        self.tenant_id = tenant_id
        self.event_store = LearningEventStore(tenant_id)
        self.confidence_history: List[float] = [0.5]  # Start at baseline
        
    def record_execution(
        self,
        input_data: Dict[str, Any],
        output_data: Dict[str, Any],
        latency_ms: float,
        error: Optional[str] = None,
        lom: str = ""
    ) -> SkillExecutedEvent:
        """
        Record a skill execution event.
        
        Args:
            input_data: Skill input
            output_data: Skill output
            latency_ms: Execution time in milliseconds
            error: Error message if execution failed
            lom: Line of Moral Responsibility (caller's frame)
        
        Returns:
            SkillExecutedEvent (immutable, hash-chained)
        """
        event = SkillExecutedEvent(
            skill_id=self.skill_id,
            tenant_id=self.tenant_id,
            timestamp=datetime.utcnow(),
            input=input_data,
            output=output_data,
            latency_ms=latency_ms,
            error=error,
            lom=lom,
        )
        return self.event_store.append_event(event)
    
    def record_outcome_feedback(
        self,
        outcome: FeedbackOutcome,
        reason: str = "",
        lom: str = ""
    ) -> OutcomeFeedbackEvent:
        """
        Record user feedback on skill outcome.
        
        Args:
            outcome: CORRECT, INCORRECT, PARTIAL, UNKNOWN
            reason: User-provided reason (NOT stored in audit, GDPR Art. 5)
            lom: Caller's frame for audit trail
        
        Returns:
            OutcomeFeedbackEvent (immutable, never logs reason)
        """
        event = OutcomeFeedbackEvent(
            skill_id=self.skill_id,
            tenant_id=self.tenant_id,
            timestamp=datetime.utcnow(),
            input={"outcome": outcome.value},
            output={},
            outcome=outcome,
            reason="",  # Never stored; only used for immediate UI feedback
            lom=lom,
        )
        logger.info(
            f"Feedback recorded for skill {self.skill_id}: {outcome.value} "
            f"(reason hint: {reason[:50]}...)" if reason else f"(no reason)"
        )
        return self.event_store.append_event(event)
    
    def record_confidence_score(
        self,
        confidence: float,
        basis: str = "",
        lom: str = ""
    ) -> ConfidenceScoreEvent:
        """
        Record confidence score observation.
        
        Args:
            confidence: 0.0-1.0 confidence that skill makes correct decisions
            basis: How confidence was calculated (e.g., "0.7 * success_rate + 0.3 * feedback_engagement")
            lom: Caller's frame for audit trail
        
        Returns:
            ConfidenceScoreEvent (immutable, hash-chained)
        """
        if not (0.0 <= confidence <= 1.0):
            raise ValueError(f"Confidence must be 0.0-1.0, got {confidence}")
        
        event = ConfidenceScoreEvent(
            skill_id=self.skill_id,
            tenant_id=self.tenant_id,
            timestamp=datetime.utcnow(),
            input={},
            output={"confidence": confidence},
            confidence=confidence,
            basis=basis,
            lom=lom,
        )
        self.confidence_history.append(confidence)
        return self.event_store.append_event(event)
    
    def get_events(self, event_type: Optional[LearningEventType] = None) -> List[LearningEvent]:
        """Retrieve events (tenant-scoped, immutable)."""
        return self.event_store.get_events(event_type)
    
    def event_count(self, event_type: LearningEventType) -> int:
        """Count events by type."""
        return self.event_store.event_count(event_type)
    
    def current_confidence(self) -> float:
        """Get latest confidence score."""
        return self.confidence_history[-1] if self.confidence_history else 0.5
    
    def confidence_stable(self, window: int = 10, tolerance: float = 0.05) -> bool:
        """
        Check if confidence is stable over recent window.
        
        Ready for k=2 optimization if:
        - At least `window` observations collected
        - Last `window` scores vary by ≤ tolerance
        """
        if len(self.confidence_history) < window:
            return False
        
        recent = self.confidence_history[-window:]
        min_conf = min(recent)
        max_conf = max(recent)
        return (max_conf - min_conf) <= tolerance
    
    def to_audit_log(self) -> str:
        """Serialize all events to JSONL for audit trail (ADR-0232)."""
        return self.event_store.to_jsonl()
