"""Flow Guard console routes (ADR-2032).

HTTP endpoints for:
  - GET  /v1/console/flow/policy   — current flow policy
  - POST /v1/console/flow/feedback — record operator/outcome feedback
  - GET  /v1/console/flow/audit    — flow decision feedback trail

Data-security audit 2026-10-05: this module was written as a Flask
Blueprint, but the console is a FastAPI app (``app.py``) — ``app.include_
router()`` cannot mount a Flask Blueprint, so this route was never
reachable from any live request (confirmed: no import of this module
anywhere in ``app.py`` or its router list). Rewritten natively in FastAPI
and mounted below. The Flask version also read ``tenant_id`` from an
UNAUTHENTICATED query parameter (``request.args.get("tenant_id", ...)``)
— any caller could have read or fed feedback into ANY OTHER tenant's flow
policy just by changing that string. That gap never shipped because the
route was dead; this port takes the tenant from the authenticated session
instead, like every sibling route in this console.
"""
from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .. import auth as session_auth
from ..deps import require_session, require_session_csrf_on_mutation

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/flow", tags=["flow-guard"])

ReadSession = Annotated[session_auth.SessionRecord, Depends(require_session)]
WriteSession = Annotated[session_auth.SessionRecord, Depends(require_session_csrf_on_mutation)]

try:
    from core.skills.os_skills.flow_guard import FlowGuard  # type: ignore[import-not-found]
    from core.skills.os_skills.flow_guard.learning_integration import (  # type: ignore[import-not-found]
        LearningIntegration,
    )
except ImportError:  # pragma: no cover — graceful degradation, as the original intended
    FlowGuard = None  # type: ignore[assignment,misc]
    LearningIntegration = None  # type: ignore[assignment,misc]

_flow_guard_instances: dict[str, Any] = {}
_learning_integrations: dict[str, Any] = {}


def _tenant_of(rec: session_auth.SessionRecord) -> str:
    return rec.tenant_id or "_default"


def _get_flow_guard(tenant_id: str) -> Any | None:
    if FlowGuard is None:
        return None
    if tenant_id not in _flow_guard_instances:
        try:
            _flow_guard_instances[tenant_id] = FlowGuard(
                tenant_id=tenant_id, confidence_threshold=0.7, allow_uncertain_flows=False,
            )
        except Exception:
            logger.exception("Failed to create FlowGuard for tenant %s", tenant_id)
            return None
    return _flow_guard_instances[tenant_id]


def _get_learning(tenant_id: str) -> Any | None:
    if LearningIntegration is None:
        return None
    if tenant_id not in _learning_integrations:
        flow_guard = _get_flow_guard(tenant_id)
        if flow_guard is None:
            return None
        try:
            _learning_integrations[tenant_id] = LearningIntegration(
                tenant_id=tenant_id, flow_guard=flow_guard, audit_backend=None,
            )
        except Exception:
            logger.exception("Failed to create LearningIntegration for tenant %s", tenant_id)
            return None
    return _learning_integrations[tenant_id]


@router.get("/policy")
def get_flow_policy(rec: ReadSession) -> dict[str, Any]:
    """Current flow policy for the session's own tenant."""
    tenant_id = _tenant_of(rec)
    flow_guard = _get_flow_guard(tenant_id)
    if flow_guard is None:
        raise HTTPException(status_code=503, detail="flow guard not available")
    policy = flow_guard.get_policy()
    rules = [{
        "data_class": r.data_class,
        "destination_engine": r.destination_engine,
        "decision": r.decision.value,
        "confidence": r.confidence,
        "feedback_count": getattr(r, "feedback_count", 0),
    } for r in policy.rules]
    allow_count = sum(1 for r in policy.rules if r.decision.value == "allow")
    deny_count = sum(1 for r in policy.rules if r.decision.value == "deny")
    avg_confidence = (sum(r.confidence for r in policy.rules) / len(policy.rules)
                      if policy.rules else 0.0)
    return {
        "tenant_id": tenant_id,
        "rules": rules,
        "summary": {
            "total_rules": len(policy.rules),
            "allow_rules": allow_count,
            "deny_rules": deny_count,
            "avg_confidence": round(avg_confidence, 3),
        },
    }


class FlowFeedbackBody(BaseModel):
    data_class: str = Field(min_length=1)
    destination_engine: str = Field(min_length=1)
    result: str = Field(min_length=1)
    reasoning: str = ""


@router.post("/feedback", status_code=201)
def post_flow_feedback(body: FlowFeedbackBody, rec: WriteSession) -> dict[str, Any]:
    """Record operator or outcome feedback on a flow decision, for the
    session's own tenant."""
    tenant_id = _tenant_of(rec)
    flow_guard = _get_flow_guard(tenant_id)
    learning = _get_learning(tenant_id)
    if flow_guard is None or learning is None:
        raise HTTPException(status_code=503, detail="flow guard not available")

    if body.result in ("approved", "rejected"):
        event = learning.process_operator_feedback(
            data_class=body.data_class, destination_engine=body.destination_engine,
            approval=(body.result == "approved"), reasoning=body.reasoning,
        )
    else:
        # process_flow_outcome only reads these 3 attributes off `evaluation`
        # (same minimal shim the pre-port Flask code used — constructing a
        # real FlowEvaluation needs a FlowDecision this endpoint never has).
        dummy_eval = type("obj", (object,), {
            "data_class": body.data_class,
            "destination_engine": body.destination_engine,
            "policy_confidence": 0.5,
        })()
        try:
            event = learning.process_flow_outcome(
                evaluation=dummy_eval, outcome_result=body.result, reasoning=body.reasoning,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    return {
        "event_id": event.event_id,
        "status": "recorded",
        "confidence_before": event.confidence_before,
        "confidence_after": event.confidence_after,
        "timestamp": event.timestamp,
    }


@router.get("/audit")
def get_flow_audit(
    rec: ReadSession,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    data_class: str | None = None,
    destination_engine: str | None = None,
    result: str | None = None,
) -> dict[str, Any]:
    """Flow-decision feedback trail for the session's own tenant."""
    tenant_id = _tenant_of(rec)
    learning = _get_learning(tenant_id)
    if learning is None:
        raise HTTPException(status_code=503, detail="flow guard not available")

    events = learning.get_feedback_history()
    if data_class:
        events = [e for e in events if e.data_class == data_class]
    if destination_engine:
        events = [e for e in events if e.destination_engine == destination_engine]
    if result:
        events = [e for e in events if result in e.result]

    shown = events[-limit:]
    audit_events = [{
        "event_id": e.event_id,
        "timestamp": e.timestamp,
        "data_class": e.data_class,
        "destination_engine": e.destination_engine,
        "result": e.result,
        "confidence_before": e.confidence_before,
        "confidence_after": e.confidence_after,
        "reasoning": e.reasoning,
        "feedback_type": e.feedback_type.value,
        "lom": e.lom,
    } for e in shown]
    result_counts: dict[str, int] = {}
    for e in events:
        result_counts[e.result] = result_counts.get(e.result, 0) + 1
    avg_confidence_after = (sum(e.confidence_after for e in events) / len(events)
                            if events else 0.0)
    return {
        "tenant_id": tenant_id,
        "audit_events": audit_events,
        "summary": {
            "total_events": len(events),
            "result_distribution": result_counts,
            "avg_confidence_after": round(avg_confidence_after, 3),
            "results_shown": len(audit_events),
        },
    }
