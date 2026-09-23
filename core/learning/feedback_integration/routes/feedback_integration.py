"""Stream 4: Feedback Integration Routes — /v1/console/learning/<skill>/feedback (ADR-2050).

Four POST endpoints for operator feedback:
  POST /v1/console/learning/workflow-optimizer/feedback → outcome_feedback
  POST /v1/console/learning/security-orchestrator/incident → outcome_feedback (security-specific)
  POST /v1/console/learning/flow-guard/policy-feedback → preference_feedback
  POST /v1/console/learning/metrics/observe → metric_observed

All endpoints:
  - Require CSRF token (@require_csrf)
  - Require authenticated session (@require_session)
  - Scope feedback to tenant_id from SessionRecord
  - Write to ADR-0314 EventStore (audit-first)
  - Never persist free-text reason field (presence + length only)
  - Return 400 on invalid input (with reason), never 500
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status

from core.security.csrf import require_csrf
from core.console.corvin_console.deps import require_session
from core.console.corvin_console.models import SessionRecord
from ..models.feedback_event import (
    FeedbackEvent,
    FeedbackType,
    OutcomeChoice,
    PreferenceChoice,
)
from ..models.feedback_validator import (
    OutcomeFeedbackRequest,
    PreferenceFeedbackRequest,
    ConfidenceFeedbackRequest,
    MetricFeedbackRequest,
    FeedbackResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# Optional: core.learning integration (ADR-0314)
try:
    from core.learning.event_store import EventStore
    from core.paths.tenant import tenant_home
except ImportError:
    EventStore = None
    tenant_home = None


def _require_learning() -> None:
    """503 on stripped install (no EventStore)."""
    if EventStore is None:
        raise HTTPException(status_code=503, detail="learning subsystem not wired (EventStore unavailable)")


def _get_event_store(session: SessionRecord) -> EventStore:
    """Get EventStore bound to session's tenant."""
    _require_learning()
    if tenant_home is None:
        raise HTTPException(status_code=503, detail="tenant_home unavailable")
    th = tenant_home(session.tenant_id)
    return EventStore(th, tenant_id=session.tenant_id)


@router.post(
    "/v1/console/learning/workflow-optimizer/feedback",
    response_model=FeedbackResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
@require_csrf
async def feedback_workflow_optimizer(
    request: OutcomeFeedbackRequest,
    session: SessionRecord = Depends(require_session),
) -> FeedbackResponse:
    """Workflow Optimizer feedback: was routing decision correct?

    POST /v1/console/learning/workflow-optimizer/feedback
    {
      "feedback_type": "outcome_feedback",
      "skill_id": "os.workflow_optimizer",
      "outcome": "yes",
      "task_id": "<task-id>",
      "reason": "correct routing choice"
    }
    """
    try:
        store = _get_event_store(session)

        # Create feedback event
        event = FeedbackEvent.create(
            feedback_type=FeedbackType.OUTCOME,
            skill_id=request.skill_id,
            tenant_id=session.tenant_id,
            outcome=request.outcome,
            task_id=request.task_id,
            reason=request.reason,
            lom="feedback_integration.feedback_workflow_optimizer",
        )

        # Write to EventStore (audit-first)
        store.write_event(event)

        logger.info(
            f"Feedback recorded: {event.feedback_id} (skill={request.skill_id}, outcome={request.outcome})"
        )

        return FeedbackResponse(
            feedback_id=event.feedback_id,
            feedback_type=request.feedback_type,
            skill_id=request.skill_id,
            timestamp=event.timestamp,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.error(f"Feedback error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.post(
    "/v1/console/learning/security-orchestrator/incident",
    response_model=FeedbackResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
@require_csrf
async def feedback_security_orchestrator(
    request: OutcomeFeedbackRequest,
    session: SessionRecord = Depends(require_session),
) -> FeedbackResponse:
    """Security Orchestrator feedback: was threat detection correct?

    POST /v1/console/learning/security-orchestrator/incident
    {
      "feedback_type": "outcome_feedback",
      "skill_id": "os.security_orchestrator",
      "outcome": "no",
      "reason": "false alarm"
    }
    """
    try:
        store = _get_event_store(session)

        # Override skill_id for security feedback
        event = FeedbackEvent.create(
            feedback_type=FeedbackType.OUTCOME,
            skill_id="os.security_orchestrator",
            tenant_id=session.tenant_id,
            outcome=request.outcome,
            task_id=request.task_id,
            reason=request.reason,
            lom="feedback_integration.feedback_security_orchestrator",
        )

        store.write_event(event)

        logger.info(
            f"Security feedback recorded: {event.feedback_id} (outcome={request.outcome})"
        )

        return FeedbackResponse(
            feedback_id=event.feedback_id,
            feedback_type=request.feedback_type,
            skill_id="os.security_orchestrator",
            timestamp=event.timestamp,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.error(f"Security feedback error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.post(
    "/v1/console/learning/flow-guard/policy-feedback",
    response_model=FeedbackResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
@require_csrf
async def feedback_flow_guard(
    request: PreferenceFeedbackRequest,
    session: SessionRecord = Depends(require_session),
) -> FeedbackResponse:
    """Flow Guard feedback: deterministic or LLM-based policy?

    POST /v1/console/learning/flow-guard/policy-feedback
    {
      "feedback_type": "preference_feedback",
      "skill_id": "os.flow_guard",
      "preference": "deterministic",
      "policy_class": "pii",
      "reason": "prefer strict rules"
    }
    """
    try:
        store = _get_event_store(session)

        event = FeedbackEvent.create(
            feedback_type=FeedbackType.PREFERENCE,
            skill_id="os.flow_guard",
            tenant_id=session.tenant_id,
            preference=request.preference,
            reason=request.reason,
            lom="feedback_integration.feedback_flow_guard",
        )

        store.write_event(event)

        logger.info(
            f"Flow Guard feedback recorded: {event.feedback_id} (preference={request.preference})"
        )

        return FeedbackResponse(
            feedback_id=event.feedback_id,
            feedback_type=request.feedback_type,
            skill_id="os.flow_guard",
            timestamp=event.timestamp,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.error(f"Flow Guard feedback error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.post(
    "/v1/console/learning/metrics/observe",
    response_model=FeedbackResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
@require_csrf
async def feedback_metrics(
    request: MetricFeedbackRequest,
    session: SessionRecord = Depends(require_session),
) -> FeedbackResponse:
    """Metrics feedback: observe latency, cost, accuracy.

    POST /v1/console/learning/metrics/observe
    {
      "feedback_type": "metric_observed",
      "skill_id": "os.workflow_optimizer",
      "metric_name": "latency_ms",
      "metric_value": 42,
      "dimension": "workflow_optimizer"
    }
    """
    try:
        store = _get_event_store(session)

        event = FeedbackEvent.create(
            feedback_type=FeedbackType.METRIC,
            skill_id=request.skill_id,
            tenant_id=session.tenant_id,
            metric_name=request.metric_name,
            metric_value=request.metric_value,
            lom="feedback_integration.feedback_metrics",
        )

        store.write_event(event)

        logger.info(
            f"Metric recorded: {event.feedback_id} ({request.metric_name}={request.metric_value})"
        )

        return FeedbackResponse(
            feedback_id=event.feedback_id,
            feedback_type=request.feedback_type,
            skill_id=request.skill_id,
            timestamp=event.timestamp,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.error(f"Metrics feedback error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error") from e
