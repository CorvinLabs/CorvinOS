"""Routes for /btw Midstream Steering (ADR-0846)"""

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/v1/console", tags=["btw"])


@router.post("/btw")
async def handle_btw(chat_id: str, instruction: str):
    """User sends /btw instruction"""
    # TODO: Dual-gate check capability
    # TODO: Audit trail
    # TODO: Hub.publish_event("guidance_received", ...)
    return {"status": "guidance_queued", "instruction": instruction}
