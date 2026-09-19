"""Helper module for emitting feedback events to the learning loop (ADR-0314, ADR-0876).

This module provides a reusable interface for routes to emit feedback events
without duplicating EventEmitter/EventStore initialization logic.

Usage:
  from .feedback_emitter_helper import emit_feedback_event

  success = await emit_feedback_event(
      skill_id="os.video_producer",
      task_id=job_id,
      tenant_id=tenant_id,
      outcome_feedback="yes",  # or "no" or "unknown"
      quality_rating=5,
      reason="Scene rendering perfect",
      confidence=0.9,
      source="user"
  )
"""

import logging
from pathlib import Path
from typing import Optional
from datetime import datetime, timezone
from uuid import uuid4

logger = logging.getLogger(__name__)


async def emit_feedback_event(
    skill_id: str,
    task_id: str,
    tenant_id: str,
    outcome_feedback: Optional[str] = None,
    quality_rating: Optional[int] = None,
    preference_feedback: Optional[str] = None,
    reason: Optional[str] = None,
    confidence: Optional[float] = None,
    source: str = "user",
    lom: Optional[str] = None,
) -> bool:
    """Emit a feedback event to the learning EventStore (audit-first, fail-closed).

    Args:
        skill_id: Skill this feedback is about (e.g., "os.video_producer")
        task_id: Task/job ID this feedback relates to
        tenant_id: Tenant scope (GDPR Art. 32)
        outcome_feedback: "yes" | "no" | "unknown" — correctness
        quality_rating: 1–5 stars (optional)
        preference_feedback: "llm" | "deterministic" | "either" (optional)
        reason: User's explanation (scrubbed of PII)
        confidence: User's confidence in feedback (0–1)
        source: "user" | "system" | "audit"
        lom: Line of Moral Responsibility (code location)

    Returns:
        True if feedback was successfully emitted, False on error (fail-soft)
    """
    try:
        # Import learning infrastructure (lazy load to avoid startup dependency)
        from core.learning.event_emitter import EventEmitter
        from core.learning.event_store import EventStore
        from core.learning.feedback_sink import FeedbackEvent, FeedbackScrubber, FeedbackValidator, OutcomeFeedbackType, PreferenceFeedbackType
        from core.paths.tenant import tenant_home

        # Validate tenant_id
        if not tenant_id or not isinstance(tenant_id, str):
            logger.error(f"feedback_emitter: invalid tenant_id={tenant_id!r}")
            return False

        # Get tenant home directory and initialize EventStore
        try:
            _tenant_home = tenant_home(tenant_id)
        except Exception as e:
            logger.warning(f"feedback_emitter: could not resolve tenant home: {e}, using fallback")
            _tenant_home = Path.home() / ".corvin" / "tenants" / tenant_id

        # Initialize EventStore (audit-first, fail-closed)
        try:
            event_store = EventStore(_tenant_home, tenant_id=tenant_id)
        except Exception as e:
            logger.error(f"feedback_emitter: failed to initialize EventStore: {e}")
            return False

        # Initialize EventEmitter (non-blocking, fire-and-forget)
        try:
            emitter = EventEmitter(event_store)
        except Exception as e:
            logger.error(f"feedback_emitter: failed to initialize EventEmitter: {e}")
            return False

        # Scrub reason of PII (GDPR Art. 5 minimization)
        scrubbed_reason = None
        if reason:
            scrubber = FeedbackScrubber()
            scrubbed_reason = scrubber.scrub(reason)
            if scrubbed_reason is None:
                logger.warning(f"feedback_emitter: reason scrubbing failed, dropping reason")

        # Convert string enums to proper types
        outcome_fb = None
        if outcome_feedback:
            try:
                outcome_fb = OutcomeFeedbackType(outcome_feedback.lower())
            except (ValueError, AttributeError):
                logger.warning(f"feedback_emitter: invalid outcome_feedback={outcome_feedback}")

        preference_fb = None
        if preference_feedback:
            try:
                preference_fb = PreferenceFeedbackType(preference_feedback.lower())
            except (ValueError, AttributeError):
                logger.warning(f"feedback_emitter: invalid preference_feedback={preference_feedback}")

        # Create FeedbackEvent (immutable, frozen dataclass)
        try:
            feedback_event = FeedbackEvent.create(
                skill_id=skill_id,
                task_id=task_id,
                tenant_id=tenant_id,
                outcome_feedback=outcome_fb,
                quality_rating=quality_rating,
                preference_feedback=preference_fb,
                reason=scrubbed_reason,
                confidence=confidence,
                source=source,
                lom=lom,
            )
        except Exception as e:
            logger.error(f"feedback_emitter: failed to create FeedbackEvent: {e}")
            return False

        # Validate feedback (fail-closed: invalid feedback is dropped)
        validator = FeedbackValidator(event_store=event_store)
        is_valid, error_msg = validator.validate(feedback_event)
        if not is_valid:
            logger.warning(f"feedback_emitter: validation failed: {error_msg}")
            return False

        # Emit event to EventStore (non-blocking, audit-first)
        # Convert FeedbackEvent to LearningEvent for EventStore
        from core.learning.learning_events import LearningEvent, EventType

        learning_event = LearningEvent(
            event_id=str(uuid4()),
            event_type=EventType.FEEDBACK,
            skill_id=skill_id,
            tenant_id=tenant_id,
            timestamp=datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
            version="1.0",
            signal={
                "feedback_id": feedback_event.feedback_id,
                "outcome_feedback": feedback_event.outcome_feedback.value if feedback_event.outcome_feedback else None,
                "quality_rating": feedback_event.quality_rating,
                "preference_feedback": feedback_event.preference_feedback.value if feedback_event.preference_feedback else None,
                "reason": feedback_event.reason,
                "confidence": feedback_event.confidence,
                "source": feedback_event.source,
                "signature": feedback_event.signature,
                "signature_verified": feedback_event.signature_verified,
            },
            lom=lom,
        )

        # Emit to EventStore (audit-first, fail-closed)
        success = emitter.emit(learning_event)
        if not success:
            logger.error(f"feedback_emitter: EventEmitter queue full, feedback dropped (dropped_count={emitter.dropped})")
            return False

        logger.info(
            f"feedback_emitter: ✓ feedback emitted (feedback_id={feedback_event.feedback_id}, "
            f"skill_id={skill_id}, task_id={task_id}, tenant_id={tenant_id})"
        )
        return True

    except Exception as e:
        logger.exception(f"feedback_emitter: unexpected error: {e}")
        return False


__all__ = ["emit_feedback_event"]
