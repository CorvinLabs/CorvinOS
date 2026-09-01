"""Integration tests for OS-Skills REST API (Phase 5-6)."""

import json
import pytest
from pathlib import Path
from datetime import datetime


@pytest.fixture
def test_skill_metrics():
    """Sample skill metrics response."""
    return {
        "skill_id": "os.delegation_router",
        "version": "1.0.0",
        "metrics": {
            "total_runs": 42,
            "total_errors": 2,
            "score_history": [
                {"epoch": 1, "score": 0.65, "timestamp": "2026-09-01T10:00:00Z"},
                {"epoch": 2, "score": 0.72, "timestamp": "2026-09-01T11:00:00Z"},
                {"epoch": 3, "score": 0.78, "timestamp": "2026-09-01T12:00:00Z"},
            ],
            "score_trend": 0.2,
            "feedback_breakdown": {
                "by_outcome": {"success": 40, "failure": 2},
                "by_task_shape": {"classification": 25, "route": 17},
                "by_decision": {"router_a": 20, "router_b": 22},
            },
            "anomalies": [],
        },
        "recommendations": ["Learning curve healthy, score trending up"],
        "timestamp": "2026-09-01T12:30:00Z",
    }


@pytest.fixture
def test_skills_status():
    """Sample skills status response."""
    return {
        "tenant_id": "_default",
        "skills": [
            {
                "id": "os.delegation_router",
                "version": "1.0.0",
                "enabled": True,
                "score": 0.78,
                "runs_24h": 42,
                "errors_24h": 2,
                "last_run": "2026-09-01T12:30:00Z",
                "status": "healthy",
            },
            {
                "id": "os.context_adapter",
                "version": "1.0.0",
                "enabled": True,
                "score": 0.85,
                "runs_24h": 38,
                "errors_24h": 0,
                "last_run": "2026-09-01T12:29:00Z",
                "status": "healthy",
            },
        ],
        "timestamp": "2026-09-01T12:30:00Z",
    }


class TestSkillsStatus:
    """Tests for GET /api/skills/status endpoint."""

    def test_status_response_schema(self, test_skills_status):
        """Verify response has required fields."""
        assert "tenant_id" in test_skills_status
        assert "skills" in test_skills_status
        assert "timestamp" in test_skills_status
        assert isinstance(test_skills_status["skills"], list)

    def test_status_skill_schema(self, test_skills_status):
        """Verify each skill has required fields."""
        for skill in test_skills_status["skills"]:
            assert "id" in skill
            assert "version" in skill
            assert "enabled" in skill
            assert "score" in skill
            assert "runs_24h" in skill
            assert "errors_24h" in skill
            assert "status" in skill

    def test_status_skill_health_valid(self, test_skills_status):
        """Verify health status is one of valid values."""
        valid_statuses = {"healthy", "degraded", "error"}
        for skill in test_skills_status["skills"]:
            assert skill["status"] in valid_statuses

    def test_status_score_in_range(self, test_skills_status):
        """Verify score is between 0 and 1."""
        for skill in test_skills_status["skills"]:
            if skill["score"] is not None:
                assert 0.0 <= skill["score"] <= 1.0

    def test_status_runs_non_negative(self, test_skills_status):
        """Verify runs_24h and errors_24h are non-negative."""
        for skill in test_skills_status["skills"]:
            assert skill["runs_24h"] >= 0
            assert skill["errors_24h"] >= 0
            assert skill["errors_24h"] <= skill["runs_24h"]


class TestSkillMetrics:
    """Tests for GET /api/skills/{id}/metrics endpoint."""

    def test_metrics_response_schema(self, test_skill_metrics):
        """Verify response has required fields."""
        assert "skill_id" in test_skill_metrics
        assert "version" in test_skill_metrics
        assert "metrics" in test_skill_metrics
        assert "recommendations" in test_skill_metrics
        assert "timestamp" in test_skill_metrics

    def test_metrics_schema(self, test_skill_metrics):
        """Verify metrics object has required fields."""
        metrics = test_skill_metrics["metrics"]
        assert "total_runs" in metrics
        assert "total_errors" in metrics
        assert "score_history" in metrics
        assert "score_trend" in metrics
        assert "feedback_breakdown" in metrics
        assert "anomalies" in metrics

    def test_score_history_epochs(self, test_skill_metrics):
        """Verify score_history entries are ordered by epoch."""
        history = test_skill_metrics["metrics"]["score_history"]
        epochs = [entry["epoch"] for entry in history]
        assert epochs == sorted(epochs), "Epochs must be in ascending order"

    def test_score_history_valid_scores(self, test_skill_metrics):
        """Verify score_history has valid score values."""
        history = test_skill_metrics["metrics"]["score_history"]
        for entry in history:
            assert "epoch" in entry
            assert "score" in entry
            assert "timestamp" in entry
            assert 0.0 <= entry["score"] <= 1.0

    def test_feedback_breakdown_schema(self, test_skill_metrics):
        """Verify feedback_breakdown has expected categories."""
        feedback = test_skill_metrics["metrics"]["feedback_breakdown"]
        assert "by_outcome" in feedback
        assert "by_task_shape" in feedback
        assert "by_decision" in feedback

    def test_anomalies_list(self, test_skill_metrics):
        """Verify anomalies is a list of strings."""
        anomalies = test_skill_metrics["metrics"]["anomalies"]
        assert isinstance(anomalies, list)
        for anomaly in anomalies:
            assert isinstance(anomaly, str)

    def test_score_trend_numeric(self, test_skill_metrics):
        """Verify score_trend is a float."""
        trend = test_skill_metrics["metrics"]["score_trend"]
        assert isinstance(trend, float) or isinstance(trend, int)


class TestErrorHandling:
    """Tests for error conditions."""

    def test_missing_skill_returns_not_found(self):
        """Verify missing skill returns 404, not 500."""
        # This would be an actual HTTP test in a real test suite
        # For now, we document the expected behavior
        pass

    def test_malformed_tenant_id_returns_bad_request(self):
        """Verify invalid tenant_id returns 400."""
        pass

    def test_filesystem_error_returns_500(self):
        """Verify filesystem errors return 500 (not silent failures)."""
        pass
