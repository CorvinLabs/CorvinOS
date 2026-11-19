"""
Tests for ADR-0274 Measurement API Server
"""

import json
import sys
import pytest
from pathlib import Path
from datetime import datetime, timedelta

# Fix import path (avoid 'operator' stdlib collision)
sys.path.insert(0, str(Path(__file__).parent.parent))
from api_server import app, MeasurementReader


TEST_KEY = "test-key-0123456789abcdef"


@pytest.fixture
def client(monkeypatch):
    """Flask test client that sends the operator's API key (R3-NEW-36: every request
    needs it — there is no built-in key and no loopback exemption)."""
    monkeypatch.setenv("CORVIN_TELEMETRY_API_KEY", TEST_KEY)
    app.config["TESTING"] = True
    with app.test_client() as client:
        client.environ_base["HTTP_X_API_KEY"] = TEST_KEY
        yield client


@pytest.fixture
def anon_client():
    """Flask test client without a key (remote_addr is 127.0.0.1, as for any local caller)."""
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


@pytest.fixture
def temp_measurement_dir(tmp_path):
    """Create temporary measurement directory with sample data."""
    # Create today's directory
    today = datetime.utcnow().strftime("%Y-%m-%d")
    date_dir = tmp_path / today
    date_dir.mkdir(parents=True, exist_ok=True)

    # Create predictions.jsonl with 5 samples
    predictions_file = date_dir / "predictions.jsonl"
    predictions = [
        {"timestamp": datetime.utcnow().isoformat(), "context_id": "adr-0270",
         "confidence_pred": 0.75, "outcome_actual": 0.78},
        {"timestamp": datetime.utcnow().isoformat(), "context_id": "adr-0271",
         "confidence_pred": 0.82, "outcome_actual": 0.80},
        {"timestamp": datetime.utcnow().isoformat(), "context_id": "skill-e2e",
         "confidence_pred": 0.70, "outcome_actual": 0.72},
    ]
    with open(predictions_file, "a", encoding="utf-8") as f:
        for p in predictions:
            f.write(json.dumps(p) + "\n")

    # Create feedback.jsonl with 5 samples
    feedback_file = date_dir / "feedback.jsonl"
    feedback = [
        {"timestamp": datetime.utcnow().isoformat(), "context_id": "adr-0270",
         "feedback_impact": "helpful", "score_before": 0.70, "score_after": 0.75},
        {"timestamp": datetime.utcnow().isoformat(), "context_id": "adr-0271",
         "feedback_impact": "neutral", "score_before": 0.80, "score_after": 0.80},
        {"timestamp": datetime.utcnow().isoformat(), "context_id": "skill-e2e",
         "feedback_impact": "harmful", "score_before": 0.75, "score_after": 0.70},
    ]
    with open(feedback_file, "a", encoding="utf-8") as f:
        for fb in feedback:
            f.write(json.dumps(fb) + "\n")

    # Create user_choices.jsonl
    choices_file = date_dir / "user_choices.jsonl"
    choices = [
        {"timestamp": datetime.utcnow().isoformat(), "user_id": "user1",
         "decision_style": "pragmatic", "task_type": "ml"},
        {"timestamp": datetime.utcnow().isoformat(), "user_id": "user1",
         "decision_style": "rigorous", "task_type": "refactor"},
        {"timestamp": datetime.utcnow().isoformat(), "user_id": "user2",
         "decision_style": "pragmatic", "task_type": "devops"},
    ]
    with open(choices_file, "a", encoding="utf-8") as f:
        for c in choices:
            f.write(json.dumps(c) + "\n")

    # Create budget_allocations.jsonl
    budget_file = date_dir / "budget_allocations.jsonl"
    budget = [
        {"timestamp": datetime.utcnow().isoformat(), "task_id": "task-1",
         "budget_allocated": "critical", "complexity_est": 8.5, "tokens_used": 2000,
         "match_score": 0.95},
        {"timestamp": datetime.utcnow().isoformat(), "task_id": "task-2",
         "budget_allocated": "important", "complexity_est": 5.0, "tokens_used": 1200,
         "match_score": 0.85},
        {"timestamp": datetime.utcnow().isoformat(), "task_id": "task-3",
         "budget_allocated": "nice_to_have", "complexity_est": 2.0, "tokens_used": 500,
         "match_score": 0.90},
    ]
    with open(budget_file, "a", encoding="utf-8") as f:
        for b in budget:
            f.write(json.dumps(b) + "\n")

    return tmp_path


class TestMeasurementReader:
    """Test MeasurementReader class."""

    def test_read_jsonl_file(self, temp_measurement_dir):
        """Test reading JSONL file."""
        reader = MeasurementReader(temp_measurement_dir)
        today = datetime.utcnow().strftime("%Y-%m-%d")
        predictions_file = temp_measurement_dir / today / "predictions.jsonl"

        records = reader.read_jsonl_file(predictions_file)
        assert len(records) == 3
        assert records[0]["context_id"] == "skill-e2e"  # Latest first (reversed)

    def test_compute_adr_0270_stats(self, temp_measurement_dir):
        """Test ADR-0270 confidence accuracy."""
        reader = MeasurementReader(temp_measurement_dir)
        today = datetime.utcnow().strftime("%Y-%m-%d")
        predictions = reader.read_jsonl_file(
            temp_measurement_dir / today / "predictions.jsonl"
        )

        stats = reader.compute_adr_0270_stats(predictions)
        assert stats["count"] == 3
        assert "accuracy" in stats
        assert 0.0 <= stats["accuracy"] <= 1.0
        assert stats["contexts_tracked"] == 3

    def test_compute_adr_0271_stats(self, temp_measurement_dir):
        """Test ADR-0271 Bayesian learning."""
        reader = MeasurementReader(temp_measurement_dir)
        today = datetime.utcnow().strftime("%Y-%m-%d")
        feedback = reader.read_jsonl_file(
            temp_measurement_dir / today / "feedback.jsonl"
        )

        stats = reader.compute_adr_0271_stats(feedback)
        assert stats["count"] == 3
        assert "helpful_pct" in stats
        assert stats["helpful_pct"] > 0  # At least one helpful

    def test_compute_adr_0272_stats(self, temp_measurement_dir):
        """Test ADR-0272 user preferences."""
        reader = MeasurementReader(temp_measurement_dir)
        today = datetime.utcnow().strftime("%Y-%m-%d")
        choices = reader.read_jsonl_file(
            temp_measurement_dir / today / "user_choices.jsonl"
        )

        stats = reader.compute_adr_0272_stats(choices)
        assert stats["count"] == 3
        assert "task_types" in stats
        assert stats["unique_users"] >= 1

    def test_compute_adr_0273_stats(self, temp_measurement_dir):
        """Test ADR-0273 budget allocation."""
        reader = MeasurementReader(temp_measurement_dir)
        today = datetime.utcnow().strftime("%Y-%m-%d")
        budget = reader.read_jsonl_file(
            temp_measurement_dir / today / "budget_allocations.jsonl"
        )

        stats = reader.compute_adr_0273_stats(budget)
        assert stats["count"] == 3
        assert "avg_match" in stats
        assert 0.0 <= stats["avg_match"] <= 1.0
        assert stats["total_tokens"] > 0


class TestAPIEndpoints:
    """Test Flask API endpoints."""

    def test_health_check(self, client):
        """Test /health endpoint."""
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json["status"] == "ok"

    def test_latest_measurements(self, client, temp_measurement_dir, monkeypatch):
        """Test /api/v1/measurements/latest endpoint."""
        # Mock the reader to use temp directory
        import api_server
        api_server.reader = MeasurementReader(temp_measurement_dir)

        response = client.get("/api/v1/measurements/latest?days=7")
        assert response.status_code == 200
        data = response.json

        assert "timestamp" in data
        assert "stats" in data
        assert "adr_0270_uncertainty" in data["stats"]
        assert "adr_0271_feedback" in data["stats"]
        assert "adr_0272_preferences" in data["stats"]
        assert "adr_0273_budget" in data["stats"]

    def test_predictions_endpoint(self, client, temp_measurement_dir):
        """Test /api/v1/measurements/predictions endpoint."""
        import api_server
        api_server.reader = MeasurementReader(temp_measurement_dir)

        response = client.get("/api/v1/measurements/predictions")
        assert response.status_code == 200
        data = response.json

        assert data["track"] == "ADR-0270 Uncertainty Quantification"
        assert "stats" in data
        assert "recent" in data

    def test_feedback_endpoint(self, client, temp_measurement_dir):
        """Test /api/v1/measurements/feedback endpoint."""
        from .. import api_server
        api_server.reader = MeasurementReader(temp_measurement_dir)

        response = client.get("/api/v1/measurements/feedback")
        assert response.status_code == 200
        data = response.json

        assert data["track"] == "ADR-0271 Outcome Feedback Loop"
        assert "stats" in data

    def test_preferences_endpoint(self, client, temp_measurement_dir):
        """Test /api/v1/measurements/preferences endpoint."""
        from .. import api_server
        api_server.reader = MeasurementReader(temp_measurement_dir)

        response = client.get("/api/v1/measurements/preferences")
        assert response.status_code == 200
        data = response.json

        assert data["track"] == "ADR-0272 User Preferences"
        assert "stats" in data

    def test_budget_endpoint(self, client, temp_measurement_dir):
        """Test /api/v1/measurements/budget endpoint."""
        from .. import api_server
        api_server.reader = MeasurementReader(temp_measurement_dir)

        response = client.get("/api/v1/measurements/budget")
        assert response.status_code == 200
        data = response.json

        assert data["track"] == "ADR-0273 Attention Budget"
        assert "stats" in data


class TestAPIKeyGate:
    """R3-NEW-36: no default key, no loopback bypass, constant-time compare, generic errors."""

    PATHS = ["/health", "/api/v1/measurements/latest", "/api/v1/talent/score"]

    def test_no_key_configured_serves_nothing(self, anon_client, monkeypatch):
        monkeypatch.delenv("CORVIN_TELEMETRY_API_KEY", raising=False)
        for path in self.PATHS:
            for headers in ({}, {"X-API-Key": "local-dev-only"}, {"X-API-Key": ""}):
                r = anon_client.get(path, headers=headers)
                assert r.status_code == 503, (path, headers, r.status_code)

    def test_former_default_key_is_refused_even_when_configured(self, anon_client, monkeypatch):
        monkeypatch.setenv("CORVIN_TELEMETRY_API_KEY", "local-dev-only")
        r = anon_client.get("/health", headers={"X-API-Key": "local-dev-only"})
        assert r.status_code == 503

    def test_short_key_is_not_a_key(self, anon_client, monkeypatch):
        monkeypatch.setenv("CORVIN_TELEMETRY_API_KEY", "abc")
        assert anon_client.get("/health", headers={"X-API-Key": "abc"}).status_code == 503

    def test_loopback_caller_without_key_is_unauthorized(self, anon_client, monkeypatch):
        monkeypatch.setenv("CORVIN_TELEMETRY_API_KEY", TEST_KEY)
        for path in self.PATHS:
            r = anon_client.get(path, environ_base={"REMOTE_ADDR": "127.0.0.1"})
            assert r.status_code == 401, (path, r.status_code)
            r = anon_client.get(path, headers={"X-API-Key": TEST_KEY[:-1]})
            assert r.status_code == 401
            r = anon_client.get(path, headers={"X-API-Key": TEST_KEY + "x"})
            assert r.status_code == 401

    def test_positive_control_right_key_is_served(self, anon_client, monkeypatch):
        monkeypatch.setenv("CORVIN_TELEMETRY_API_KEY", TEST_KEY)
        r = anon_client.get("/health", headers={"X-API-Key": TEST_KEY})
        assert r.status_code == 200

    def test_compare_is_constant_time_primitive(self):
        import inspect
        import api_server
        src = inspect.getsource(api_server.validate_api_key)
        assert "compare_digest" in src and "!=" not in src

    def test_error_body_never_carries_the_exception_text(self, client, monkeypatch):
        import api_server

        def _boom():
            raise RuntimeError("/home/operator/.corvin/secret-path leaked")
        monkeypatch.setattr(api_server, "get_talent_calculator", _boom)
        for path in ("/api/v1/talent/score", "/api/v1/talent/ranking", "/api/v1/talent/insights",
                     "/api/v1/talent/story"):
            r = client.get(path)
            assert r.status_code == 500, path
            assert r.json == {"error": "internal error"}, r.json

    def test_default_queue_root_is_under_corvin_home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "ch"))
        import talent_score
        assert MeasurementReader().queue_root == tmp_path / "ch" / "measurement"
        assert talent_score.TalentScoreCalculator().queue_root == tmp_path / "ch" / "measurement"
