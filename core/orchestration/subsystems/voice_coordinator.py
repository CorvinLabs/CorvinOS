"""VoiceCoordinator Subsystem — Streaming Voice I/O (Proposal 2, Week 2).

Handles live transcription confidence, bidirectional voice, and interrupts.
Integrates with STT/TTS engines (OpenAI Whisper, piper TTS).
Non-blocking coordination between voice channel and Brain subsystems.

Architecture (Proposal 2):
1. StreamingTranscription: User speaks → partial transcription with confidence
2. StreamingPlayback: Brain responds → voice audio chunks streamed back
3. InterruptHandler: User says "stop" or presses button → cancels playback
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, List, Any
from enum import Enum
from .base import Subsystem

logger = logging.getLogger(__name__)


class ConfidenceLevel(str, Enum):
    """Confidence thresholds for voice input."""
    LOW = "low"          # < 0.7
    MEDIUM = "medium"    # 0.7-0.85
    HIGH = "high"        # >= 0.85


@dataclass
class PartialTranscript:
    """Partial transcription from streaming STT."""
    text: str
    confidence: float  # 0.0-1.0
    is_final: bool = False
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def confidence_level(self) -> ConfidenceLevel:
        """Classify confidence as LOW/MEDIUM/HIGH."""
        if self.confidence < 0.7:
            return ConfidenceLevel.LOW
        elif self.confidence < 0.85:
            return ConfidenceLevel.MEDIUM
        else:
            return ConfidenceLevel.HIGH

    def should_clarify(self) -> bool:
        """True if confidence is too low to proceed (< 0.7)."""
        return self.confidence < 0.7

    def to_dict(self):
        return {
            "text": self.text,
            "confidence": self.confidence,
            "is_final": self.is_final,
            "confidence_level": self.confidence_level().value,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class VoiceChannel:
    """Active voice streaming session."""
    channel_id: str
    task_id: str
    actor: str
    started_at: datetime = field(default_factory=datetime.utcnow)
    stt_partial: Optional[PartialTranscript] = None
    tts_playing: bool = False
    interrupted: bool = False
    reason_for_interrupt: Optional[str] = None

    def mark_interrupted(self, reason: str = "user_request"):
        """Mark channel as interrupted (user said 'stop' or clicked stop button)."""
        self.interrupted = True
        self.reason_for_interrupt = reason
        self.tts_playing = False


class VoiceCoordinator(Subsystem):
    """Coordinate voice I/O for Brain decisions (Proposal 2, Week 2).

    Lifecycle:
    1. User speaks → STT streams partial transcription
    2. VoiceCoordinator.on_event("user_said") with confidence
    3. If confidence >= 0.7: proceed; else: ask for clarification
    4. Brain generates response
    5. VoiceCoordinator.handle_request("start_tts") → stream audio
    6. If interrupt: VoiceCoordinator.on_event("interrupt_received") → stop playback

    Integration with Brain:
    - Brain checks: "Should I explain this vocally?" (via voice_confidence_for_turn)
    - Brain emits: "response_ready" with text
    - VoiceCoordinator: streams TTS + watches for interrupts
    """

    @property
    def name(self) -> str:
        return "voice_coordinator"

    @property
    def version(self) -> str:
        return "1.0.0"

    def __init__(self):
        self.active_channels: Dict[str, VoiceChannel] = {}  # channel_id → VoiceChannel
        self.tts_queue: List[Dict[str, Any]] = []  # Queued TTS requests
        self._lock = asyncio.Lock()  # Thread-safe channel operations
        self.clarification_threshold = 0.7  # Require at least 70% confidence
        self.hub = None  # Set by startup()

    def startup(self, hub: "SubsystemHub") -> None:  # noqa: F821
        """Initialize VoiceCoordinator and subscribe to voice events."""
        self.hub = hub
        self.hub.subscribe("user_said", self.on_event)
        self.hub.subscribe("interrupt_received", self.on_event)
        self.hub.subscribe("response_ready", self.on_event)
        logger.info(f"{self.name} v{self.version} started")

    def shutdown(self) -> None:
        """Cleanup resources."""
        self.active_channels.clear()
        self.tts_queue.clear()
        logger.info(f"{self.name} shut down")

    async def on_event(self, event_name: str, event_data: Dict[str, Any]):
        """Handle incoming events from Hub."""
        if event_name == "user_said":
            # User finished speaking; STT produced transcript
            await self._handle_user_said(event_data)

        elif event_name == "interrupt_received":
            # User interrupted playback (said "stop" or clicked button)
            await self._handle_interrupt(event_data)

        elif event_name == "response_ready":
            # Brain generated response; ready to speak
            await self._queue_tts(event_data)

    async def handle_request(self, request_type: str, **kwargs) -> Optional[Dict]:
        """Handle requests from other subsystems."""
        if request_type == "voice_confidence_for_turn":
            # Brain asks: "Should I explain this vocally?"
            task_id = kwargs.get("task_id", "")
            return await self.get_voice_confidence(task_id)

        elif request_type == "get_channel_status":
            # Query status of a voice channel
            channel_id = kwargs.get("channel_id", "")
            return await self.get_channel_status(channel_id)

        elif request_type == "check_for_interrupt":
            # Brain polls: "Did user interrupt me?"
            channel_id = kwargs.get("channel_id", "")
            return await self.check_for_interrupt(channel_id)

        return None

    async def _handle_user_said(self, event_data: Dict[str, Any]):
        """Process incoming user voice (partial transcription)."""
        channel_id = event_data.get("channel_id", "")
        text = event_data.get("text", "")
        confidence = event_data.get("confidence", 0.0)
        is_final = event_data.get("is_final", False)
        task_id = event_data.get("task_id", "")

        async with self._lock:
            # Create channel if doesn't exist
            if channel_id not in self.active_channels:
                self.active_channels[channel_id] = VoiceChannel(
                    channel_id=channel_id,
                    task_id=task_id,
                    actor=event_data.get("actor", "unknown")
                )

            channel = self.active_channels[channel_id]
            channel.stt_partial = PartialTranscript(
                text=text,
                confidence=confidence,
                is_final=is_final
            )

            # Emit event for Brain to observe
            if is_final:
                if channel.stt_partial.should_clarify():
                    logger.warning(
                        f"Low confidence ({confidence:.0%}) for voice input: '{text}'; "
                        f"asking for clarification"
                    )
                    # Request clarification (Brain will ask user to repeat)
                    self.publish_event("request_clarification", {
                        "channel_id": channel_id,
                        "reason": "low_confidence",
                        "confidence": confidence,
                        "partial_text": text,
                    })
                else:
                    # High confidence; proceed
                    logger.info(
                        f"Voice input confirmed ({confidence:.0%}): '{text}'"
                    )
                    self.publish_event("user_input_confirmed", {
                        "channel_id": channel_id,
                        "text": text,
                        "confidence": confidence,
                        "task_id": task_id,
                    })

    async def _handle_interrupt(self, event_data: Dict[str, Any]):
        """Process interrupt (user said 'stop' or clicked stop button)."""
        channel_id = event_data.get("channel_id", "")
        reason = event_data.get("reason", "user_request")

        async with self._lock:
            if channel_id in self.active_channels:
                channel = self.active_channels[channel_id]
                channel.mark_interrupted(reason)

                logger.warning(f"Voice channel {channel_id} interrupted: {reason}")

                # Publish interrupt event
                self.publish_event("task_interrupted", {
                    "channel_id": channel_id,
                    "reason": reason,
                    "task_id": channel.task_id,
                })

    async def _queue_tts(self, event_data: Dict[str, Any]):
        """Queue TTS response for streaming."""
        channel_id = event_data.get("channel_id", "")
        text = event_data.get("text", "")
        task_id = event_data.get("task_id", "")

        async with self._lock:
            if channel_id not in self.active_channels:
                logger.warning(f"TTS requested for unknown channel {channel_id}")
                return

            self.tts_queue.append({
                "channel_id": channel_id,
                "text": text,
                "task_id": task_id,
                "queued_at": datetime.utcnow().isoformat(),
            })

            logger.info(f"Queued TTS for channel {channel_id}: {text[:50]}...")

    async def get_voice_confidence(self, task_id: str) -> Optional[Dict]:
        """Get voice confidence level for a task.

        Returns: {"should_speak": True/False, "urgency": "high"/"medium"/"low"}
        """
        # Heuristic: speak if last input was voice (not text)
        # TODO: Integrate with OperatorFingerprint (ADR-0348) for user preference
        async with self._lock:
            for channel in self.active_channels.values():
                if channel.task_id == task_id and channel.stt_partial:
                    return {
                        "should_speak": True,
                        "urgency": "high",
                        "reason": "user_input_was_voice",
                    }

        return {
            "should_speak": False,
            "urgency": "low",
            "reason": "no_voice_input_detected",
        }

    async def get_channel_status(self, channel_id: str) -> Optional[Dict]:
        """Get detailed status of a voice channel."""
        async with self._lock:
            if channel_id not in self.active_channels:
                return None

            channel = self.active_channels[channel_id]
            return {
                "channel_id": channel_id,
                "task_id": channel.task_id,
                "actor": channel.actor,
                "started_at": channel.started_at.isoformat(),
                "stt_partial": channel.stt_partial.to_dict() if channel.stt_partial else None,
                "tts_playing": channel.tts_playing,
                "interrupted": channel.interrupted,
                "reason_for_interrupt": channel.reason_for_interrupt,
            }

    async def check_for_interrupt(self, channel_id: str) -> Optional[Dict]:
        """Check if user interrupted playback (for Brain polling)."""
        async with self._lock:
            if channel_id not in self.active_channels:
                return None

            channel = self.active_channels[channel_id]
            if channel.interrupted:
                return {
                    "interrupted": True,
                    "reason": channel.reason_for_interrupt,
                }

        return {"interrupted": False}

    def publish_event(self, event_name: str, event_data: Dict[str, Any]):
        """Publish event to Hub (Brain listens)."""
        if self.hub:
            try:
                self.hub.publish_event(event_name, event_data)
            except Exception as e:
                logger.error(f"Error publishing {event_name} event: {e}")
        else:
            logger.warning(f"Hub not initialized; event {event_name} not published")

    async def cleanup_channel(self, channel_id: str):
        """Clean up voice channel (e.g., on task completion)."""
        async with self._lock:
            if channel_id in self.active_channels:
                del self.active_channels[channel_id]
                logger.info(f"Cleaned up voice channel {channel_id}")
