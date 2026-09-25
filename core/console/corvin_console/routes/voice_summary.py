"""
Voice Summary API Routes (Phase 1)
Separate APIRouter for Voice Recording + Summary Generation
Implements Graceful Degradation: STT unavailable → Chat works

Architecture (ADR-0596):
- Type-aware Summary (Phase 2)
- Opt-In Recording with Transcript + Auto-Summary
- Audit-logged (Events only, not raw audio)
- Tenant-scoped

@date 2026-09-25
@phase Phase 1: Simple Summary + Graceful Fallback
"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from datetime import datetime
from typing import Optional, Dict, Any
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["voice-summary"])

# ─────────────────────────────────────────────────────────────────────────────
# Models
# ─────────────────────────────────────────────────────────────────────────────


class VoiceStartRequest(BaseModel):
    tenant_id: str = "_default"


class VoiceStopRequest(BaseModel):
    tenant_id: str = "_default"
    transcript: str = ""


class VoiceHealthResponse(BaseModel):
    status: str  # "healthy" | "degraded"
    stt_available: bool
    timestamp: str
    message: str


class VoiceSummaryResponse(BaseModel):
    session_id: str
    status: str  # "not_started" | "recording" | "stopped"
    transcript: str = ""
    summary: Optional[str] = None
    started_at: Optional[str] = None
    stopped_at: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
# In-Memory Session Storage (Phase 1: Mock, Phase 2: Persistent)
# ─────────────────────────────────────────────────────────────────────────────

_voice_sessions: Dict[str, Dict[str, Any]] = {}


def _get_stt_available() -> bool:
    """
    Check if STT (Speech-To-Text) is available.
    Phase 1: Mock availability. Later: Check actual provider (Google Cloud STT, etc.)
    Graceful Fallback: Chat works even if STT is unavailable.
    """
    return True


def _record_audit_event(event_type: str, data: Dict[str, Any], tenant_id: str) -> None:
    """
    Log audit event (GDPR Art. 30/32 compliance).
    Audit logs: Events ONLY, never raw audio.
    """
    event = {
        "event_type": f"voice.{event_type}",
        "tenant_id": tenant_id,
        "timestamp": datetime.utcnow().isoformat(),
        **data,
    }
    logger.info(f"AUDIT: {event}")


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────


@router.post("/{sid}/voice/start", response_model=Dict[str, Any])
async def start_voice_recording(sid: str, req: VoiceStartRequest):
    """
    Start Voice Recording for a chat session.

    Opt-In: User explicitly clicks "Start Recording" button.
    Graceful Fallback: Returns error if STT unavailable, but chat continues.
    """
    if not _get_stt_available():
        logger.warning(f"STT unavailable for session {sid} - Graceful Fallback")
        raise HTTPException(
            status_code=503,
            detail={
                "status": "stt_unavailable",
                "message": "Speech-to-Text is currently unavailable. Chat works without voice summary.",
                "session_id": sid,
                "stt_available": False,
            },
        )

    _voice_sessions[sid] = {
        "status": "recording",
        "started_at": datetime.utcnow().isoformat(),
        "transcript": "",
        "summary": None,
        "events": [],
    }

    _record_audit_event(
        "recording_started",
        {"session_id": sid, "stt_available": True},
        req.tenant_id,
    )

    return {
        "status": "recording_started",
        "session_id": sid,
        "timestamp": datetime.utcnow().isoformat(),
        "stt_available": True,
    }


@router.post("/{sid}/voice/stop", response_model=Dict[str, Any])
async def stop_voice_recording(sid: str, req: VoiceStopRequest):
    """
    Stop Voice Recording and trigger Summary Generation.
    """
    if sid not in _voice_sessions:
        raise HTTPException(status_code=404, detail=f"No recording session for {sid}")

    session = _voice_sessions[sid]
    session["status"] = "stopped"
    session["transcript"] = req.transcript
    session["stopped_at"] = datetime.utcnow().isoformat()

    # Phase 2: Call LLM for type-aware summary
    summary = f"Session summary: {req.transcript[:100]}..." if req.transcript else "No transcript"
    session["summary"] = summary

    _record_audit_event(
        "recording_stopped",
        {
            "session_id": sid,
            "transcript_length": len(req.transcript),
            "summary": summary,
        },
        req.tenant_id,
    )

    return {
        "status": "recording_stopped",
        "session_id": sid,
        "transcript": req.transcript,
        "summary": summary,
    }


@router.get("/{sid}/voice/summary", response_model=VoiceSummaryResponse)
async def get_voice_summary(sid: str):
    """
    Retrieve Voice Summary for a session.
    """
    if sid not in _voice_sessions:
        return VoiceSummaryResponse(
            session_id=sid,
            status="not_started",
        )

    session = _voice_sessions.get(sid, {})
    return VoiceSummaryResponse(
        session_id=sid,
        status=session.get("status", "unknown"),
        transcript=session.get("transcript", ""),
        summary=session.get("summary"),
        started_at=session.get("started_at"),
        stopped_at=session.get("stopped_at"),
    )


@router.get("/voice/health", response_model=VoiceHealthResponse)
async def voice_summary_health():
    """
    Health check for Voice Summary subsystem.
    Returns STT availability (for Graceful Degradation).
    """
    stt_available = _get_stt_available()
    return VoiceHealthResponse(
        status="healthy" if stt_available else "degraded",
        stt_available=stt_available,
        timestamp=datetime.utcnow().isoformat(),
        message="Voice Summary available" if stt_available else "STT unavailable (Graceful Fallback active)",
    )
