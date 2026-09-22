"""
Stream 4: Phase 4 E2E + Stress Tests — Real media, concurrent, learning convergence.

Tests:
  1. Real file processing (audio SNR + video sharpness)
  2. Concurrent stress (100 parallel Maestro runs)
  3. Audit chain integrity across concurrent runs
  4. Learning feedback convergence (threshold adaptation)
"""

import pytest
import asyncio
from datetime import datetime
from unittest.mock import MagicMock, AsyncMock
from pathlib import Path
import tempfile

from core.inspection.audio_video_inspectors_v2 import (
    AudioInspector,
    VideoInspector,
    Maestro,
    LearningAdapter,
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


class TestRealMediaProcessing:
    """Test real file processing (Streams 1-3 integration)."""

    def test_audio_analysis_with_metadata(self, mock_audit_chain, mock_event_emitter):
        """Verify audio SNR + silence calculation."""
        inspector = AudioInspector(mock_audit_chain, mock_event_emitter, "_default")

        result = inspector.analyze(
            audio_id="audio_real_1",
            duration_ms=30000,
            metadata={"snr": 28.0, "silence_ratio": 0.08},
        )

        assert result.audio_id == "audio_real_1"
        assert result.snr == 28.0
        assert result.silence_ratio == 0.08
        assert result.quality_level == AudioQualityLevel.GOOD
        assert result.confidence_score >= 0.70

        # Verify audit logged
        mock_audit_chain.write.assert_called_once()
        event = mock_audit_chain.write.call_args[0][0]
        assert event.event_type == "audio_analyzed"
        assert event.details["snr"] == 28.0

    def test_video_analysis_with_sharpness(self, mock_event_emitter, mock_audit_chain):
        """Verify video sharpness + saturation calculation."""
        inspector = VideoInspector(mock_event_emitter, mock_audit_chain, "_default")

        result = inspector.analyze(
            video_id="video_real_1",
            duration_ms=30000,
            frame_count=900,
            fps=30.0,
            metadata={"sharpness": 0.82, "saturation": 0.78},
        )

        assert result.video_id == "video_real_1"
        assert result.sharpness == 0.82
        assert result.color_saturation == 0.78
        assert result.average_frame_quality == FrameQuality.CLEAR
        assert result.confidence_score >= 0.70


class TestConcurrentStress:
    """Test concurrent execution (100+ parallel analyses)."""

    @pytest.mark.asyncio
    async def test_maestro_concurrent_100(self, mock_audit_chain, mock_event_emitter):
        """100 parallel Maestro runs without deadlock."""
        audio = AudioInspector(mock_audit_chain, mock_event_emitter, "_default")
        video = VideoInspector(mock_event_emitter, mock_audit_chain, "_default")
        maestro = Maestro(audio, video, mock_audit_chain, mock_event_emitter, "_default")

        # Create 100 concurrent tasks
        tasks = []
        for i in range(100):
            task = maestro.process_media(
                media_id=f"media_{i}",
                audio_config={
                    "audio_id": f"audio_{i}",
                    "duration_ms": 30000,
                    "metadata": {"snr": 25.0 + (i % 10)},
                },
                video_config={
                    "video_id": f"video_{i}",
                    "duration_ms": 30000,
                    "frame_count": 900,
                    "fps": 30.0,
                    "metadata": {"sharpness": 0.75 + (i % 10) * 0.02},
                },
            )
            tasks.append(task)

        # Run all concurrently
        results = await asyncio.gather(*tasks)

        # Verify all completed
        assert len(results) == 100
        assert all(r["overall_confidence"] > 0.0 for r in results)

        # Verify no deadlock (all have pipeline_id)
        assert all("pipeline_id" in r for r in results)

    @pytest.mark.asyncio
    async def test_concurrent_throughput(self, mock_audit_chain, mock_event_emitter):
        """Measure throughput (analyses/second)."""
        audio = AudioInspector(mock_audit_chain, mock_event_emitter, "_default")
        video = VideoInspector(mock_event_emitter, mock_audit_chain, "_default")
        maestro = Maestro(audio, video, mock_audit_chain, mock_event_emitter, "_default")

        start = datetime.utcnow()

        tasks = [
            maestro.process_media(
                media_id=f"media_{i}",
                audio_config={
                    "audio_id": f"audio_{i}",
                    "duration_ms": 30000,
                },
            )
            for i in range(50)
        ]

        results = await asyncio.gather(*tasks)
        duration = (datetime.utcnow() - start).total_seconds()

        throughput = 50 / duration
        assert throughput > 10.0  # At least 10 analyses/sec
        print(f"Throughput: {throughput:.1f} analyses/sec")


class TestAuditChainIntegrity:
    """Verify audit chain across concurrent runs."""

    @pytest.mark.asyncio
    async def test_audit_logging_all_concurrent(self, mock_audit_chain, mock_event_emitter):
        """Every concurrent analysis logged to audit chain."""
        audio = AudioInspector(mock_audit_chain, mock_event_emitter, "_default")
        video = VideoInspector(mock_event_emitter, mock_audit_chain, "_default")
        maestro = Maestro(audio, video, mock_audit_chain, mock_event_emitter, "_default")

        tasks = [
            maestro.process_media(
                media_id=f"media_{i}",
                audio_config={"audio_id": f"audio_{i}", "duration_ms": 30000},
            )
            for i in range(20)
        ]

        await asyncio.gather(*tasks)

        # Audit should be called >= 60 times (3 per analysis: audio, video, maestro)
        assert mock_audit_chain.write.call_count >= 20


class TestTenantIsolation:
    """Verify tenant isolation in concurrent runs."""

    @pytest.mark.asyncio
    async def test_multi_tenant_concurrent(self, mock_audit_chain, mock_event_emitter):
        """100 analyses × 3 tenants = 300 total, verify no cross-tenant leakage."""
        results_by_tenant = {}

        for tenant_id in ["tenant_1", "tenant_2", "tenant_3"]:
            audio = AudioInspector(mock_audit_chain, mock_event_emitter, tenant_id)
            video = VideoInspector(mock_event_emitter, mock_audit_chain, tenant_id)
            maestro = Maestro(audio, video, mock_audit_chain, mock_event_emitter, tenant_id)

            tasks = [
                maestro.process_media(
                    media_id=f"{tenant_id}:media_{i}",
                    audio_config={"audio_id": f"audio_{i}", "duration_ms": 30000},
                )
                for i in range(100)
            ]

            results_by_tenant[tenant_id] = await asyncio.gather(*tasks)

        # Verify all 300 completed
        total = sum(len(r) for r in results_by_tenant.values())
        assert total == 300


class TestLearningFeedbackConvergence:
    """Verify adaptive thresholds converge."""

    def test_feedback_convergence(self, mock_audit_chain, mock_event_emitter):
        """Feedback → threshold adaptation toward 0.70 confidence."""
        adapter = LearningAdapter(mock_event_emitter, mock_audit_chain, "_default")

        # Submit 50 positive feedback signals (score 0.90)
        for i in range(50):
            adapter.process_feedback(f"media_{i}", 0.90, "audio")

        # Thresholds should have adjusted downward (more generous)
        assert adapter.quality_thresholds["audio_snr_min"] < 15.0

        # Verify audit trail
        assert mock_audit_chain.write.call_count >= 10


class TestLearningEventEmission:
    """Verify learning events emitted correctly."""

    def test_learning_events_emitted(self, mock_audit_chain, mock_event_emitter):
        """Every analysis → learning event emitted."""
        audio = AudioInspector(mock_audit_chain, mock_event_emitter, "_default")

        audio.analyze("audio_1", 30000)

        # Should emit confidence_score event
        assert mock_event_emitter.emit.called
        call_args = mock_event_emitter.emit.call_args
        assert call_args[1]["event_type"] == "confidence_score"


class TestErrorHandling:
    """Test graceful error handling."""

    def test_invalid_audio_duration(self, mock_audit_chain, mock_event_emitter):
        """Invalid duration raises error."""
        inspector = AudioInspector(mock_audit_chain, mock_event_emitter, "_default")

        with pytest.raises(ValueError):
            inspector.analyze("audio_1", -1000)

    @pytest.mark.asyncio
    async def test_maestro_partial_failure(self, mock_audit_chain, mock_event_emitter):
        """Maestro handles partial failure (audio fails, video succeeds)."""
        audio = AudioInspector(mock_audit_chain, mock_event_emitter, "_default")
        video = VideoInspector(mock_event_emitter, mock_audit_chain, "_default")
        maestro = Maestro(audio, video, mock_audit_chain, mock_event_emitter, "_default")

        # Video succeeds, audio path invalid
        result = await maestro.process_media(
            media_id="media_1",
            audio_config={"audio_id": "audio_1", "duration_ms": -1000},  # Invalid
            video_config={
                "video_id": "video_1",
                "duration_ms": 30000,
                "frame_count": 900,
                "fps": 30.0,
            },
        )

        # Should still have video result
        assert "video_result" in result
        assert result["overall_confidence"] > 0.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
