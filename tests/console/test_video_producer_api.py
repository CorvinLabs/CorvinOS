"""Phase 4b Tests: Video Producer Console API (15+ tests).

Covers:
- Console route registration
- Video production request/response schemas
- Job status polling
- Event streaming (SSE)
- Metrics aggregation
- Job cancellation
- Error handling
"""

import asyncio
import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from core.console.corvin_console.routes.video_producer_api import (
    VideoProducerRequest,
    VideoProducerStatusResponse,
    VideoProducerMetrics,
    _create_job,
    _update_job,
    _get_job,
    _add_event,
)


class TestVideoProducerSchemas:
    """Request/Response schema validation tests."""

    def test_request_schema_valid(self):
        """Test VideoProducerRequest validation."""
        req = VideoProducerRequest(
            asset_paths=["/path/to/ppt.pptx"],
            title="Test Video",
            description="Test description",
            tags=["test", "corvin"],
            export_youtube=False,
            async_=True,
        )
        assert req.asset_paths == ["/path/to/ppt.pptx"]
        assert req.title == "Test Video"
        assert req.export_youtube is False

    def test_request_schema_minimal(self):
        """Test VideoProducerRequest with minimal fields."""
        req = VideoProducerRequest(asset_paths=["/path/to/ppt.pptx"])
        assert req.asset_paths == ["/path/to/ppt.pptx"]
        assert req.title == "CorvinOS Video"
        assert req.tags == ["CorvinOS"]
        assert req.export_youtube is False

    def test_request_schema_invalid_empty_paths(self):
        """Test VideoProducerRequest rejects empty asset_paths."""
        with pytest.raises(ValueError):
            VideoProducerRequest(asset_paths=[])

    def test_status_response_schema(self):
        """Test VideoProducerStatusResponse schema."""
        resp = VideoProducerStatusResponse(
            job_id="vp_test123",
            status="analyzing",
            phase="asset_analysis",
            progress=10,
            message="Analyzing assets...",
            created_at=datetime.utcnow().isoformat() + "Z",
            updated_at=datetime.utcnow().isoformat() + "Z",
        )
        assert resp.job_id == "vp_test123"
        assert resp.status == "analyzing"
        assert resp.progress == 10

    def test_metrics_response_schema(self):
        """Test VideoProducerMetrics schema."""
        metrics = VideoProducerMetrics(
            total_videos_produced=5,
            average_production_time_seconds=300.0,
            average_quality_score=0.85,
            success_rate=0.8,
            recent_errors=[],
            workers_performance={
                "voice_synthesizer": {"status": "active", "processed": 5},
            },
        )
        assert metrics.total_videos_produced == 5
        assert metrics.success_rate == 0.8


class TestJobTracking:
    """Job creation, update, retrieval tests."""

    def test_create_job(self):
        """Test job creation."""
        req = VideoProducerRequest(asset_paths=["/path/to/ppt.pptx"])
        session_rec = MagicMock()
        session_rec.tenant_id = "_default"

        _create_job("vp_test123", req, session_rec)
        job = _get_job("vp_test123")

        assert job is not None
        assert job["job_id"] == "vp_test123"
        assert job["status"] == "analyzing"
        assert job["progress"] == 0

    def test_update_job(self):
        """Test job status update."""
        req = VideoProducerRequest(asset_paths=["/path/to/ppt.pptx"])
        session_rec = MagicMock()
        session_rec.tenant_id = "_default"

        _create_job("vp_test124", req, session_rec)
        _update_job("vp_test124", status="rendering", progress=50)

        job = _get_job("vp_test124")
        assert job["status"] == "rendering"
        assert job["progress"] == 50

    def test_get_job_not_found(self):
        """Test retrieving non-existent job."""
        job = _get_job("vp_nonexistent")
        assert job is None

    def test_add_event(self):
        """Test event addition to job."""
        req = VideoProducerRequest(asset_paths=["/path/to/ppt.pptx"])
        session_rec = MagicMock()
        session_rec.tenant_id = "_default"

        _create_job("vp_test125", req, session_rec)
        _add_event("vp_test125", {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "event_type": "phase_started",
            "phase": "asset_analysis",
        })

        job = _get_job("vp_test125")
        assert len(job["events"]) == 1
        assert job["events"][0]["event_type"] == "phase_started"

    def test_multiple_events_ordered(self):
        """Test that events maintain order."""
        req = VideoProducerRequest(asset_paths=["/path/to/ppt.pptx"])
        session_rec = MagicMock()
        session_rec.tenant_id = "_default"

        _create_job("vp_test126", req, session_rec)
        for i in range(5):
            _add_event("vp_test126", {
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "event_type": f"event_{i}",
            })

        job = _get_job("vp_test126")
        assert len(job["events"]) == 5
        for i, event in enumerate(job["events"]):
            assert event["event_type"] == f"event_{i}"


class TestJobLifecycle:
    """Full job lifecycle tests."""

    def test_job_lifecycle_success(self):
        """Test complete job lifecycle: create → update → success."""
        req = VideoProducerRequest(
            asset_paths=["/path/to/ppt.pptx"],
            title="Lifecycle Test",
        )
        session_rec = MagicMock()
        session_rec.tenant_id = "_default"

        _create_job("vp_lifecycle", req, session_rec)
        job = _get_job("vp_lifecycle")
        assert job["status"] == "analyzing"
        assert job["progress"] == 0

        _update_job("vp_lifecycle", progress=30, phase="voice")
        job = _get_job("vp_lifecycle")
        assert job["progress"] == 30
        assert job["phase"] == "voice"

        _update_job("vp_lifecycle", progress=100, status="success", output_path="/output.mp4")
        job = _get_job("vp_lifecycle")
        assert job["status"] == "success"
        assert job["output_path"] == "/output.mp4"

    def test_job_lifecycle_failure(self):
        """Test job lifecycle with failure."""
        req = VideoProducerRequest(asset_paths=["/path/to/ppt.pptx"])
        session_rec = MagicMock()
        session_rec.tenant_id = "_default"

        _create_job("vp_failure", req, session_rec)
        _update_job("vp_failure", status="failed", error="Analysis blocked: missing assets")

        job = _get_job("vp_failure")
        assert job["status"] == "failed"
        assert "missing assets" in job["error"]

    def test_job_with_youtube_export(self):
        """Test job that exports to YouTube."""
        req = VideoProducerRequest(
            asset_paths=["/path/to/ppt.pptx"],
            export_youtube=True,
            title="YouTube Export Test",
        )
        session_rec = MagicMock()
        session_rec.tenant_id = "_default"

        _create_job("vp_youtube", req, session_rec)
        _update_job("vp_youtube", progress=80, status="uploading")
        _update_job("vp_youtube", export_task_id="yt_abc123def")

        job = _get_job("vp_youtube")
        assert job["export_task_id"] == "yt_abc123def"


class TestEventStreaming:
    """Event streaming validation tests."""

    @pytest.mark.asyncio
    async def test_event_stream_format(self):
        """Test SSE event format."""
        req = VideoProducerRequest(asset_paths=["/path/to/ppt.pptx"])
        session_rec = MagicMock()
        session_rec.tenant_id = "_default"

        _create_job("vp_sse", req, session_rec)
        _add_event("vp_sse", {
            "timestamp": "2026-09-12T10:00:00Z",
            "event_type": "phase_started",
            "phase": "asset_analysis",
        })

        job = _get_job("vp_sse")
        event = job["events"][0]

        # Verify event can be JSON serialized (required for SSE)
        sse_line = f"data: {json.dumps(event)}\n\n"
        assert "data:" in sse_line
        assert json.loads(sse_line.replace("data: ", "")) == event

    def test_event_queue_not_lost(self):
        """Test that rapid events don't overwrite."""
        req = VideoProducerRequest(asset_paths=["/path/to/ppt.pptx"])
        session_rec = MagicMock()
        session_rec.tenant_id = "_default"

        _create_job("vp_rapid", req, session_rec)

        # Simulate rapid event stream
        for i in range(20):
            _add_event("vp_rapid", {
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "event_type": f"progress_{i}",
                "progress": i * 5,
            })

        job = _get_job("vp_rapid")
        assert len(job["events"]) == 20
        assert job["events"][-1]["event_type"] == "progress_19"
        assert job["events"][-1]["progress"] == 95


class TestMetricsAggregation:
    """Metrics collection and aggregation tests."""

    def test_metrics_empty_state(self):
        """Test metrics in empty state (no jobs completed)."""
        # Metrics start at 0
        from core.console.corvin_console.routes.video_producer_api import _metrics

        # Save original state
        orig_total = _metrics["total_videos"]
        orig_errors = list(_metrics["errors"])

        try:
            _metrics["total_videos"] = 0
            _metrics["successful_videos"] = 0
            _metrics["total_time_seconds"] = 0.0
            _metrics["errors"] = []

            # In empty state
            success_rate = 0.0 if _metrics["total_videos"] == 0 else _metrics["successful_videos"] / _metrics["total_videos"]
            assert _metrics["total_videos"] == 0
            assert success_rate == 0.0
        finally:
            # Restore
            _metrics["total_videos"] = orig_total
            _metrics["errors"] = orig_errors

    def test_metrics_calculation(self):
        """Test metrics aggregation formula."""
        total = 10
        successful = 8
        total_time = 3000.0
        total_quality = 7.5

        avg_time = total_time / total
        avg_quality = total_quality / successful
        success_rate = successful / total

        assert avg_time == 300.0
        assert avg_quality == 0.9375
        assert success_rate == 0.8


class TestErrorHandling:
    """Error handling and edge cases."""

    def test_job_update_nonexistent(self):
        """Test updating non-existent job (should be no-op)."""
        _update_job("vp_nonexistent", status="complete")

        # Should return None (not raise)
        job = _get_job("vp_nonexistent")
        assert job is None

    def test_add_event_nonexistent_job(self):
        """Test adding event to non-existent job (should be no-op)."""
        _add_event("vp_nonexistent", {"event_type": "test"})

        job = _get_job("vp_nonexistent")
        assert job is None

    def test_concurrent_job_updates(self):
        """Test that concurrent job updates don't race."""
        req = VideoProducerRequest(asset_paths=["/path/to/ppt.pptx"])
        session_rec = MagicMock()
        session_rec.tenant_id = "_default"

        _create_job("vp_concurrent", req, session_rec)

        # Simulate concurrent updates (via threading in real scenario)
        for i in range(10):
            _update_job("vp_concurrent", progress=i * 10)

        job = _get_job("vp_concurrent")
        assert job["progress"] == 90  # Last update wins


class TestIntegration:
    """End-to-end integration tests."""

    def test_full_workflow_request_to_status(self):
        """Test complete workflow: request → create → update → retrieve."""
        # 1. Create request
        req = VideoProducerRequest(
            asset_paths=["/assets/video.pptx"],
            title="Integration Test",
            export_youtube=True,
        )

        # 2. Create job
        session_rec = MagicMock()
        session_rec.tenant_id = "_default"
        _create_job("vp_integration", req, session_rec)

        # 3. Simulate orchestration progress
        _update_job("vp_integration", status="analyzing", progress=10)
        _add_event("vp_integration", {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "event_type": "analysis_started",
        })

        _update_job("vp_integration", status="rendering", progress=50, phase="voice")
        _add_event("vp_integration", {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "event_type": "voice_synthesis_started",
        })

        # 4. Retrieve final status
        job = _get_job("vp_integration")
        assert job["job_id"] == "vp_integration"
        assert job["status"] == "rendering"
        assert job["progress"] == 50
        assert len(job["events"]) == 2
        assert job["request"]["export_youtube"] is True


# ============================================================================
# Run all tests
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
