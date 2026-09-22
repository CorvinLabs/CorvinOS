"""
Phase 3 E2E Tests: Audio/Video Inspectors + Maestro (ADR-0232 + ADR-0314).
"""

import pytest
import asyncio
from unittest.mock import MagicMock
from datetime import datetime

from core.inspection.audio_video_inspectors import (
    AudioInspector,
    VideoInspector,
    Maestro,
    AudioQualityLevel,
    FrameQuality,
)


@pytest.fixture
def mock_audit_chain():
    chain = MagicMock()
    chain.write = MagicMock()
    return chain


@pytest.fixture
def mock_event_emitter():
    emitter = MagicMock()
    emitter.emit = MagicMock()
    return emitter


class TestAudioInspector:
    """Test audio analysis with audit logging."""

    def test_analyze_success(self, mock_audit_chain, mock_event_emitter):
        inspector = AudioInspector(mock_audit_chain, mock_event_emitter, "_default")
        result = inspector.analyze("audio_1", 30000, {"snr": 30.0})

        assert result is not None
        assert result.audio_id == "audio_1"
        assert result.confidence_score > 0.0
        mock_audit_chain.write.assert_called_once()
        mock_event_emitter.emit.assert_called_once()

    def test_audit_logging(self, mock_audit_chain, mock_event_emitter):
        inspector = AudioInspector(mock_audit_chain, mock_event_emitter, "_default")
        inspector.analyze("audio_1", 30000)

        call_args = mock_audit_chain.write.call_args[0][0]
        assert call_args.event_type == "audio_analyzed"
        assert call_args.resource == "audio:audio_1"
        assert call_args.tenant_id == "_default"


class TestVideoInspector:
    """Test video analysis with learning events."""

    def test_analyze_success(self, mock_event_emitter, mock_audit_chain):
        inspector = VideoInspector(mock_event_emitter, mock_audit_chain, "_default")
        result = inspector.analyze("video_1", 60000, 1800, 30.0, {"sharpness": 0.85})

        assert result is not None
        assert result.video_id == "video_1"
        assert result.confidence_score > 0.0
        mock_audit_chain.write.assert_called_once()
        assert mock_event_emitter.emit.call_count >= 2

    def test_learning_events(self, mock_event_emitter, mock_audit_chain):
        inspector = VideoInspector(mock_event_emitter, mock_audit_chain, "_default")
        inspector.analyze("video_1", 60000, 1800, 30.0)

        # Should emit both confidence_score and outcome_observed
        calls = mock_event_emitter.emit.call_args_list
        event_types = [call[1]["event_type"] for call in calls]
        assert "confidence_score" in event_types
        assert "outcome_observed" in event_types


class TestMaestroOrchestrator:
    """Test parallel orchestration."""

    @pytest.mark.asyncio
    async def test_process_audio_only(self, mock_audit_chain, mock_event_emitter):
        audio = AudioInspector(mock_audit_chain, mock_event_emitter, "_default")
        video = VideoInspector(mock_event_emitter, mock_audit_chain, "_default")
        maestro = Maestro(audio, video, mock_audit_chain, mock_event_emitter, "_default")

        result = await maestro.process_media(
            "media_1",
            audio_config={
                "audio_id": "audio_1",
                "duration_ms": 30000,
                "metadata": {"snr": 30.0},
            },
        )

        assert result["media_id"] == "media_1"
        assert result["audio_result"] is not None
        assert result["overall_confidence"] > 0.0

    @pytest.mark.asyncio
    async def test_process_both(self, mock_audit_chain, mock_event_emitter):
        audio = AudioInspector(mock_audit_chain, mock_event_emitter, "_default")
        video = VideoInspector(mock_event_emitter, mock_audit_chain, "_default")
        maestro = Maestro(audio, video, mock_audit_chain, mock_event_emitter, "_default")

        result = await maestro.process_media(
            "media_1",
            audio_config={
                "audio_id": "audio_1",
                "duration_ms": 30000,
            },
            video_config={
                "video_id": "video_1",
                "duration_ms": 30000,
                "frame_count": 900,
                "fps": 30.0,
            },
        )

        assert result["audio_result"] is not None
        assert result["video_result"] is not None
        assert result["overall_confidence"] > 0.0


class TestAuditChainIntegration:
    """Test audit chain integration."""

    def test_all_events_logged(self, mock_audit_chain, mock_event_emitter):
        audio = AudioInspector(mock_audit_chain, mock_event_emitter, "_default")
        audio.analyze("audio_1", 30000)

        video = VideoInspector(mock_event_emitter, mock_audit_chain, "_default")
        video.analyze("video_1", 30000, 900, 30.0)

        # Should have called audit_chain.write at least 2 times
        assert mock_audit_chain.write.call_count >= 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
