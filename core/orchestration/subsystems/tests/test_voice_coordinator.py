"""Tests for VoiceCoordinator subsystem (Proposal 2, k=2).

- Unit tests: Confidence levels, channel isolation
- Async tests: Event handling, TTS queueing
- Interrupt tests: Stop playback, cleanup
"""

import pytest
from datetime import datetime

from ..voice_coordinator import (
    VoiceCoordinator, VoiceChannel, PartialTranscript, ConfidenceLevel
)


class TestPartialTranscript:
    """Test confidence classification."""

    def test_confidence_high(self):
        """Confidence >= 0.85 is HIGH."""
        transcript = PartialTranscript(text="hello world", confidence=0.95)
        assert transcript.confidence_level() == ConfidenceLevel.HIGH
        assert not transcript.should_clarify()

    def test_confidence_medium(self):
        """0.7 <= confidence < 0.85 is MEDIUM."""
        transcript = PartialTranscript(text="hello", confidence=0.77)
        assert transcript.confidence_level() == ConfidenceLevel.MEDIUM
        assert not transcript.should_clarify()

    def test_confidence_low(self):
        """Confidence < 0.7 is LOW; triggers clarification."""
        transcript = PartialTranscript(text="h*llo", confidence=0.65)
        assert transcript.confidence_level() == ConfidenceLevel.LOW
        assert transcript.should_clarify()

    def test_confidence_boundary_high(self):
        """Boundary: 0.85 exactly is HIGH."""
        transcript = PartialTranscript(text="test", confidence=0.85)
        assert transcript.confidence_level() == ConfidenceLevel.HIGH

    def test_confidence_boundary_medium(self):
        """Boundary: 0.7 exactly is MEDIUM."""
        transcript = PartialTranscript(text="test", confidence=0.70)
        assert transcript.confidence_level() == ConfidenceLevel.MEDIUM

    def test_to_dict(self):
        """Serialize to dict."""
        transcript = PartialTranscript(
            text="hello",
            confidence=0.88,
            is_final=True
        )
        d = transcript.to_dict()
        assert d["text"] == "hello"
        assert d["confidence"] == 0.88
        assert d["is_final"] is True
        assert d["confidence_level"] == "high"


class TestVoiceChannel:
    """Test voice channel state management."""

    def test_channel_creation(self):
        """Create a voice channel."""
        channel = VoiceChannel(
            channel_id="ch_001",
            task_id="task_001",
            actor="user1"
        )
        assert channel.channel_id == "ch_001"
        assert channel.task_id == "task_001"
        assert not channel.interrupted
        assert channel.stt_partial is None

    def test_mark_interrupted(self):
        """Mark channel as interrupted."""
        channel = VoiceChannel(
            channel_id="ch_001",
            task_id="task_001",
            actor="user1"
        )
        channel.tts_playing = True

        channel.mark_interrupted("user_request")

        assert channel.interrupted
        assert channel.reason_for_interrupt == "user_request"
        assert not channel.tts_playing  # TTS stops on interrupt


@pytest.mark.asyncio
class TestVoiceCoordinatorAsync:
    """Test async voice coordination."""

    async def test_on_event_user_said_high_confidence(self):
        """High confidence voice input → proceed."""
        coordinator = VoiceCoordinator()

        event_data = {
            "channel_id": "ch_001",
            "task_id": "task_001",
            "actor": "user1",
            "text": "use Opus for this",
            "confidence": 0.92,
            "is_final": True,
        }

        # Call directly (avoid Hub integration for unit test)
        await coordinator._handle_user_said(event_data)

        # Verify channel created
        assert "ch_001" in coordinator.active_channels
        channel = coordinator.active_channels["ch_001"]
        assert channel.stt_partial.confidence == 0.92
        assert not channel.stt_partial.should_clarify()

    async def test_on_event_user_said_low_confidence(self):
        """Low confidence voice input → request clarification."""
        coordinator = VoiceCoordinator()

        event_data = {
            "channel_id": "ch_002",
            "task_id": "task_002",
            "actor": "user2",
            "text": "u*e Op*s",
            "confidence": 0.65,  # LOW
            "is_final": True,
        }

        await coordinator._handle_user_said(event_data)

        channel = coordinator.active_channels["ch_002"]
        assert channel.stt_partial.should_clarify()

    async def test_on_event_interrupt(self):
        """Interrupt stops playback."""
        coordinator = VoiceCoordinator()

        # Create channel first
        await coordinator._handle_user_said({
            "channel_id": "ch_003",
            "task_id": "task_003",
            "actor": "user3",
            "text": "start task",
            "confidence": 0.90,
            "is_final": True,
        })

        # Mark TTS playing
        coordinator.active_channels["ch_003"].tts_playing = True

        # Send interrupt
        await coordinator._handle_interrupt({
            "channel_id": "ch_003",
            "reason": "user_request",
        })

        channel = coordinator.active_channels["ch_003"]
        assert channel.interrupted
        assert not channel.tts_playing

    async def test_get_voice_confidence_with_voice_input(self):
        """Voice confidence should return True if user input was voice."""
        coordinator = VoiceCoordinator()

        # Setup voice input
        await coordinator._handle_user_said({
            "channel_id": "ch_004",
            "task_id": "task_004",
            "actor": "user4",
            "text": "explain this",
            "confidence": 0.88,
            "is_final": True,
        })

        # Query confidence
        result = await coordinator.get_voice_confidence("task_004")
        assert result["should_speak"] is True
        assert result["urgency"] == "high"

    async def test_get_voice_confidence_no_voice_input(self):
        """Voice confidence should return False if no voice input."""
        coordinator = VoiceCoordinator()

        result = await coordinator.get_voice_confidence("unknown_task")
        assert result["should_speak"] is False
        assert result["urgency"] == "low"

    async def test_multi_channel_isolation(self):
        """Different channels are isolated (tenant scoping)."""
        coordinator = VoiceCoordinator()

        # Two concurrent voice channels
        await coordinator._handle_user_said({
            "channel_id": "ch_a",
            "task_id": "task_a",
            "text": "channel A",
            "confidence": 0.90,
            "is_final": True,
        })

        await coordinator._handle_user_said({
            "channel_id": "ch_b",
            "task_id": "task_b",
            "text": "channel B",
            "confidence": 0.85,
            "is_final": True,
        })

        # Interrupt one channel
        await coordinator._handle_interrupt({
            "channel_id": "ch_a",
            "reason": "user_request",
        })

        # Verify isolation: only ch_a is interrupted
        assert coordinator.active_channels["ch_a"].interrupted
        assert not coordinator.active_channels["ch_b"].interrupted

    async def test_get_channel_status(self):
        """Query detailed channel status."""
        coordinator = VoiceCoordinator()

        await coordinator._handle_user_said({
            "channel_id": "ch_status",
            "task_id": "task_status",
            "text": "test status",
            "confidence": 0.92,
            "is_final": False,
        })

        status = await coordinator.get_channel_status("ch_status")
        assert status["channel_id"] == "ch_status"
        assert status["task_id"] == "task_status"
        assert status["stt_partial"]["text"] == "test status"
        assert status["stt_partial"]["confidence"] == 0.92
        assert status["interrupted"] is False

    async def test_check_for_interrupt(self):
        """Poll for interrupt status."""
        coordinator = VoiceCoordinator()

        await coordinator._handle_user_said({
            "channel_id": "ch_interrupt_test",
            "task_id": "task_x",
            "text": "speaking",
            "confidence": 0.90,
            "is_final": True,
        })

        # No interrupt initially
        result = await coordinator.check_for_interrupt("ch_interrupt_test")
        assert result["interrupted"] is False

        # Send interrupt
        await coordinator._handle_interrupt({
            "channel_id": "ch_interrupt_test",
            "reason": "user_stop",
        })

        # Now interrupt is detected
        result = await coordinator.check_for_interrupt("ch_interrupt_test")
        assert result["interrupted"] is True
        assert result["reason"] == "user_stop"

    async def test_cleanup_channel(self):
        """Clean up channel on task completion."""
        coordinator = VoiceCoordinator()

        await coordinator._handle_user_said({
            "channel_id": "ch_cleanup",
            "task_id": "task_cleanup",
            "text": "test",
            "confidence": 0.90,
            "is_final": True,
        })

        assert "ch_cleanup" in coordinator.active_channels

        # Cleanup
        await coordinator.cleanup_channel("ch_cleanup")

        assert "ch_cleanup" not in coordinator.active_channels


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
