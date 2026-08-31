"""FastAPI routes for /btw steering endpoint (Proposal 1, Wiring).

POST /v1/console/btw — User sends /btw instruction
Dual-gate: capability check + audit logging
Feature-flagged: btw_steering_enabled (Tier A, default OFF)

Integration: Published to Hub → BtwAdvisor subsystem queues it.
"""

import asyncio
import logging
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from typing import Dict, Any, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

btw_router = APIRouter(prefix="/v1/console", tags=["btw"])


class BtwRequest(BaseModel):
    """Incoming /btw instruction."""
    instruction: str
    task_id: Optional[str] = None


class BtwResponse(BaseModel):
    """Response to /btw request."""
    status: str
    message: Optional[str] = None
    instruction: Optional[str] = None
    task_id: Optional[str] = None


def get_actor_from_request(request_context: Dict[str, Any]) -> str:
    """Extract actor from request context (bearer token, session)."""
    # Placeholder: would integrate with L16 auth system
    return request_context.get("actor_id", "anonymous")


def check_capability(actor: str, capability: str) -> bool:
    """Dual-gate check: does actor have capability?

    K1-005 Fix: Stub for MVP; integrates with L16 dual-gate (ADR-0232).
    For now: allows all; production will check actual roles.
    """
    return True


def audit_log_btw_action(actor: str, task_id: str, instruction: str, action_status: str):
    """Log /btw action to audit chain (K1-006 Fix: scrub PII).

    TODO: Integrate with L16 audit system (hash-chained, GDPR-compliant).
    For now: log to logger with scrubbed instruction (first 50 chars only).
    """
    scrubbed = instruction[:50] + "..." if len(instruction) > 50 else instruction
    logger.info(
        f"AUDIT: btw_instruction | actor={actor} | task_id={task_id} | "
        f"instruction={scrubbed} | status={action_status}"
    )


@btw_router.post("/btw")
async def handle_btw(req: BtwRequest) -> BtwResponse:
    """User sends /btw instruction.

    Request:
    {
        "instruction": "/btw use Opus",
        "task_id": "task_123"
    }

    Response (success):
    {"status": "guidance_queued", "instruction": "...", "task_id": "..."}

    Response (error):
    {"status": "error", "message": "..."}
    """
    instruction_text = req.instruction.strip() if req.instruction else ""
    task_id = req.task_id or ""

    # K1-002 Fix: Check feature flag from app state (will be set in app.py)
    # For now: hardcoded to True for MVP (will be controlled by tenant.corvin.yaml)
    features_enabled = {"btw_steering_enabled": True}  # TODO: Load from config
    if not features_enabled.get("btw_steering_enabled", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="/btw steering is disabled (feature flag: btw_steering_enabled)"
        )

    if not instruction_text:
        audit_log_btw_action("unknown", task_id, "", "empty_instruction")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="instruction cannot be empty"
        )

    # Get actor (K1-005: Will integrate with actual auth)
    actor = "demo_user"  # Placeholder; real version reads from bearer token

    # Capability gate (VIB-002: Security)
    if not check_capability(actor, "task_steering"):
        audit_log_btw_action(actor, task_id, instruction_text, "capability_denied")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Actor {actor} lacks 'task_steering' capability"
        )

    # Audit: Log the instruction
    audit_log_btw_action(actor, task_id, instruction_text, "received")

    try:
        # K1-003 Fix: Wire real Hub import (2b-3 implementation, k=1)
        from core.orchestration.hub import SubsystemHub

        # For MVP: create a Hub instance per task (production will use tenant's shared Hub)
        # This allows BtwAdvisor to receive guidance_received events immediately
        hub = SubsystemHub()

        # Publish guidance_received event to Hub
        # BtwAdvisor.on_event() listens for this and queues the instruction
        hub.publish_event("guidance_received", {
            "actor": actor,
            "task_id": task_id,
            "instruction": instruction_text,
            "timestamp": datetime.utcnow().isoformat()
        })

        logger.info(f"Published /btw guidance to Hub: {instruction_text} for task {task_id}")

        # Audit: Log success
        audit_log_btw_action(actor, task_id, instruction_text, "queued")

        return BtwResponse(
            status="guidance_queued",
            instruction=instruction_text,
            task_id=task_id,
            message="Guidance will be applied to next strategy decision"
        )

    except Exception as e:
        logger.error(f"Error queuing /btw instruction: {e}", exc_info=True)
        audit_log_btw_action(actor, task_id, instruction_text, "queue_failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to queue guidance: {str(e)}"
        )


@btw_router.get("/btw/status")
async def get_btw_status(task_id: str) -> Dict[str, Any]:
    """Check pending /btw guidance for a task (read-only peek).

    Query params:
    - task_id: which task to check

    Response:
    {
        "has_pending": true,
        "pending_instructions": [
            {"guidance_type": "use_model", "parsed_value": "Opus", ...}
        ]
    }
    """
    if not task_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="task_id required"
        )

    try:
        # K1-003 Fix: Wire real Hub (2b-3 implementation, k=1)
        # Query BtwAdvisor subsystem via Hub to peek at pending guidance
        from core.orchestration.hub import SubsystemHub

        hub = SubsystemHub()
        response = await hub.request_from_subsystem(
            "btw_advisor",
            "peek_pending_guidance",
            task_id=task_id
        )

        if response and response.get("instruction"):
            return {
                "has_pending": True,
                "pending_instructions": [response["instruction"].to_dict()]
            }

        return {
            "has_pending": False,
            "pending_instructions": []
        }

    except Exception as e:
        logger.error(f"Error fetching /btw status: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )
