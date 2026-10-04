"""Helper for recording feedback events in the learning loop (ADR-0314, ADR-0876).

Usage:
  from .feedback_emitter_helper import emit_feedback_event

  audit_ref = await emit_feedback_event(
      skill_id="os.video_producer",
      task_id=job_id,
      tenant_id=rec.tenant_id,
      outcome_feedback="yes",  # or "no"
      quality_rating=5,
      reason="Scene rendering perfect",
      confidence=0.9,
      source="user",
      scene_id="s01",
  )
  # audit_ref is None when NOTHING was recorded.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional
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
    scene_id: Optional[str] = None,
) -> Optional[str]:
    """Record a feedback event in the learning EventStore (audit-first, fail-closed).

    The write is SYNCHRONOUS (run off the event loop): ``EventStore.write_event``
    commits the tenant's audit-chain record first and only then the event, so a
    returned ``audit_ref`` means the record exists; ``None`` means nothing was
    recorded. The free-text ``reason`` is validated and scrubbed but never
    persisted (CLAUDE.md § ADR-0613) — only whether one was given is stored.
    """
    return await asyncio.to_thread(
        _record_feedback_sync, skill_id, task_id, tenant_id, outcome_feedback,
        quality_rating, preference_feedback, reason, confidence, source, lom, scene_id,
    )


def _record_feedback_sync(
    skill_id, task_id, tenant_id, outcome_feedback, quality_rating,
    preference_feedback, reason, confidence, source, lom, scene_id,
) -> Optional[str]:
    try:
        from core.learning.event_store import EventStore
        from core.learning.feedback_sink import (
            FeedbackEvent, FeedbackScrubber, FeedbackValidator,
            OutcomeFeedbackType, PreferenceFeedbackType,
        )
        from core.learning.learning_events import EventType, LearningEvent
        from core.paths.tenant import tenant_home

        if not tenant_id or not isinstance(tenant_id, str):
            logger.error("feedback_emitter: invalid tenant_id")
            return None

        # Fail closed: an unresolvable tenant is refused, never re-routed to a
        # hand-built path (that ignored CORVIN_HOME and skipped validation).
        try:
            event_store = EventStore(tenant_home(tenant_id), tenant_id=tenant_id)
        except Exception as e:  # noqa: BLE001
            logger.error("feedback_emitter: no event store for tenant (%s)", type(e).__name__)
            return None

        scrubbed_reason = None
        if reason:
            scrubbed_reason = FeedbackScrubber().scrub(reason)
            if scrubbed_reason is None:
                logger.warning("feedback_emitter: reason scrubbing failed, dropping reason")

        try:
            outcome_fb = OutcomeFeedbackType(outcome_feedback.lower()) if outcome_feedback else None
            preference_fb = PreferenceFeedbackType(preference_feedback.lower()) if preference_feedback else None
        except (ValueError, AttributeError):
            logger.warning("feedback_emitter: invalid feedback enum — not recorded")
            return None

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
        except Exception as e:  # noqa: BLE001
            logger.error("feedback_emitter: could not build FeedbackEvent (%s)", type(e).__name__)
            return None

        is_valid, error_msg = FeedbackValidator(event_store=event_store).validate(feedback_event)
        if not is_valid:
            logger.warning("feedback_emitter: validation failed: %s", error_msg)
            return None

        learning_event = LearningEvent(
            event_id=str(uuid4()),
            event_type=EventType.FEEDBACK,
            skill_id=skill_id,
            tenant_id=tenant_id,
            timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            version="1.0",
            signal={
                "feedback_id": feedback_event.feedback_id,
                # the join keys the per-job learning-metrics reader filters on
                "task_id": task_id,
                "scene_id": scene_id,
                "outcome_feedback": feedback_event.outcome_feedback.value if feedback_event.outcome_feedback else None,
                "quality_rating": feedback_event.quality_rating,
                "preference_feedback": feedback_event.preference_feedback.value if feedback_event.preference_feedback else None,
                "reason_given": bool(feedback_event.reason),
                "confidence": feedback_event.confidence,
                "source": feedback_event.source,
                "signature": feedback_event.signature,
                "signature_verified": feedback_event.signature_verified,
            },
            lom=lom,
        )

        # Synchronous, audit-first: the chain record is committed, then the event.
        audit_ref = event_store.write_event(learning_event)
        logger.info("feedback_emitter: feedback recorded (skill_id=%s, task_id=%s)", skill_id, task_id)
        return audit_ref or None

    except Exception as e:  # noqa: BLE001
        logger.error("feedback_emitter: feedback not recorded (%s)", type(e).__name__)
        return None


__all__ = ["emit_feedback_event"]
