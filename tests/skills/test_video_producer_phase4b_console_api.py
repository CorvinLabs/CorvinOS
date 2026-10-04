"""Phase 4b Tests: Console API Extensions (8+ tests).

Tests for video learning API endpoints:
- POST /v1/console/video/jobs/{job_id}/feedback
- GET /v1/console/video/learning/stats
- GET /v1/console/video/learning/models
- GET /v1/console/video/learning/confidence
- POST /v1/console/video/learning/select-model
- POST /v1/console/video/learning/report-quality
"""

import pytest

# ── Not runnable on main ──────────────────────────────────────────────────
# These tests assert the PRE-2026-10-03 route behaviour: unauthenticated
# requests, an unvalidated job id ("job1", not the real "job_<8 hex>" shape),
# `{"success": True}` returned unconditionally, and hardcoded sample numbers
# (0.78 confidence, 42 decisions, "claude-opus", every learning/models/
# confidence/select-model/report-quality endpoint). The 2026-10-03 adversarial
# review found exactly that — ADR-0763 "fabricates nothing" — and replaced it:
# feedback now needs a session+CSRF and a real job, returns an audit_ref (503
# if nothing was recorded) instead of an unconditional success flag, and the
# four unbuilt endpoints answer 501 instead of sample data.
#
# The live surface IS covered, by tests driven through the real router with a
# real session: core/console/tests/test_video_producer_routes_e2e.py
# (test_unbuilt_endpoints_answer_501_not_fabricated_success,
# test_scene_feedback_is_validated_recorded_and_read_back_per_tenant,
# test_job_level_feedback_records_or_404s, among others).
#
# Delete this guard (and rewrite the tests below against the current
# contract) in the commit that changes the route again, or delete the file.
pytest.skip(
    "Tests the pre-2026-10-03 fake route behaviour (unauthenticated, "
    "unconditional success, hardcoded learning numbers). See "
    "core/console/tests/test_video_producer_routes_e2e.py for the coverage "
    "that runs against the current (honest, tenant-scoped) route.",
    allow_module_level=True,
)

import json
import tempfile
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from core.console.corvin_console.routes import video_learning_api  # noqa: F401,E402


class TestVideoLearningAPI:
    """Console API for video learning infrastructure."""

    def test_submit_feedback_valid(self, client):
        """Test submitting valid feedback."""
        response = client.post(
            "/v1/console/video/jobs/job1/feedback",
            json={
                "scene_id": "s01",
                "feedback_type": "quality",
                "rating": 4,
                "worker_notes": "Good quality",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["rating"] == 4

    def test_submit_feedback_missing_scene_id(self, client):
        """Test feedback submission with missing scene_id."""
        response = client.post(
            "/v1/console/video/jobs/job1/feedback",
            json={
                "rating": 4,
            },
        )

        # FastAPI/pydantic answers a schema violation with 422 and a `detail`
        # list naming the missing field — not Flask's 400 + {"error": ...}.
        assert response.status_code == 422
        data = response.json()
        assert "detail" in data
        assert any("scene_id" in str(item) for item in data["detail"])

    def test_submit_feedback_invalid_rating(self, client):
        """Test feedback with invalid rating."""
        response = client.post(
            "/v1/console/video/jobs/job1/feedback",
            json={
                "scene_id": "s01",
                "rating": 6,  # Invalid
            },
        )

        assert response.status_code == 400

    def test_get_learning_stats(self, client):
        """Test getting learning statistics."""
        # Submit feedback first
        client.post(
            "/v1/console/video/jobs/job1/feedback",
            json={
                "scene_id": "s01",
                "feedback_type": "quality",
                "rating": 4,
            },
        )

        response = client.get("/v1/console/video/learning/stats")

        assert response.status_code == 200
        data = response.json()
        assert "confidence_metrics" in data
        assert "model_stats" in data
        assert "timestamp" in data

    def test_get_model_stats(self, client):
        """Test getting model selection statistics."""
        response = client.get("/v1/console/video/learning/models")

        assert response.status_code == 200
        data = response.json()
        assert "total_decisions" in data
        assert "by_duration" in data
        assert "1min" in data["by_duration"]

    def test_get_confidence_metrics(self, client):
        """Test getting confidence metrics."""
        response = client.get("/v1/console/video/learning/confidence")

        assert response.status_code == 200
        data = response.json()
        # Should return metrics for each worker type
        assert isinstance(data, dict)

    def test_select_model(self, client):
        """Test model selection endpoint."""
        response = client.post(
            "/v1/console/video/learning/select-model",
            json={"duration_seconds": 60},
        )

        assert response.status_code == 200
        data = response.json()
        assert "model" in data
        assert data["model"] in ["gpt-4", "claude-opus", "claude-sonnet"]
        assert data["duration_seconds"] == 60

    def test_report_video_quality(self, client):
        """Test video quality reporting."""
        # First select a model
        select_response = client.post(
            "/v1/console/video/learning/select-model",
            json={"duration_seconds": 60},
        )
        model = select_response.json()["model"]

        # Then report quality
        response = client.post(
            "/v1/console/video/learning/report-quality",
            json={
                "job_id": "job1",
                "duration_seconds": 60,
                "quality_score": 0.8,
                "model_used": model,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["job_id"] == "job1"

    def test_learning_health_check(self, client):
        """Test learning infrastructure health check."""
        response = client.get("/v1/console/video/learning/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "components" in data
        assert data["components"]["feedback_collector"] == "ready"
        assert data["components"]["model_selector"] == "ready"


# ============================================================================
# Integration Tests
# ============================================================================

class TestVideoLearningAPIIntegration:
    """Full API integration tests."""

    def test_full_feedback_loop_api(self, client):
        """Test complete feedback → model selection → report loop via API."""
        # Step 1: Submit feedback
        feedback_response = client.post(
            "/v1/console/video/jobs/job1/feedback",
            json={
                "scene_id": "s01",
                "feedback_type": "quality",
                "rating": 3,
                "worker_notes": "voice is slow",
            },
        )
        assert feedback_response.status_code == 200

        # Step 2: Get stats
        stats_response = client.get("/v1/console/video/learning/stats")
        assert stats_response.status_code == 200

        # Step 3: Select model for next video
        select_response = client.post(
            "/v1/console/video/learning/select-model",
            json={"duration_seconds": 60},
        )
        assert select_response.status_code == 200
        model = select_response.json()["model"]

        # Step 4: Report quality
        report_response = client.post(
            "/v1/console/video/learning/report-quality",
            json={
                "job_id": "job2",
                "duration_seconds": 60,
                "quality_score": 0.85,
                "model_used": model,
            },
        )
        assert report_response.status_code == 200

        # Verify model stats updated
        models_response = client.get("/v1/console/video/learning/models")
        assert models_response.status_code == 200
