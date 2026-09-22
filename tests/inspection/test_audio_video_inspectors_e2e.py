"""
E2E Tests for Audio/Video Inspectors and Maestro Orchestrator (Phase 3, Stream 4).

Tests:
1. Audio Inspector (analysis + audit logging)
2. Video Inspector (analysis + learning events)
3. Maestro (parallel orchestration + aggregation)
4. Audit chain integrity (hash-chained events)
5. Learning loop feedback (confidence scoring)

References:
  - core.inspection.audio_inspector
  - core.inspection.video_inspector
  - core.inspection.maestro
  - core.audit.chain
  - core.learning.event_emitter
"""

import pytest
import asyncio
from datetime import datetime
from unittest.mock import MagicMock, patch
from uuid import uuid4

from core.inspection.audio_inspector import AudioInspector, AudioQualityLevel
from core.inspection.video_inspector import VideoInspector, FrameQuality, FeedbackSignal
from core.inspection.maestro import Maestro, MediaInput, PipelineStage
from core.audit.chain import AuditChain, AuditEvent
from core.learning.event_emitter import EventEmitter, LearningEventType


@pytest.fixture
def mock_audit_chain():
    """Mock audit chain."""
    chain = MagicMock(spec=AuditChain)
    chain.write = MagicMock(return_value="audit_id_123")
    return chain


@pytest.fixture
def mock_event_emitter():
    """Mock event emitter."""
    emitter = MagicMock(spec=EventEmitter)
    emitter.emit = MagicMock()
    return emitter


@pytest.fixture
def audio_inspector(mock_audit_chain, mock_event_emitter):
    """Create AudioInspector with mocks."""
    return AudioInspector(mock_audit_chain, mock_event_emitter, "_default")


@pytest.fixture
def video_inspector(mock_event_emitter, mock_audit_chain):
    """Create VideoInspector with mocks."""
    return VideoInspector(mock_event_emitter, mock_audit_chain, "_default")


@pytest.fixture
def maestro(audio_inspector, video_inspector, mock_audit_chain, mock_event_emitter):
    """Create Maestro orchestrator with mocks."""
    return Maestro(
        audio_inspector,
        video_inspector,
        mock_audit_chain,
        mock_event_emitter,
        "_default",
    )


class TestAudioInspector:
    """Test audio analysis and audit integration."""

    def test_audio_analyze_success(self, audio_inspector, mock_audit_chain):
        """Test successful audio analysis."""
        result = audio_inspector.analyze(
            audio_id="test_audio_1",
            duration_ms=30000,
            transcription="Hello world",
            metadata={"snr": 30.0, "silence_ratio": 0.05},
        )

        assert result is not None
        assert result.audio_id == "test_audio_1"
        assert result.duration_ms == 30000
        assert result.transcription == "Hello world"
        assert result.confidence_score > 0.0
        assert result.quality_level is not None

        # Verify audit chain was called
        mock_audit_chain.write.assert_called_once()
        event = mock_audit_chain.write.call_args[0][0]
        assert event.event_type == "audio_analyzed"
        assert event.subject == "audio:test_audio_1"

    def test_audio_analyze_low_quality(self, audio_inspector):
        """Test audio with poor quality metrics."""
        result = audio_inspector.analyze(
            audio_id="low_quality_audio",
            duration_ms=5000,
            metadata={"snr": 5.0, "silence_ratio": 0.5},
        )

        assert result.quality_level == AudioQualityLevel.POOR
        assert result.confidence_score < 0.5

    def test_audio_analyze_high_quality(self, audio_inspector):
        """Test audio with excellent quality metrics."""
        result = audio_inspector.analyze(
            audio_id="high_quality_audio",
            duration_ms=60000,
            transcription="Crystal clear audio",
            metadata={"snr": 40.0, "silence_ratio": 0.02},
        )

        assert result.quality_level == AudioQualityLevel.EXCELLENT
        assert result.confidence_score >= 0.85

    def test_audio_analyze_invalid_duration(self, audio_inspector):
        """Test audio with invalid duration."""
        with pytest.raises(ValueError):
            audio_inspector.analyze(
                audio_id="invalid",
                duration_ms=-1000,
            )

    def test_audio_analyze_audit_failure(self, audio_inspector, mock_audit_chain):
        """Test that audit failure raises error."""
        mock_audit_chain.write.side_effect = RuntimeError("Chain write failed")

        with pytest.raises(RuntimeError):
            audio_inspector.analyze(
                audio_id="test",
                duration_ms=30000,
            )


class TestVideoInspector:
    """Test video analysis and learning loop integration."""

    def test_video_analyze_success(self, video_inspector, mock_audit_chain):
        """Test successful video analysis."""
        result = video_inspector.analyze(
            video_id="test_video_1",
            duration_ms=60000,
            frame_count=1800,
            fps=30.0,
            resolution="1920x1080",
            metadata={"sharpness_avg": 0.85, "color_saturation": 0.80},
        )

        assert result is not None
        assert result.video_id == "test_video_1"
        assert result.frame_count == 1800
        assert result.fps == 30.0
        assert result.confidence_score > 0.0

        # Verify audit chain
        assert mock_audit_chain.write.call_count >= 1

    def test_video_analyze_scene_detection(self, video_inspector):
        """Test video with multiple scenes."""
        scenes = [
            {"type": "static", "start_ms": 0, "end_ms": 10000},
            {"type": "panning", "start_ms": 10000, "end_ms": 20000},
            {"type": "dynamic", "start_ms": 20000, "end_ms": 30000},
        ]

        result = video_inspector.analyze(
            video_id="multi_scene_video",
            duration_ms=30000,
            frame_count=900,
            fps=30.0,
            resolution="1920x1080",
            metadata={"scenes": scenes, "sharpness_avg": 0.80},
        )

        assert len(result.detected_scenes) == 3
        assert result.confidence_score > 0.0

    def test_video_feedback_processing(self, video_inspector, mock_event_emitter):
        """Test feedback loop integration."""
        feedback = FeedbackSignal(
            video_id="test_video_1",
            feedback_type="quality_rating",
            score=0.95,
            comment="Excellent quality",
            timestamp=datetime.utcnow(),
            user_id="user_123",
            tenant_id="_default",
        )

        video_inspector.process_feedback(feedback)

        # Verify learning events emitted
        assert mock_event_emitter.emit.call_count >= 2
        calls = mock_event_emitter.emit.call_args_list
        assert any(
            call[1]["event_type"] == LearningEventType.user_feedback
            for call in calls
        )


class TestMaestroOrchestrator:
    """Test Maestro parallel orchestration."""

    @pytest.mark.asyncio
    async def test_maestro_process_audio_only(self, maestro, mock_audit_chain):
        """Test Maestro with audio only."""
        media = MediaInput(
            media_id="audio_only_1",
            audio_path="/tmp/test.wav",
            metadata={
                "audio_duration_ms": 30000,
                "audio_metadata": {"snr": 30.0},
            },
        )

        result = await maestro.process_media(media)

        assert result.media_id == "audio_only_1"
        assert result.audio_result is not None
        assert result.video_result is None
        assert result.overall_confidence > 0.0
        assert result.stage == PipelineStage.COMPLETE

    @pytest.mark.asyncio
    async def test_maestro_process_video_only(self, maestro, mock_audit_chain):
        """Test Maestro with video only."""
        media = MediaInput(
            media_id="video_only_1",
            video_path="/tmp/test.mp4",
            metadata={
                "video_duration_ms": 30000,
                "frame_count": 900,
                "fps": 30.0,
                "resolution": "1920x1080",
                "video_metadata": {"sharpness_avg": 0.85},
            },
        )

        result = await maestro.process_media(media)

        assert result.media_id == "video_only_1"
        assert result.audio_result is None
        assert result.video_result is not None
        assert result.overall_confidence > 0.0

    @pytest.mark.asyncio
    async def test_maestro_process_both(self, maestro, mock_audit_chain):
        """Test Maestro with both audio and video."""
        media = MediaInput(
            media_id="audio_video_1",
            audio_path="/tmp/test.wav",
            video_path="/tmp/test.mp4",
            metadata={
                "audio_duration_ms": 30000,
                "audio_metadata": {"snr": 30.0},
                "video_duration_ms": 30000,
                "frame_count": 900,
                "fps": 30.0,
                "resolution": "1920x1080",
                "video_metadata": {"sharpness_avg": 0.85},
            },
        )

        result = await maestro.process_media(media)

        assert result.audio_result is not None
        assert result.video_result is not None
        # Confidence should be weighted average (60% audio, 40% video)
        expected = (
            result.audio_result.confidence_score * 0.6 +
            result.video_result.confidence_score * 0.4
        )
        assert abs(result.overall_confidence - expected) < 0.01

    @pytest.mark.asyncio
    async def test_maestro_input_validation(self, maestro):
        """Test input validation."""
        media = MediaInput(media_id="invalid")  # No audio or video path

        with pytest.raises(ValueError):
            await maestro.process_media(media)

    @pytest.mark.asyncio
    async def test_maestro_audit_logging(self, maestro, mock_audit_chain):
        """Test that all stages are logged to audit chain."""
        media = MediaInput(
            media_id="audit_test_1",
            audio_path="/tmp/test.wav",
            metadata={"audio_duration_ms": 30000},
        )

        result = await maestro.process_media(media)

        # Should have called audit_chain.write multiple times
        assert mock_audit_chain.write.call_count >= 3

        # Verify event types
        calls = [call[0][0] for call in mock_audit_chain.write.call_args_list]
        event_types = [event.event_type for event in calls]
        assert "maestro_stage" in event_types
        assert "maestro_complete" in event_types


class TestAuditChainIntegrity:
    """Test audit chain integrity across inspectors."""

    def test_audio_audit_chain_hash(self, audio_inspector, mock_audit_chain):
        """Verify audio analysis is hash-chained."""
        result = audio_inspector.analyze(
            audio_id="hash_test_audio",
            duration_ms=30000,
        )

        event = mock_audit_chain.write.call_args[0][0]
        assert event.content_hash is not None
        assert len(event.content_hash) == 64  # SHA256 hex

    def test_video_audit_chain_hash(self, video_inspector, mock_audit_chain):
        """Verify video analysis is hash-chained."""
        result = video_inspector.analyze(
            video_id="hash_test_video",
            duration_ms=30000,
            frame_count=900,
            fps=30.0,
            resolution="1920x1080",
        )

        calls = mock_audit_chain.write.call_args_list
        assert len(calls) >= 1
        # Find maestro events (video_inspector logs via audit_chain)
        for call in calls:
            event = call[0][0]
            if event.event_type == "video_analyzed":
                assert event.content_hash is not None
                assert len(event.content_hash) == 64


class TestLearningLoopIntegration:
    """Test learning loop event emission."""

    def test_audio_emits_confidence_event(self, audio_inspector, mock_event_emitter):
        """Verify audio analysis emits confidence scores."""
        audio_inspector.analyze(
            audio_id="learning_audio_1",
            duration_ms=30000,
        )

        calls = mock_event_emitter.emit.call_args_list
        confidence_calls = [
            call for call in calls
            if call[1].get("event_type") == LearningEventType.confidence_score
        ]
        assert len(confidence_calls) >= 1

    def test_video_emits_feedback_event(self, video_inspector, mock_event_emitter):
        """Verify video feedback emits learning events."""
        feedback = FeedbackSignal(
            video_id="test_video_1",
            feedback_type="quality_rating",
            score=0.90,
            comment="Good",
            timestamp=datetime.utcnow(),
            user_id="user_1",
            tenant_id="_default",
        )

        video_inspector.process_feedback(feedback)

        calls = mock_event_emitter.emit.call_args_list
        feedback_calls = [
            call for call in calls
            if call[1].get("event_type") == LearningEventType.user_feedback
        ]
        assert len(feedback_calls) >= 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
