"""FastAPI WebSocket routes for voice streaming (Proposal 2, Week 2).

WebSocket /v1/voice/stream — Real-time STT (speech-to-text) + TTS (text-to-speech)

Protocol (Phase 1 — STT only):
1. Client connects: WebSocket /v1/voice/stream?task_id=task_123&channel_id=ch_abc
2. Client sends binary audio chunks (PCM 16kHz, 16-bit)
3. Server responds with JSON events:
   - {"type": "partial_transcript", "text": "...", "confidence": 0.95}
   - {"type": "final_transcript", "text": "...", "confidence": 0.98}
   - {"type": "error", "message": "..."}

Protocol (Phase 2 — bidirectional, deferred):
- Server sends: {"type": "response_audio", "data": "<base64-audio>"}
- Client can interrupt: {"type": "interrupt", "reason": "user_request"}

Constraints (Proposal 2 design, ADR-0352):
- Latency: < 100ms per STT chunk
- TTS latency: < 500ms from response ready to first audio chunk
- Confidence thresholds: LOW < 0.7, MEDIUM 0.7-0.85, HIGH >= 0.85
- Interrupt: Immediate stop playback (no buffering)
"""

import asyncio
import logging
import json
import base64
import os
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# Try to import OpenAI client for real STT/TTS (2b-1, 2b-2)
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False
    logger.warning("OpenAI SDK not available; using mock STT/TTS (2b-1/2b-2 MVP)")

voice_router = APIRouter(prefix="/v1/voice", tags=["voice"])


async def generate_speech_real(text: str, voice: str = "nova") -> Optional[bytes]:
    """Real TTS via OpenAI TTS API (2b-2 implementation, k=4).

    Args:
        text: Text to convert to speech
        voice: Voice name (nova, alloy, echo, fable, onyx, shimmer; default: nova)

    Returns:
        Audio bytes (MP3) or None if failed
    """
    if not OPENAI_AVAILABLE:
        logger.warning("OpenAI SDK not available; TTS skipped (2b-2 MVP)")
        return None

    try:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            logger.warning("OPENAI_API_KEY not set; TTS skipped")
            return None

        client = OpenAI(api_key=api_key)

        # Call OpenAI TTS API
        response = client.audio.speech.create(
            model="tts-1",  # Fast TTS (vs tts-1-hd for higher quality)
            voice=voice,
            input=text
        )

        # Extract audio bytes
        audio_bytes = response.content
        logger.info(f"Real TTS generated {len(audio_bytes)} bytes for: {text[:50]}...")

        return audio_bytes

    except Exception as e:
        logger.error(f"Real TTS failed: {e}")
        return None


async def transcribe_audio_chunk_real(audio_bytes: bytes, language: str = "en") -> Dict[str, Any]:
    """Real STT via OpenAI Whisper API (2b-1 implementation).

    Args:
        audio_bytes: PCM audio data (16kHz, 16-bit)
        language: Language code (default: "en")

    Returns:
        {"text": "...", "confidence": 0.95}
    """
    if not OPENAI_AVAILABLE:
        # Fallback to mock STT
        return {
            "text": "[mock STT - OpenAI SDK not installed]",
            "confidence": 0.5
        }

    try:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            logger.warning("OPENAI_API_KEY not set; using mock STT")
            return {"text": "[mock STT - API key not configured]", "confidence": 0.5}

        client = OpenAI(api_key=api_key)

        # Save audio to temp file (Whisper API requires file-like object)
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(audio_bytes)
            temp_path = f.name

        try:
            # Call OpenAI Whisper API
            with open(temp_path, "rb") as audio_file:
                transcript = client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                    language=language
                )

            # Extract text and compute confidence
            # Note: Whisper doesn't return confidence; use high confidence for now
            text = transcript.text
            confidence = 0.95  # TODO: compute from word_timings if available

            logger.info(f"Real STT transcribed: {text} (confidence={confidence:.2f})")

            return {"text": text, "confidence": confidence}

        finally:
            # Clean up temp file
            import os as os_module
            try:
                os_module.unlink(temp_path)
            except Exception as e:
                logger.debug(f"Failed to clean up temp file: {e}")

    except Exception as e:
        logger.error(f"Real STT failed: {e}; falling back to mock")
        return {
            "text": f"[STT error: {str(e)[:50]}]",
            "confidence": 0.3
        }


class VoiceStreamSession:
    """Manages a single WebSocket voice streaming session."""

    def __init__(self, websocket: WebSocket, task_id: str, channel_id: str, actor: str = "unknown"):
        self.websocket = websocket
        self.task_id = task_id
        self.channel_id = channel_id
        self.actor = actor
        self.is_connected = False
        self.stt_buffer = b""  # Accumulate audio chunks
        self.last_transcript = ""
        self._lock = asyncio.Lock()

    async def connect(self) -> bool:
        """Accept WebSocket connection."""
        try:
            await self.websocket.accept()
            self.is_connected = True
            logger.info(f"Voice channel {self.channel_id} connected (task={self.task_id})")
            return True
        except Exception as e:
            logger.error(f"Failed to accept voice connection: {e}")
            return False

    async def send_partial_transcript(self, text: str, confidence: float):
        """Send partial transcription to client."""
        try:
            await self.websocket.send_json({
                "type": "partial_transcript",
                "text": text,
                "confidence": confidence,
                "channel_id": self.channel_id,
            })
        except Exception as e:
            logger.error(f"Failed to send partial transcript: {e}")

    async def send_final_transcript(self, text: str, confidence: float):
        """Send final transcription (end of utterance)."""
        try:
            await self.websocket.send_json({
                "type": "final_transcript",
                "text": text,
                "confidence": confidence,
                "channel_id": self.channel_id,
            })
            self.last_transcript = text
        except Exception as e:
            logger.error(f"Failed to send final transcript: {e}")

    async def send_error(self, message: str, code: str = "unknown_error"):
        """Send error message to client."""
        try:
            await self.websocket.send_json({
                "type": "error",
                "message": message,
                "code": code,
                "channel_id": self.channel_id,
            })
        except Exception as e:
            logger.error(f"Failed to send error: {e}")

    async def send_response_audio(self, audio_data: bytes):
        """Send TTS audio chunk to client (Phase 2, deferred)."""
        try:
            # Encode audio as base64 for JSON transport
            audio_b64 = base64.b64encode(audio_data).decode("utf-8")
            await self.websocket.send_json({
                "type": "response_audio",
                "data": audio_b64,
                "channel_id": self.channel_id,
            })
        except Exception as e:
            logger.error(f"Failed to send response audio: {e}")

    async def receive_audio_chunk(self) -> Optional[bytes]:
        """Receive audio chunk from client."""
        try:
            data = await self.websocket.receive_bytes()
            return data
        except WebSocketDisconnect:
            self.is_connected = False
            logger.info(f"Voice channel {self.channel_id} disconnected")
            return None
        except Exception as e:
            logger.error(f"Error receiving audio chunk: {e}")
            return None

    async def receive_control_message(self) -> Optional[Dict[str, Any]]:
        """Receive control message (e.g., interrupt) from client."""
        try:
            msg = await self.websocket.receive_json(mode="text")
            return msg
        except WebSocketDisconnect:
            self.is_connected = False
            return None
        except Exception as e:
            logger.debug(f"No control message (audio chunk expected): {type(e).__name__}")
            return None

    async def close(self):
        """Close WebSocket connection."""
        try:
            if self.is_connected:
                await self.websocket.close(code=status.WS_1000_NORMAL_CLOSURE)
                self.is_connected = False
                logger.info(f"Voice channel {self.channel_id} closed")
        except Exception as e:
            logger.error(f"Error closing voice connection: {e}")


@voice_router.websocket("/stream")
async def voice_stream_websocket(websocket: WebSocket, task_id: str, channel_id: str):
    """WebSocket endpoint for real-time voice streaming.

    Query params:
    - task_id: associated CorvinOS task
    - channel_id: unique voice channel identifier
    - actor: (optional) user identifier

    Protocol:
    - Client sends binary audio frames (PCM)
    - Server responds with JSON events (partial/final transcripts, errors)
    - Client can send control messages (interrupt, clarification)
    """

    # Get actor from query params or headers
    actor = websocket.query_params.get("actor", "unknown")

    # Create session
    session = VoiceStreamSession(websocket, task_id, channel_id, actor)

    # Accept connection
    if not await session.connect():
        return

    try:
        # Phase 1: Streaming STT (Speech-To-Text)
        # Listen for audio chunks and transcribe in real-time

        stt_enabled = True  # TODO: Load from feature flags
        if not stt_enabled:
            await session.send_error("STT is disabled", code="feature_disabled")
            await session.close()
            return

        logger.info(f"Voice session started: task={task_id}, channel={channel_id}, actor={actor}")

        # Real STT via OpenAI Whisper (2b-1 implementation, k=3)
        # Falls back to mock STT if OpenAI SDK or API key not available
        audio_buffer = b""
        chunk_count = 0
        language = websocket.query_params.get("language", "en")

        while session.is_connected:
            # Receive audio chunk (or control message)
            try:
                # Try to receive with timeout (to allow for interrupt checks)
                data = await asyncio.wait_for(
                    session.receive_audio_chunk(),
                    timeout=5.0
                )

                if data is None:
                    break

                audio_buffer += data
                chunk_count += 1

                # Send partial transcription every 3 chunks
                # (In production, would use VAD or stream chunks to Whisper API)
                if chunk_count % 3 == 0:
                    partial_text = f"[listening... {len(audio_buffer)} bytes]"
                    await session.send_partial_transcript(partial_text, confidence=0.70)

                # Check for end-of-utterance heuristic (silence or buffer size)
                # ~1 second at 16kHz = 32000 bytes
                if len(audio_buffer) > 32000:
                    # Call real STT API (2b-1, k=3)
                    stt_result = await asyncio.to_thread(
                        transcribe_audio_chunk_real,
                        audio_buffer,
                        language
                    )

                    final_text = stt_result.get("text", "[STT failed]")
                    confidence = stt_result.get("confidence", 0.5)

                    await session.send_final_transcript(final_text, confidence=confidence)

                    # Reset for next utterance
                    audio_buffer = b""
                    chunk_count = 0

                    # Emit event to Hub (Brain will receive via VoiceCoordinator)
                    # (2b-3 implementation, k=1: Voice Hub wiring)
                    try:
                        from core.orchestration.hub import SubsystemHub
                        hub = SubsystemHub()
                        hub.publish_event("user_said", {
                            "channel_id": channel_id,
                            "task_id": task_id,
                            "actor": actor,
                            "text": final_text,
                            "confidence": confidence,
                            "is_final": True
                        })
                        logger.info(f"Published user_said event to Hub: {final_text}")
                    except Exception as e:
                        logger.error(f"Failed to publish user_said event: {e}")

                    logger.info(f"Transcribed: {final_text}")

            except asyncio.TimeoutError:
                # Timeout waiting for audio; check for control messages
                control_msg = await session.receive_control_message()
                if control_msg:
                    if control_msg.get("type") == "interrupt":
                        logger.warning(f"Interrupt received on channel {channel_id}")

                        # Emit interrupt event to Hub (2b-3 implementation, k=1)
                        try:
                            from core.orchestration.hub import SubsystemHub
                            hub = SubsystemHub()
                            hub.publish_event("interrupt_received", {
                                "channel_id": channel_id,
                                "task_id": task_id,
                                "actor": actor,
                                "reason": control_msg.get("reason", "user_request")
                            })
                            logger.info(f"Published interrupt_received event to Hub")
                        except Exception as e:
                            logger.error(f"Failed to publish interrupt_received event: {e}")

                        await session.send_json({
                            "type": "interrupt_ack",
                            "message": "Playback stopped",
                            "channel_id": channel_id,
                        })
                        break
                continue

            except Exception as e:
                logger.error(f"Error in voice stream loop: {e}")
                await session.send_error(f"Stream error: {str(e)}", code="stream_error")
                break

    except Exception as e:
        logger.error(f"Unexpected error in voice stream handler: {e}", exc_info=True)
    finally:
        await session.close()


@voice_router.post("/status")
async def get_voice_status(task_id: str) -> Dict[str, Any]:
    """Check active voice channels for a task (read-only)."""
    # TODO: Query VoiceCoordinator for active channels
    # Placeholder: return empty
    return {
        "task_id": task_id,
        "active_channels": [],
        "status": "no_active_channels",
    }
