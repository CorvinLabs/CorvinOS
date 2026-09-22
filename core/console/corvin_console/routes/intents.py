"""
Intent Router Routes — HTTP endpoints for intent classification.

POST /v1/console/intents/classify
GET  /v1/console/intents/recent

ADR-2028: Natural Language Intent Router
"""

import asyncio
import json
from typing import Optional, Dict, Any
from datetime import datetime
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from corvin_console.intent_router import (
    classify_intent,
    IntentType,
    IntentClassification
)
from core.plugins.corvin_plugins.providers import audit_backend
from core.compliance.consent import consent_required
from core.paths import tenant_audit_chain

router = APIRouter(prefix="/v1/console/intents", tags=["intents"])


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
@consent_required("intent_classification")  # User must consent to intent analysis
async def classify_user_intent(req: IntentClassifyRequest) -> IntentClassifyResponse:
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

        # Emit audit event (immutable, hash-chained)
        audit_event = {
            "tenant_id": req.tenant_id or "default",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "event_type": result.audit_event_type,
            "input_text": req.text[:100],  # Truncate for privacy
            "intent_type": result.intent_type.value,
            "confidence": float(result.confidence),
            "classifier_stage": result.classifier_stage,
            "latency_ms": float(result.latency_ms),
            "status": status,
        }

        # Write to audit chain (FAIL-CLOSED if chain fails)
        audit_chain_path = tenant_audit_chain(req.tenant_id or "default")
        audit_backend.write_event(
            event_type=result.audit_event_type,
            payload=audit_event,
            tenant_id=req.tenant_id or "default"
        )

        return IntentClassifyResponse(
            intent_type=result.intent_type.value,
            confidence=float(result.confidence),
            classifier_stage=result.classifier_stage,
            latency_ms=float(result.latency_ms),
            status=status
        )

    except Exception as e:
        # Log error + emit audit event
        import traceback
        logger_msg = f"Intent classification failed: {str(e)}"
        traceback.print_exc()

        audit_event = {
            "tenant_id": req.tenant_id or "default",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "event_type": "intent_classification_error",
            "error": str(e),
        }

        audit_backend.write_event(
            event_type="intent_classification_error",
            payload=audit_event,
            tenant_id=req.tenant_id or "default"
        )

        raise HTTPException(status_code=500, detail=logger_msg)


@router.get("/recent")
async def get_recent_intents(
    tenant_id: Optional[str] = Query(None),
    limit: int = Query(10, ge=1, le=100)
) -> list[IntentAuditEvent]:
    """
    Get recent intent classifications (read-only audit trail).

    Args:
        tenant_id: Tenant to query (required)
        limit: Max results (1-100)

    Returns:
        List of recent intent audit events
    """
    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id required")

    try:
        # Read audit chain (tenant-scoped)
        audit_chain_path = tenant_audit_chain(tenant_id)
        events = []

        # Placeholder: in production, read from audit.jsonl
        # For now, return empty list (E2E tests will mock this)

        return events[:limit]

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read audit trail: {str(e)}")


# Optional: Health check
@router.get("/health")
async def health_check() -> Dict[str, str]:
    """Health check for intent router."""
    return {
        "status": "ok",
        "classifier": "regex + llm (two-stage)",
    }
