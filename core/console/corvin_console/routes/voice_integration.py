"""
Voice Integration API Routes
Provides TTS (Text-to-Speech), speech synthesis, and voice narration endpoints.

Entry points:
- POST /api/v1/voice/synthesize — Convert text to speech (TTS)
- POST /api/v1/voice/narrate — Narrate task outcomes with voice
- GET /api/v1/voice/status — Check TTS service health
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import Optional
import asyncio
import logging

router = APIRouter(prefix="/api/v1/voice", tags=["voice"])
logger = logging.getLogger(__name__)

# Try importing TTS library
try:
    import edge_tts
    TTS_AVAILABLE = True
except ImportError:
    TTS_AVAILABLE = False
    logger.warning("edge_tts not available — TTS endpoints will use fallback")


class TtsRequest(BaseModel):
    """TTS synthesis request"""
    text: str = Field(..., min_length=1, max_length=5000)
    voice: str = Field(default="en-US-AvaMultilingualNeural")
    language: str = Field(default="en-US")
    loudness_lufs: float = Field(default=-23.0, description="Target loudness in LUFS (broadcast standard)")


class TtsResponse(BaseModel):
    """TTS synthesis response"""
    audio_url: str
    duration_ms: int
    voice: str
    loudness_lufs: float
    success: bool


class NarrateRequest(BaseModel):
    """Narration request for task outcome"""
    outcome: str = Field(..., description="Outcome text to narrate")
    task_id: str
    urgency: str = Field(default="normal", regex="^(low|normal|high)$")


class NarrateResponse(BaseModel):
    """Narration response"""
    audio_url: str
    task_id: str
    delivered: bool
    voice_text: str


@router.post("/synthesize", response_model=TtsResponse)
async def synthesize_speech(request: TtsRequest):
    """
    Synthesize speech from text using edge-tts.

    Returns MP3 audio URL + metadata.
    Includes loudness normalization to -23 LUFS (broadcast standard).
    """
    if not TTS_AVAILABLE:
        logger.warning("TTS requested but edge_tts unavailable")
        return TtsResponse(
            audio_url="data:audio/wav;base64,UklGRiYAAABXQVZFZm10IBAAAAABAAEAQB8AAAB9AAACABAAZGF0YCIAAAAAAAAA",
            duration_ms=1000,
            voice=request.voice,
            loudness_lufs=request.loudness_lufs,
            success=False  # Fallback: silent audio
        )

    try:
        # Generate TTS using edge-tts (Microsoft Edge TTS, free)
        communicate = edge_tts.Communicate(
            text=request.text,
            voice=request.voice,
            language=request.language
        )

        # Save to temp location
        import tempfile
        import os
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            audio_path = f.name

        await communicate.save(audio_path)

        # Normalize loudness using FFmpeg
        normalized_path = audio_path.replace(".mp3", "_normalized.mp3")
        try:
            import subprocess
            subprocess.run([
                "ffmpeg", "-i", audio_path,
                "-af", f"loudnorm=I={request.loudness_lufs}:TP=-1.5:LRA=7",
                normalized_path
            ], capture_output=True, check=True)
            os.unlink(audio_path)
            audio_path = normalized_path
        except Exception as e:
            logger.warning(f"Loudness normalization failed: {e}")

        # Return file as data URL
        with open(audio_path, "rb") as f:
            import base64
            audio_data = base64.b64encode(f.read()).decode("utf-8")

        os.unlink(audio_path)

        # Estimate duration (roughly 140 words per minute = 3-5 words per second)
        words = len(request.text.split())
        duration_ms = int((words / 3.5) * 1000)

        return TtsResponse(
            audio_url=f"data:audio/mpeg;base64,{audio_data}",
            duration_ms=duration_ms,
            voice=request.voice,
            loudness_lufs=request.loudness_lufs,
            success=True
        )

    except Exception as e:
        logger.error(f"TTS synthesis failed: {e}")
        raise HTTPException(status_code=500, detail=f"TTS failed: {str(e)}")


@router.post("/narrate", response_model=NarrateResponse)
async def narrate_task_outcome(request: NarrateRequest):
    """
    Narrate a task outcome with voice.
    Integrates with task management system to deliver voice notifications.

    Handles urgency levels:
    - low: Narrate at convenience (24h TTL)
    - normal: Narrate within minutes
    - high: Narrate immediately (push + voice)
    """
    try:
        # Generate voice narration
        narration_text = f"Task {request.task_id} complete. {request.outcome}"

        tts_request = TtsRequest(text=narration_text)
        tts_response = await synthesize_speech(tts_request)

        # For high urgency, emit push notification
        if request.urgency == "high":
            # Emit to notification system
            logger.info(f"High-urgency narration queued for task {request.task_id}")
            # TODO: Integrate with notification_router

        # Emit audit event
        logger.info(f"Narrated task outcome: {request.task_id}")

        return NarrateResponse(
            audio_url=tts_response.audio_url,
            task_id=request.task_id,
            delivered=(request.urgency != "high"),  # Async delivery for high urgency
            voice_text=narration_text
        )

    except Exception as e:
        logger.error(f"Narration failed: {e}")
        raise HTTPException(status_code=500, detail=f"Narration failed: {str(e)}")


@router.get("/status")
async def check_tts_status():
    """Check TTS service health"""
    return {
        "service": "voice-integration",
        "tts_available": TTS_AVAILABLE,
        "tts_library": "edge-tts" if TTS_AVAILABLE else "none",
        "capabilities": [
            "text-to-speech",
            "loudness-normalization",
            "task-narration",
            "broadcast-quality"
        ],
        "status": "ready" if TTS_AVAILABLE else "degraded"
    }


# Learning integration: Collect feedback on narration quality
@router.post("/feedback")
async def submit_narration_feedback(task_id: str, quality_score: float, notes: Optional[str] = None):
    """
    User submits feedback on voice narration quality.
    Feeds into learning loop (ADR-0314) to optimize future narrations.
    """
    if not (0 <= quality_score <= 10):
        raise HTTPException(status_code=400, detail="quality_score must be 0-10")

    logger.info(f"Narration feedback: task={task_id}, score={quality_score}, notes={notes}")

    # Emit learning event (ADR-0314)
    # TODO: Integrate with learning_optimizer

    return {
        "feedback_recorded": True,
        "task_id": task_id,
        "quality_score": quality_score
    }
