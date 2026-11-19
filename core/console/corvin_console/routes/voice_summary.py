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

import asyncio
import time

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from datetime import datetime
from typing import Annotated, Optional, Dict, Any, List, Tuple
import logging

from corvin_console import audit as console_audit
from corvin_console import auth as session_auth
from corvin_console.deps import require_session_csrf_on_mutation

from corvin_console.services.type_detector import (
    detect_message_type,
    get_summary_strategy_for_type,
    MessageType,
)
from corvin_console.services.stt_engine import is_stt_available
from corvin_console.services.voice_feedback_loop import (
    get_feedback_store,
    get_collision_detector,
    SummaryFeedback,
    FeedbackType,
)

logger = logging.getLogger(__name__)

# Every route needs a live console session, every mutation the CSRF token.
# The tenant is the session's — a body-supplied ``tenant_id`` is ignored (it
# used to scope nothing, and any unauthenticated caller could read or overwrite
# any session's transcript by guessing its sid).
router = APIRouter(
    prefix="/chat", tags=["voice-summary"],
    dependencies=[Depends(require_session_csrf_on_mutation)],
)
Session = Annotated[session_auth.SessionRecord, Depends(require_session_csrf_on_mutation)]

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


#: Bounds on what one voice session may hold in memory (R3-NEW-30).
_MAX_TRANSCRIPT_CHARS = 200_000
_MAX_MESSAGES = 500


class VoiceStopRequest(BaseModel):
    tenant_id: str = "_default"
    transcript: str = Field(default="", max_length=_MAX_TRANSCRIPT_CHARS)
    # Phase 2a: message stream for type detection
    messages: Optional[List[Dict[str, str]]] = Field(default=None, max_length=_MAX_MESSAGES)


class VoiceHealthResponse(BaseModel):
    status: str  # "healthy" | "degraded"
    #: A real speech-to-text provider reports ready (never a mock).
    stt_available: bool
    #: Whether summaries are generated. False on this build: no summariser
    #: is wired, ``/voice/stop`` returns ``summary: null``.
    summary_available: bool = False
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

_voice_sessions: Dict[Tuple[str, str], Dict[str, Any]] = {}

#: The store is process memory and used to grow by one entry per
#: (tenant, sid) forever. It now holds at most ``_MAX_VOICE_SESSIONS`` entries
#: and none older than ``_VOICE_SESSION_TTL_S``; the oldest are dropped first.
_MAX_VOICE_SESSIONS = 500
_VOICE_SESSION_TTL_S = 24 * 3600.0


def _store_session(key: Tuple[str, str], session: Dict[str, Any]) -> None:
    """Insert ``session`` and enforce the store's age and size bounds."""
    now = time.time()
    session["_created_ts"] = now
    _voice_sessions.pop(key, None)  # re-insert at the end (newest)
    for k in [k for k, v in _voice_sessions.items()
              if now - float(v.get("_created_ts", now)) > _VOICE_SESSION_TTL_S]:
        _voice_sessions.pop(k, None)
    while len(_voice_sessions) >= _MAX_VOICE_SESSIONS:
        _voice_sessions.pop(next(iter(_voice_sessions)), None)
    _voice_sessions[key] = session


def _key(rec: session_auth.SessionRecord, sid: str) -> Tuple[str, str]:
    """Voice state is keyed by (tenant, sid): a sid alone crossed tenants."""
    return (rec.tenant_id, sid)


def _scoped(rec: session_auth.SessionRecord, sid: str) -> str:
    """Tenant-qualified id for the shared feedback / collision stores."""
    return f"{rec.tenant_id}:{sid}"


def _get_stt_available() -> bool:
    """Whether a REAL speech-to-text provider is ready, from the platform's
    ``stt.provider_status()`` (cached ~30 s). BLOCKING on a cache miss -
    handlers call it through ``asyncio.to_thread``. It used to ask a mock
    engine that always said yes."""
    return is_stt_available()


def _record_audit_event(event_type: str, rec: session_auth.SessionRecord, sid: str) -> None:
    """Record the action in the tenant's audit chain — metadata only.

    This used to be a ``logger.info("AUDIT: ...")`` line carrying the first
    100 transcript characters and the free-text feedback comment: user content
    in a log file, and no record in the chain at all.
    """
    console_audit.action_performed(
        tenant_id=rec.tenant_id, sid_fingerprint=rec.sid_fingerprint,
        action=f"voice.{event_type}", target_kind="voice_session", target_id=sid[:128],
    )


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────


@router.post("/{sid}/voice/start", response_model=Dict[str, Any])
async def start_voice_recording(sid: str, req: VoiceStartRequest, rec: Session):
    """
    Start Voice Recording for a chat session.

    Opt-In: User explicitly clicks "Start Recording" button.
    Graceful Fallback: Returns error if STT unavailable, but chat continues.
    """
    if not await asyncio.to_thread(_get_stt_available):
        logger.warning("STT unavailable for a voice session - graceful fallback")
        raise HTTPException(
            status_code=503,
            detail={
                "status": "stt_unavailable",
                "message": "Speech-to-Text is currently unavailable. Chat works without voice summary.",
                "session_id": sid,
                "stt_available": False,
            },
        )

    _store_session(_key(rec, sid), {
        "status": "recording",
        "started_at": datetime.utcnow().isoformat(),
        "transcript": "",
        "summary": None,
        "events": [],
    })

    await asyncio.to_thread(_record_audit_event, "recording_started", rec, sid)

    return {
        "status": "recording_started",
        "session_id": sid,
        "timestamp": datetime.utcnow().isoformat(),
        "stt_available": True,
    }


@router.post("/{sid}/voice/stop", response_model=Dict[str, Any])
async def stop_voice_recording(sid: str, req: VoiceStopRequest, rec: Session):
    """
    Stop Voice Recording and trigger Type-Aware Summary Generation (Phase 2a).

    Phase 2a: Detects content type (code/image/video/text) and selects strategy
    Phase 2b: Persists to database
    Phase 2c: Learns from user feedback
    """
    if _key(rec, sid) not in _voice_sessions:
        raise HTTPException(status_code=404, detail="No recording session")

    session = _voice_sessions[_key(rec, sid)]
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

    # No summariser is wired yet. The route used to return a fabricated
    # "summary" (the strategy prompt glued to the first 100 transcript
    # characters) as if it were generated; the honest answer is None.
    summary = None
    session["summary"] = summary
    session["summary_strategy"] = dominant_strategy
    session["content_types"] = content_types

    await asyncio.to_thread(_record_audit_event, "recording_stopped", rec, sid)

    return {
        "status": "recording_stopped",
        "session_id": sid,
        "transcript": req.transcript,
        "summary": summary,
        "summary_strategy": dominant_strategy,
        "content_types": content_types,
    }


@router.get("/{sid}/voice/summary", response_model=VoiceSummaryResponse)
async def get_voice_summary(sid: str, rec: Session):
    """
    Retrieve Voice Summary for a session (Phase 2a: with type-aware data).
    """
    if _key(rec, sid) not in _voice_sessions:
        return VoiceSummaryResponse(
            session_id=sid,
            status="not_started",
        )

    session = _voice_sessions.get(_key(rec, sid), {})

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
    Health check for the Voice Summary subsystem.

    ``stt_available`` is the platform's real provider status. ``status`` is
    "healthy" only when a provider is ready; the message never claims more
    than exists (summaries are not generated on this build).
    """
    stt_available = await asyncio.to_thread(_get_stt_available)
    return VoiceHealthResponse(
        status="healthy" if stt_available else "degraded",
        stt_available=stt_available,
        summary_available=False,
        timestamp=datetime.utcnow().isoformat(),
        message=(
            "Speech-to-text is ready. Summaries are not generated on this build."
            if stt_available else
            "Speech-to-text is not available on this install. Chat works without it."
        ),
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
async def record_summary_feedback(sid: str, req: FeedbackRequest, rec: Session):
    """
    Record user feedback on summary quality (Phase 2c Learning Loop).
    Used to improve summary strategies via learning.
    """
    if _key(rec, sid) not in _voice_sessions:
        raise HTTPException(status_code=404, detail="Session not found")

    feedback = SummaryFeedback(
        session_id=_scoped(rec, sid),
        feedback_type=FeedbackType.QUALITY_RATING,
        score=req.score,
        comment=req.comment,
    )

    feedback_store = get_feedback_store()
    feedback_store.record_feedback(feedback)

    # Update session with feedback
    _voice_sessions[_key(rec, sid)]["feedback_score"] = req.score
    _voice_sessions[_key(rec, sid)]["feedback_provided_at"] = datetime.utcnow().isoformat()

    await asyncio.to_thread(_record_audit_event, "feedback_provided", rec, sid)

    return {
        "status": "feedback_recorded",
        "session_id": sid,
        "score": req.score,
    }


@router.post("/{sid}/task/register")
async def register_task(sid: str, req: TaskRegistrationRequest, rec: Session):
    """
    Register a task to detect collisions (Phase 2c).
    Prevents user and agent from working on same task.
    """
    collision_detector = get_collision_detector()

    if req.initiated_by == "user":
        collision_detector.register_user_task(_scoped(rec, sid), req.task_id)
        return {
            "status": "task_registered",
            "session_id": sid,
            "task_id": req.task_id,
            "initiated_by": "user",
        }

    elif req.initiated_by == "agent":
        success = collision_detector.register_agent_task(_scoped(rec, sid), req.task_id)
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
async def get_task_status(sid: str, rec: Session):
    """
    Get task status for a session (Phase 2c).
    Returns user tasks vs agent tasks to detect conflicts.
    """
    collision_detector = get_collision_detector()
    key = _scoped(rec, sid)

    return {
        "session_id": sid,
        "user_tasks": list(collision_detector.get_user_tasks(key)),
        "agent_tasks": list(collision_detector.get_agent_tasks(key)),
        "collision_risk": len(
            collision_detector.get_user_tasks(key) & collision_detector.get_agent_tasks(key)
        ) > 0,
    }
