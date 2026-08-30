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
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

voice_router = APIRouter(prefix="/v1/voice", tags=["voice"])


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

        # Simulate STT (Phase 2: will integrate with OpenAI Whisper or local Ollama)
        # For now: mock STT that echoes back with high confidence
        audio_buffer = b""
        chunk_count = 0

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

                # Simulate partial transcription every 3 chunks (500ms at ~60ms/chunk)
                if chunk_count % 3 == 0:
                    # Mock transcription (Phase 2: will call real STT)
                    mock_text = f"[partial transcription chunk {chunk_count}]"
                    await session.send_partial_transcript(mock_text, confidence=0.85)

                # Check for end-of-utterance heuristic (silence or buffer size)
                if len(audio_buffer) > 32000:  # ~1 second at 16kHz
                    # Mock final transcription
                    mock_final_text = "[complete transcription from " + str(chunk_count) + " chunks]"
                    await session.send_final_transcript(mock_final_text, confidence=0.92)

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
                            "text": mock_final_text,
                            "confidence": 0.92,
                            "is_final": True
                        })
                        logger.info(f"Published user_said event to Hub: {mock_final_text}")
                    except Exception as e:
                        logger.error(f"Failed to publish user_said event: {e}")

                    logger.info(f"Transcribed: {mock_final_text}")

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
