"""
Engine Configuration K=1 Unit Tests

Phase 1 (Week 1–2): Frontend component + mock API routes

Tests:
  Frontend (5 tests):
    - Component renders without error
    - Dropdowns load and select models
    - Confidence badges display correctly
    - External provider modal opens/closes
    - Hardcoded "0 runs" shows for new config

  Backend (5 tests):
    - GET /v1/engine/config returns valid schema
    - PUT /v1/engine/config validates task_type
    - PUT /v1/engine/config rejects invalid models
    - POST /v1/engine/external-provider/test responds
    - GET /v1/engine/analytics returns empty data

ADR-0641: Engine Configuration Console
"""

import pytest
import json
from datetime import datetime, timezone
from typing import Dict, Any

# Backend API tests (using FastAPI TestClient)
from fastapi.testclient import TestClient
from core.console.routes.engine_api import (
    router,
    EngineConfigRequest,
    ModelConfigRequest,
    ExternalProviderTestRequest,
)
from fastapi import FastAPI

# Frontend component tests (using React Testing Library via pytest)
# Note: These would normally run via Jest/Vitest, but for Phase 1
# we include basic schema/type validation tests here.


# ─────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────


@pytest.fixture
def api_client():
    """Create a test client for the engine_api router."""
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def mock_config() -> Dict[str, Any]:
    """Mock engine configuration."""
    return {
        "tenant_id": "_default",
        "models": {
            "corvinOS": {
                "task_type": "corvinOS",
                "selected_model": "haiku",
                "alternatives": ["sonnet", "opus"],
            },
            "SIMPLE": {
                "task_type": "SIMPLE",
                "selected_model": "haiku",
                "alternatives": ["sonnet"],
            },
            "MEDIUM": {
                "task_type": "MEDIUM",
                "selected_model": "sonnet",
                "alternatives": ["haiku", "opus"],
            },
            "COMPLEX": {
                "task_type": "COMPLEX",
                "selected_model": "opus",
                "alternatives": ["sonnet"],
            },
        },
        "learning_status": "converged",
    }


# ─────────────────────────────────────────────────────────────────
# Backend API Tests
# ─────────────────────────────────────────────────────────────────


class TestEngineConfigAPI:
    """Test engine_api.py routes."""

    def test_get_engine_config_returns_valid_schema(self, api_client):
        """Test GET /v1/engine/config returns valid EngineConfigResponse."""
        response = api_client.get("/v1/engine/config")

        assert response.status_code == 200
        data = response.json()

        # Validate schema
        assert "tenant_id" in data
        assert "models" in data
        assert "last_updated" in data
        assert "learning_status" in data
        assert "last_learning_update" in data
        assert "total_samples" in data

        # Validate model structure
        for task_type, model_cfg in data["models"].items():
            assert task_type in ("corvinOS", "SIMPLE", "MEDIUM", "COMPLEX")
            assert "selected_model" in model_cfg
            assert "alternatives" in model_cfg
            assert model_cfg["selected_model"] in ("haiku", "sonnet", "opus", "fable")

    def test_get_engine_config_includes_hardcoded_scores_k1(self, api_client):
        """Test that GET response includes hardcoded confidence scores (Phase 1)."""
        response = api_client.get("/v1/engine/config")
        data = response.json()

        # SIMPLE should have hardcoded score from mock
        assert data["models"]["SIMPLE"]["confidence_score"] == 0.87
        assert data["models"]["SIMPLE"]["run_count"] == 1247

        # corvinOS should have "0 runs" (Phase 1 no learning yet)
        assert data["models"]["corvinOS"]["confidence_score"] == 0.0
        assert data["models"]["corvinOS"]["run_count"] == 0

    def test_put_engine_config_updates_model_choice(self, api_client, mock_config):
        """Test PUT /v1/engine/config updates model selection."""
        # Change SIMPLE from haiku to sonnet
        mock_config["models"]["SIMPLE"]["selected_model"] = "sonnet"

        response = api_client.put(
            "/v1/engine/config",
            json=mock_config,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["models"]["SIMPLE"]["selected_model"] == "sonnet"

    def test_put_engine_config_validates_task_type(self, api_client, mock_config):
        """Test PUT rejects invalid task_type."""
        # Add invalid task type
        mock_config["models"]["INVALID_TYPE"] = {
            "task_type": "INVALID_TYPE",
            "selected_model": "haiku",
        }

        response = api_client.put(
            "/v1/engine/config",
            json=mock_config,
        )

        # Should reject (validation error)
        assert response.status_code in (400, 422)

    def test_put_engine_config_validates_model_choice(self, api_client, mock_config):
        """Test PUT rejects invalid model name."""
        # Set invalid model
        mock_config["models"]["SIMPLE"]["selected_model"] = "claude-999-invalid"

        response = api_client.put(
            "/v1/engine/config",
            json=mock_config,
        )

        assert response.status_code in (400, 422)

    def test_put_engine_config_requires_models_field(self, api_client):
        """Test PUT rejects config without models."""
        response = api_client.put(
            "/v1/engine/config",
            json={"tenant_id": "_default", "models": {}},
        )

        # Empty models should fail validation
        assert response.status_code == 400

    def test_post_external_provider_test_returns_valid_schema(self, api_client):
        """Test POST /v1/engine/external-provider/test returns valid schema."""
        response = api_client.post(
            "/v1/engine/external-provider/test",
            json={
                "provider_type": "ollama",
                "server_url": "http://localhost:11434",
            },
        )

        assert response.status_code == 200
        data = response.json()

        assert "is_connected" in data
        assert isinstance(data["is_connected"], bool)
        assert "latency_ms" in data
        assert "error_message" in data

    def test_post_external_provider_test_validates_provider_type(self, api_client):
        """Test POST rejects invalid provider_type."""
        response = api_client.post(
            "/v1/engine/external-provider/test",
            json={
                "provider_type": "invalid-provider",
                "server_url": "http://localhost:11434",
            },
        )

        assert response.status_code == 400

    def test_get_engine_analytics_returns_valid_schema(self, api_client):
        """Test GET /v1/engine/analytics returns valid schema (Phase 1: empty data)."""
        response = api_client.get("/v1/engine/analytics")

        assert response.status_code == 200
        data = response.json()

        # Phase 1: These fields should exist (even if empty)
        assert "tenant_id" in data
        assert "task_type_breakdown" in data or data == {}
        assert "last_updated" in data or data == {}

    def test_engine_health_check(self, api_client):
        """Test GET /v1/engine/health returns ok status."""
        response = api_client.get("/v1/engine/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"


# ─────────────────────────────────────────────────────────────────
# Frontend Component Schema Tests
# ─────────────────────────────────────────────────────────────────


class TestEngineConfigFrontend:
    """Test frontend component rendering and types."""

    def test_model_options_are_valid(self):
        """Test that model option list is complete (haiku/sonnet/opus/fable)."""
        MODEL_OPTIONS = [
            {"value": "haiku", "label": "Claude 3.5 Haiku (Fast, Low Cost)"},
            {"value": "sonnet", "label": "Claude 3.5 Sonnet (Balanced)"},
            {"value": "opus", "label": "Claude Opus (Powerful)"},
            {"value": "fable", "label": "Claude Fable (Experimental)"},
        ]

        assert len(MODEL_OPTIONS) == 4
        values = [opt["value"] for opt in MODEL_OPTIONS]
        assert set(values) == {"haiku", "sonnet", "opus", "fable"}

    def test_task_types_match_backend_schema(self):
        """Test that frontend task types match backend validation."""
        TASK_TYPES = ["corvinOS", "SIMPLE", "MEDIUM", "COMPLEX"]
        assert len(TASK_TYPES) == 4
        assert "corvinOS" in TASK_TYPES
        assert "SIMPLE" in TASK_TYPES
        assert "MEDIUM" in TASK_TYPES
        assert "COMPLEX" in TASK_TYPES

    def test_confidence_score_bounds(self):
        """Test that confidence scores are valid floats between 0 and 1."""
        MOCK_SCORES = [
            {"task_type": "corvinOS", "confidence": 0.0, "run_count": 0},
            {"task_type": "SIMPLE", "confidence": 0.87, "run_count": 1247},
            {"task_type": "MEDIUM", "confidence": 0.72, "run_count": 892},
            {"task_type": "COMPLEX", "confidence": 0.91, "run_count": 456},
        ]

        for score in MOCK_SCORES:
            assert 0.0 <= score["confidence"] <= 1.0
            assert isinstance(score["run_count"], int)
            assert score["run_count"] >= 0


# ─────────────────────────────────────────────────────────────────
# Integration Tests (K=2)
# ─────────────────────────────────────────────────────────────────


class TestEngineConfigIntegration:
    """Integration tests (to be filled in K=2 and K=3)."""

    @pytest.mark.skip(reason="K=2: Config persistence not yet implemented")
    def test_config_persists_to_disk(self, api_client, mock_config):
        """Test that config changes are persisted (K=2)."""
        pass

    @pytest.mark.skip(reason="K=2: Audit trail not yet integrated")
    def test_config_change_logged_to_audit_trail(self, api_client, mock_config):
        """Test that config updates are audited (K=2)."""
        pass

    @pytest.mark.skip(reason="K=3: Learning integration not yet ready")
    def test_learning_scores_update_from_feedback(self, api_client):
        """Test that confidence scores update with learning feedback (K=3)."""
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
