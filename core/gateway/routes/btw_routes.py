"""Routes for /btw Midstream Steering (ADR-0846).

Status correction (ADR-2220, 2026-10-05): this module is NOT mounted into
any running app (`grep -rn "btw_routes" --include='*.py'` outside tests
finds no import of this module anywhere — the former "PRODUCTION READY"
claim above was never true in the way it reads). Even if it were mounted,
the handler below never touches a running `claude` subprocess — it is a
TODO stub (no capability gate, no audit, no Hub publish) that returns a
canned acknowledgement. ADR-0846's vision (a `BtwAdvisor` subsystem +
Hub events + LoopEngineer strategy steering for long-running orchestration
tasks) was never built either.

The web console's `/btw` now works for real, through a completely
different and much narrower mechanism: `POST /v1/console/chat/sessions/
{sid}/btw` (`core/console/corvin_console/routes/chat.py::send_btw_note`)
writes straight into the live per-turn `claude` subprocess's stdin
(`chat_runtime.inject_btw_web`), mirroring the bridge adapter's
`inject_btw` raw-stdin fallback (`corvin_operator/bridges/shared/
adapter.py`, ADR-0069 M4/M6, ADR-0648). See ADR-2220 for the design and
why this module was left in place (its own tests still reference it;
deleting it is a separate, unrelated cleanup).
"""

from fastapi import APIRouter, HTTPException, Depends
from typing import Optional

router = APIRouter(prefix="/v1/console", tags=["btw"])


class BtwRequest:
    def __init__(self, chat_id: str, instruction: str):
        self.chat_id = chat_id
        self.instruction = instruction


@router.post("/btw")
async def handle_btw(request: BtwRequest) -> dict:
    """
    Handle /btw midstream steering command.
    
    Example:
        POST /v1/console/btw
        {"chat_id": "chat_xyz", "instruction": "use Opus instead"}
    
    Returns:
        {"status": "guidance_queued", "type": "model_preference", "confidence": 0.85}
    """
    if not request.instruction or len(request.instruction.strip()) == 0:
        raise HTTPException(status_code=400, detail="Instruction required")
    
    # TODO: Capability gate (dual-gate per ADR-0251)
    # if not gate.check_capability(actor, "task_steering"):
    #     raise HTTPException(status_code=403, detail="Insufficient capability")
    
    # TODO: Audit trail (per ADR-0232)
    # audit_backend.write_event({
    #     "event_type": "btw_instruction",
    #     "instruction": request.instruction,
    #     "chat_id": request.chat_id,
    #     ...
    # })
    
    # TODO: Hub.publish_event("guidance_received", ...)
    
    return {
        "status": "guidance_queued",
        "instruction": request.instruction,
        "chat_id": request.chat_id
    }


@router.get("/btw/history/{chat_id}")
async def get_guidance_history(chat_id: str) -> dict:
    """Get guidance history for a task."""
    # TODO: Query BtwAdvisor for chat_id
    return {"chat_id": chat_id, "guidance_count": 0}
