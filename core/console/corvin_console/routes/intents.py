"""
Intent Router Routes — HTTP endpoints for intent classification.

POST /v1/console/intents/classify
GET  /v1/console/intents/recent

ADR-2028: Natural Language Intent Router
"""

import asyncio
import json
import logging
from typing import Optional, Dict, Any
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from corvin_console.intent_router import (
    classify_intent,
    IntentType,
    IntentClassification
)
from core.compliance.consent import consent_required
from core.paths import tenant_audit_chain

from .. import audit as console_audit
from .. import auth as session_auth
from ..deps import require_session
from ..deps import require_session_csrf_on_mutation

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_session_csrf_on_mutation)], prefix="/intents", tags=["intents"])


class IntentClassifyRequest(BaseModel):
    """Request to classify an intent."""
    text: str
    tenant_id: Optional[str] = None


class IntentClassifyResponse(BaseModel):
    """Response from intent classification."""
    intent_type: str
    confidence: float
    classifier_stage: str
    latency_ms: float
    status: str  # "success" | "ambiguous" | "error"


class IntentAuditEvent(BaseModel):
    """Audit event for intent classification."""
    tenant_id: str
    timestamp: str
    event_type: str
    input_text: str
    intent_type: str
    confidence: float
    classifier_stage: str
    latency_ms: float
    status: str


@router.post("/classify")
async def classify_user_intent(
    req: IntentClassifyRequest,
    rec: session_auth.SessionRecord = Depends(require_session),
) -> IntentClassifyResponse:
    """
    Classify a user intent using two-stage pipeline.

    Stage 1: Regex (fast, fallback)
    Stage 2: LLM (accurate, preferred)

    Args:
        req: IntentClassifyRequest with user text

    Returns:
        IntentClassifyResponse with classification result

    Raises:
        HTTPException: On classification error
    """
    # User must consent to intent analysis. Called with the session record
    # rather than wired via Depends(), so ``rec`` cannot be query-supplied.
    await consent_required("intent_classification")(rec)
    tenant_id = rec.tenant_id

    try:
        # Classify intent
        result: IntentClassification = await classify_intent(req.text)

        # Determine status
        if result.intent_type == IntentType.AMBIGUOUS:
            status = "ambiguous"
        elif result.intent_type == IntentType.ERROR:
            status = "error"
        else:
            status = "success"

        # Audit (hash-chained, session tenant). Content-free: the classification
        # and its measurements only — never the user's text (PII floor).
        console_audit.system_event(
            tenant_id=tenant_id,
            event=result.audit_event_type,
            details={
                "intent_type": result.intent_type.value,
                "confidence": float(result.confidence),
                "classifier_stage": result.classifier_stage,
                "latency_ms": float(result.latency_ms),
                "status": status,
                "text_len": len(req.text),
            },
        )

        return IntentClassifyResponse(
            intent_type=result.intent_type.value,
            confidence=float(result.confidence),
            classifier_stage=result.classifier_stage,
            latency_ms=float(result.latency_ms),
            status=status
        )

    except Exception as e:
        # Record the failure content-free (error TYPE only — a message can carry
        # the user's text) and answer 500 without echoing internals.
        logger.exception("Intent classification failed")
        try:
            console_audit.system_event(
                tenant_id=tenant_id,
                event="intent_classification_error",
                details={"error_type": type(e).__name__},
                severity="WARNING",
            )
        except Exception:  # noqa: BLE001 — the 500 below is the answer either way
            logger.exception("intent_classification_error audit write failed")
        logger_msg = "Intent classification failed"
        raise HTTPException(status_code=500, detail=logger_msg)


@router.get("/recent")
async def get_recent_intents(
    limit: int = Query(10, ge=1, le=100),
    rec: session_auth.SessionRecord = Depends(require_session),
) -> list[IntentAuditEvent]:
    """
    Get recent intent classifications (read-only audit trail).

    SECURITY FIX (2026-09-22):
    - Added require_session authentication
    - Tenant is determined from authenticated session (not user input)
    - Users can only read their own tenant's audit trail

    Args:
        limit: Max results (1-100)
        rec: Authenticated session record

    Returns:
        List of recent intent audit events for authenticated user's tenant
    """
    # CRITICAL FIX: tenant_id comes from authenticated session, NOT user input
    tenant_id = rec.tenant_id

    try:
        # Read audit chain (tenant-scoped, authenticated)
        audit_chain_path = tenant_audit_chain(tenant_id)
        events = []

        # Placeholder: in production, read from audit.jsonl
        # For now, return empty list (E2E tests will mock this)

        logger = __import__('logging').getLogger(__name__)
        logger.info(f"Recent intents query: user={rec.sid} tenant={tenant_id} limit={limit}")

        return events[:limit]

    except Exception as e:
        logger = __import__('logging').getLogger(__name__)
        logger.error(f"Failed to read audit trail for tenant={tenant_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to read audit trail: {str(e)}")


# Optional: Health check
@router.get("/health")
async def health_check() -> Dict[str, str]:
    """Health check for intent router."""
    return {
        "status": "ok",
        "classifier": "regex + llm (two-stage)",
    }
