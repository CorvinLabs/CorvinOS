"""
Voice Summary API Routes (Phase 1 + Phase 2a)
Separate APIRouter for Voice Recording + Summary Generation + Type-Aware Strategies

Architecture (ADR-0596):
- Phase 1: Simple Summary + Graceful Fallback
- Phase 2a: Type-Aware Detection (Code/Image/Video strategies)
- Phase 2b: Persistent Storage (SQLite/Postgres)
- Phase 2c: Learning Loop + Task Collision Detection

@date 2026-09-25
@phase Phase 2a: Type-Aware Summary Detection
"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from datetime import datetime
from typing import Optional, Dict, Any, List
import logging

from core.console.corvin_console.services.type_detector import (
    detect_message_type,
    get_summary_strategy_for_type,
    MessageType,
)
from core.console.corvin_console.services.stt_engine import (
    get_stt_engine,
    is_stt_available,
)
from core.console.corvin_console.services.voice_feedback_loop import (
    get_feedback_store,
    get_collision_detector,
    SummaryFeedback,
    FeedbackType,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["voice-summary"])

# ─────────────────────────────────────────────────────────────────────────────
# Models
# ─────────────────────────────────────────────────────────────────────────────


class VoiceStartRequest(BaseModel):
    tenant_id: str = "_default"


class MessageContentType(BaseModel):
    """Phase 2a: Type-aware content detection"""
    detected_type: str  # "text" | "code" | "image" | "video" | "mixed"
    confidence: float
    metadata: Dict[str, Any] = {}


class VoiceStopRequest(BaseModel):
    tenant_id: str = "_default"
    transcript: str = ""
    messages: Optional[List[Dict[str, str]]] = None  # Phase 2a: message stream for type detection


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
    summary_strategy: Optional[str] = None  # Phase 2a: strategy used
    content_types: Optional[List[MessageContentType]] = None  # Phase 2a: detected types
    started_at: Optional[str] = None
    stopped_at: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
# In-Memory Session Storage (Phase 1: Mock, Phase 2: Persistent)
# ─────────────────────────────────────────────────────────────────────────────

_voice_sessions: Dict[str, Dict[str, Any]] = {}


def _get_stt_available() -> bool:
    """
    Check if STT (Speech-To-Text) is available (Phase 2b: via STT Engine).
    Graceful Fallback: Chat works even if STT is unavailable.
    """
    return is_stt_available()


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
    Stop Voice Recording and trigger Type-Aware Summary Generation (Phase 2a).

    Phase 2a: Detects content type (code/image/video/text) and selects strategy
    Phase 2b: Persists to database
    Phase 2c: Learns from user feedback
    """
    if sid not in _voice_sessions:
        raise HTTPException(status_code=404, detail=f"No recording session for {sid}")

    session = _voice_sessions[sid]
    session["status"] = "stopped"
    session["transcript"] = req.transcript
    session["stopped_at"] = datetime.utcnow().isoformat()

    # ──────────────────────────────────────────────────────────
    # Phase 2a: Type-Aware Detection
    # ──────────────────────────────────────────────────────────

    content_types = []
    dominant_type = MessageType.TEXT
    dominant_strategy = "simple"

    if req.messages:
        # Detect type for each message
        for msg in req.messages:
            text = msg.get("text", "")
            if text:
                detection = detect_message_type(text)
                content_types.append({
                    "detected_type": detection.detected_type.value,
                    "confidence": detection.confidence,
                    "metadata": detection.metadata,
                })

        # Find dominant type (Phase 2a: simple majority voting)
        if content_types:
            type_counts = {}
            for ct in content_types:
                t = ct["detected_type"]
                type_counts[t] = type_counts.get(t, 0) + 1

            dominant_type_str = max(type_counts, key=type_counts.get)
            dominant_type = MessageType(dominant_type_str)
            dominant_strategy = get_summary_strategy_for_type(dominant_type)
    else:
        # Fallback: detect from transcript
        if req.transcript:
            detection = detect_message_type(req.transcript)
            dominant_type = detection.detected_type
            dominant_strategy = get_summary_strategy_for_type(dominant_type)
            content_types.append({
                "detected_type": dominant_type.value,
                "confidence": detection.confidence,
                "metadata": detection.metadata,
            })

    # ──────────────────────────────────────────────────────────
    # Phase 2: Summary Generation (strategy-dependent)
    # ──────────────────────────────────────────────────────────

    # Phase 2a: Simple strategy selection
    # Phase 2b: Implement strategy-specific LLM prompts
    summary_prompt = {
        "simple": "Summarize this conversation concisely.",
        "syntax_aware": "Summarize this code discussion, highlighting function signatures and key logic.",
        "visual_aware": "Describe the key visual elements and composition discussed.",
        "temporal_aware": "Create a timeline summary of the key moments and transitions.",
        "entity_aware": "Extract and relate key entities and concepts discussed.",
    }.get(dominant_strategy, "Summarize this conversation.")

    # Phase 2: TODO - Call real LLM (currently mock)
    summary = f"[{dominant_strategy}] {summary_prompt}: {req.transcript[:100]}..." if req.transcript else "No transcript"
    session["summary"] = summary
    session["summary_strategy"] = dominant_strategy
    session["content_types"] = content_types

    _record_audit_event(
        "recording_stopped",
        {
            "session_id": sid,
            "transcript_length": len(req.transcript),
            "dominant_type": dominant_type.value,
            "summary_strategy": dominant_strategy,
            "summary": summary,
        },
        req.tenant_id,
    )

    return {
        "status": "recording_stopped",
        "session_id": sid,
        "transcript": req.transcript,
        "summary": summary,
        "summary_strategy": dominant_strategy,
        "content_types": content_types,
    }


@router.get("/{sid}/voice/summary", response_model=VoiceSummaryResponse)
async def get_voice_summary(sid: str):
    """
    Retrieve Voice Summary for a session (Phase 2a: with type-aware data).
    """
    if sid not in _voice_sessions:
        return VoiceSummaryResponse(
            session_id=sid,
            status="not_started",
        )

    session = _voice_sessions.get(sid, {})

    # Convert content_types to response format
    content_types = None
    if session.get("content_types"):
        content_types = [
            MessageContentType(**ct) for ct in session["content_types"]
        ]

    return VoiceSummaryResponse(
        session_id=sid,
        status=session.get("status", "unknown"),
        transcript=session.get("transcript", ""),
        summary=session.get("summary"),
        summary_strategy=session.get("summary_strategy"),
        content_types=content_types,
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


# ────────────────────────────────────────────────────────────────────
# Phase 2c: Learning Loop + Task Collision Detection
# ────────────────────────────────────────────────────────────────────


class FeedbackRequest(BaseModel):
    """User feedback on summary quality (Phase 2c)"""
    tenant_id: str = "_default"
    score: float  # 0-5 rating
    comment: Optional[str] = None


class TaskRegistrationRequest(BaseModel):
    """Register a task (user or agent initiated) (Phase 2c)"""
    task_id: str
    initiated_by: str  # "user" or "agent"


@router.post("/{sid}/voice/feedback")
async def record_summary_feedback(sid: str, req: FeedbackRequest):
    """
    Record user feedback on summary quality (Phase 2c Learning Loop).
    Used to improve summary strategies via learning.
    """
    if sid not in _voice_sessions:
        raise HTTPException(status_code=404, detail=f"Session {sid} not found")

    feedback = SummaryFeedback(
        session_id=sid,
        feedback_type=FeedbackType.QUALITY_RATING,
        score=req.score,
        comment=req.comment,
    )

    feedback_store = get_feedback_store()
    feedback_store.record_feedback(feedback)

    # Update session with feedback
    _voice_sessions[sid]["feedback_score"] = req.score
    _voice_sessions[sid]["feedback_provided_at"] = datetime.utcnow().isoformat()

    _record_audit_event(
        "feedback_provided",
        {
            "session_id": sid,
            "score": req.score,
            "comment": req.comment,
        },
        req.tenant_id,
    )

    return {
        "status": "feedback_recorded",
        "session_id": sid,
        "score": req.score,
    }


@router.post("/{sid}/task/register")
async def register_task(sid: str, req: TaskRegistrationRequest):
    """
    Register a task to detect collisions (Phase 2c).
    Prevents user and agent from working on same task.
    """
    collision_detector = get_collision_detector()

    if req.initiated_by == "user":
        collision_detector.register_user_task(sid, req.task_id)
        return {
            "status": "task_registered",
            "session_id": sid,
            "task_id": req.task_id,
            "initiated_by": "user",
        }

    elif req.initiated_by == "agent":
        success = collision_detector.register_agent_task(sid, req.task_id)
        if not success:
            raise HTTPException(
                status_code=409,
                detail=f"Task collision detected: user already working on {req.task_id}",
            )
        return {
            "status": "task_registered",
            "session_id": sid,
            "task_id": req.task_id,
            "initiated_by": "agent",
        }

    raise HTTPException(status_code=400, detail="initiated_by must be 'user' or 'agent'")


@router.get("/{sid}/task/status")
async def get_task_status(sid: str):
    """
    Get task status for a session (Phase 2c).
    Returns user tasks vs agent tasks to detect conflicts.
    """
    collision_detector = get_collision_detector()

    return {
        "session_id": sid,
        "user_tasks": list(collision_detector.get_user_tasks(sid)),
        "agent_tasks": list(collision_detector.get_agent_tasks(sid)),
        "collision_risk": len(
            collision_detector.get_user_tasks(sid) & collision_detector.get_agent_tasks(sid)
        ) > 0,
    }
