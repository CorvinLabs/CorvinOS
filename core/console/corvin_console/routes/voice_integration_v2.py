"""
Voice Integration API v2 — Quality-First TTS with Fallback

Endpoints:
- POST /api/v1/voice/synthesize — Convert text to speech (OpenAI primary, Edge fallback)
- POST /api/v1/voice/narrate — Narrate task outcomes with voice
- GET /api/v1/voice/status — Check TTS service health + provider status
- GET /api/v1/voice/test — Test both TTS providers
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, Literal
import logging
import tempfile
import os

# Import TTS manager (Quality-First with fallback)
from core.voice.tts_providers import get_tts_manager, TTSProvider

router = APIRouter(prefix="/api/v1/voice", tags=["voice"])
logger = logging.getLogger(__name__)


class TtsRequest(BaseModel):
    """TTS synthesis request"""
    text: str = Field(..., min_length=1, max_length=5000)
    voice: str = Field(default="en-US-AvaMultilingualNeural")
    language: str = Field(default="en-US")
    loudness_lufs: float = Field(default=-23.0, description="Target loudness in LUFS")
    force_provider: Optional[Literal["openai", "edge_tts"]] = Field(
        default=None,
        description="Force use of specific provider (debugging)"
    )


class TtsResponse(BaseModel):
    """TTS synthesis response"""
    success: bool
    audio_url: Optional[str] = None
    duration_ms: int = 0
    provider: str = "unknown"
    quality_score: float = 0.0
    message: str = ""


class NarrateRequest(BaseModel):
    """Narration request for task outcome"""
    outcome: str = Field(..., description="Outcome text to narrate")
    task_id: str
    urgency: str = Field(default="normal", regex="^(low|normal|high)$")


class NarrateResponse(BaseModel):
    """Narration response"""
    success: bool
    audio_url: Optional[str] = None
    task_id: str
    delivered: bool = False
    voice_text: str = ""


@router.post("/synthesize", response_model=TtsResponse)
async def synthesize_speech(request: TtsRequest) -> TtsResponse:
    """
    Synthesize speech from text.

    Quality-First Strategy:
    1. Try OpenAI TTS (high quality, paid) if available
    2. Fallback to Edge TTS (good quality, free) if OpenAI fails
    3. Both providers guarantee success (availability first)

    Returns MP3 audio as data URL + metadata.
    """
    try:
        tts_manager = get_tts_manager()

        # Map force_provider string to enum
        force_provider = None
        if request.force_provider == "openai":
            force_provider = TTSProvider.OPENAI
        elif request.force_provider == "edge_tts":
            force_provider = TTSProvider.EDGE_TTS

        # Synthesize with quality-first fallback
        result = await tts_manager.synthesize(
            text=request.text,
            voice=request.voice,
            language=request.language,
            force_provider=force_provider
        )

        if not result or not result.success:
            logger.error("TTS synthesis failed (all providers exhausted)")
            return TtsResponse(
                success=False,
                message="TTS synthesis failed (no providers available)",
                provider="none"
            )

        # Convert audio bytes to data URL
        import base64
        audio_b64 = base64.b64encode(result.audio_bytes).decode()
        audio_url = f"data:audio/mpeg;base64,{audio_b64}"

        # Normalize loudness with FFmpeg if needed
        try:
            if request.loudness_lufs != -23.0:
                import subprocess
                import tempfile

                with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                    f.write(result.audio_bytes)
                    temp_path = f.name

                normalized_path = temp_path.replace(".mp3", "_normalized.mp3")
                subprocess.run([
                    "ffmpeg", "-i", temp_path,
                    "-af", f"loudnorm=I={request.loudness_lufs}:TP=-1.5:LRA=7",
                    normalized_path
                ], capture_output=True, check=True, timeout=10)

                with open(normalized_path, "rb") as f:
                    result.audio_bytes = f.read()

                os.unlink(temp_path)
                os.unlink(normalized_path)

                audio_b64 = base64.b64encode(result.audio_bytes).decode()
                audio_url = f"data:audio/mpeg;base64,{audio_b64}"
        except Exception as e:
            logger.warning(f"Loudness normalization failed: {e}; continuing without")

        return TtsResponse(
            success=True,
            audio_url=audio_url,
            duration_ms=result.duration_ms,
            provider=result.provider,
            quality_score=result.quality_score,
            message=f"✅ Synthesized with {result.provider} ({result.quality_score:.0%} quality)"
        )

    except Exception as e:
        logger.error(f"TTS synthesis exception: {e}")
        return TtsResponse(
            success=False,
            message=f"Exception: {str(e)}",
            provider="error"
        )


@router.post("/narrate", response_model=NarrateResponse)
async def narrate_task_outcome(request: NarrateRequest) -> NarrateResponse:
    """
    Narrate a task outcome with voice.
    Integrates with task management system to deliver voice notifications.
    """
    try:
        # Generate voice narration
        narration_text = f"Task {request.task_id} complete. {request.outcome}"

        tts_request = TtsRequest(text=narration_text)
        tts_response = await synthesize_speech(tts_request)

        if not tts_response.success:
            logger.error(f"Narration failed for task {request.task_id}")
            return NarrateResponse(
                success=False,
                task_id=request.task_id,
                voice_text=narration_text
            )

        return NarrateResponse(
            success=True,
            audio_url=tts_response.audio_url,
            task_id=request.task_id,
            delivered=(request.urgency != "high"),  # Async delivery for high urgency
            voice_text=narration_text
        )

    except Exception as e:
        logger.error(f"Narration exception: {e}")
        return NarrateResponse(
            success=False,
            task_id=request.task_id,
            voice_text=request.outcome
        )


@router.get("/status")
async def check_tts_status():
    """
    Check TTS service health and provider availability.

    Returns:
    - Primary provider (OpenAI) availability + quality
    - Fallback provider (Edge TTS) availability + quality
    - Last provider used
    """
    tts_manager = get_tts_manager()
    status = tts_manager.get_status()

    return {
        "service": "voice-integration-v2",
        "status": "ready" if (status["primary_available"] or status["fallback_available"]) else "degraded",
        "strategy": "quality-first with fallback",
        "primary_provider": {
            "name": "openai",
            "available": status["primary_available"],
            "quality_score": status["primary_quality"],
            "cost": "paid"
        },
        "fallback_provider": {
            "name": "edge_tts",
            "available": status["fallback_available"],
            "quality_score": status["fallback_quality"],
            "cost": "free"
        },
        "last_provider_used": status["last_provider_used"],
        "capabilities": [
            "text-to-speech",
            "quality-first-fallback",
            "loudness-normalization",
            "task-narration",
            "broadcast-quality"
        ]
    }


@router.post("/test")
async def test_tts_providers():
    """
    Test both TTS providers with a sample phrase.
    Useful for debugging provider configuration.

    Returns results from both OpenAI and Edge TTS attempts.
    """
    test_text = "CorvinOS is working with Quality-First voice synthesis."
    results = {}

    tts_manager = get_tts_manager()

    # Test OpenAI
    logger.info("Testing OpenAI TTS provider...")
    openai_result = await tts_manager.synthesize(
        test_text,
        force_provider=TTSProvider.OPENAI
    )
    results["openai"] = {
        "success": bool(openai_result and openai_result.success),
        "quality_score": openai_result.quality_score if openai_result else 0.0,
        "provider": openai_result.provider if openai_result else "n/a"
    }

    # Test Edge TTS
    logger.info("Testing Edge TTS provider...")
    edge_result = await tts_manager.synthesize(
        test_text,
        force_provider=TTSProvider.EDGE_TTS
    )
    results["edge_tts"] = {
        "success": bool(edge_result and edge_result.success),
        "quality_score": edge_result.quality_score if edge_result else 0.0,
        "provider": edge_result.provider if edge_result else "n/a"
    }

    # Test automatic fallback (no force_provider)
    logger.info("Testing automatic Quality-First fallback...")
    auto_result = await tts_manager.synthesize(test_text)
    results["automatic_fallback"] = {
        "success": bool(auto_result and auto_result.success),
        "quality_score": auto_result.quality_score if auto_result else 0.0,
        "provider_used": auto_result.provider if auto_result else "n/a"
    }

    return {
        "test_text": test_text,
        "results": results,
        "summary": f"✅ {sum(1 for r in results.values() if r['success'])}/3 providers working"
    }
