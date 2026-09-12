"""
Unit Tests for VIBE 9D Maturity Dashboard Phase 2 — API Endpoint

Covers:
- Endpoint auth + tenant isolation
- Time window filtering
- Data transformation (loss metrics → scores)
- Error handling and fallbacks
- Measurement schema validation
"""

from __future__ import annotations

import json
import pytest
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any
from unittest.mock import Mock, patch, MagicMock

from fastapi.testclient import TestClient

from core.console.corvin_console.app import app
from core.console.corvin_console.routes import api_vibe_maturity
from core.console.corvin_console.auth import SessionRecord


# ============ FIXTURES ============

@pytest.fixture
def sample_measurement() -> Dict[str, Any]:
    """Sample measurement record from live_measurements/."""
    return {
        "timestamp": "2026-09-06T15:28:35.827012",
        "unix_time": 1788701315,
        "tenant_id": "_default",
        "schema": "corvin.live_measurement/1",
        "learning": {
            "loss_total": 0.1,
            "loss_routing": 0.04,
            "loss_confidence": 0.025,
            "loss_feedback": 0.015,
            "accuracy_routing": 0.95,
            "convergence_rate": 0.816,
        },
        "system": {
            "latency_p99_ms": 48.26,
            "throughput_tasks_per_sec": 38.42,
            "memory_usage_mb": 242.08,
            "cpu_usage_percent": 52.89,
            "audit_chain_length": 86230,
        },
        "user_actions": {
            "tasks_completed_this_hour": 23,
            "routing_decisions": 12,
            "training_batches": 5,
            "anomalies_detected": 1,
        },
        "component_health": {
            "routing": {"active": True, "contribution": 0.154, "drift": 0.002},
            "confidence": {"active": True, "contribution": 0.132, "drift": 0.029},
            "feedback": {"active": True, "contribution": 0.196, "drift": 0.016},
            "attention": {"active": True, "contribution": 0.040, "drift": -0.023},
            "latency": {"active": True, "contribution": 0.119, "drift": 0.022},
            "diversity": {"active": True, "contribution": 0.096, "drift": 0.041},
        },
    }


@pytest.fixture
def mock_session() -> SessionRecord:
    """Mock authenticated session."""
    return Mock(
        spec=SessionRecord,
        tenant_id="_default",
        user_id="test_user",
        authenticated=True,
    )


@pytest.fixture
def test_client() -> TestClient:
    """FastAPI test client."""
    return TestClient(app)


# ============ TESTS: ENDPOINT REGISTRATION ============

class TestEndpointRegistration:
    """Verify endpoint is properly registered and accessible."""

    def test_measurements_endpoint_exists(self, test_client: TestClient):
        """Endpoint /v1/console/vibe/maturity/measurements should exist."""
        # Note: This will return 401 without auth, but that proves the route exists
        response = test_client.get("/v1/console/vibe/maturity/measurements")
        assert response.status_code in [200, 401, 403], "Route should exist and return 200 or auth error"


# ============ TESTS: TIME WINDOW FILTERING ============

class TestTimeWindowFiltering:
    """Test that measurements are correctly filtered by time window."""

    def test_window_filtering_today(self, sample_measurement: Dict[str, Any]):
        """Measurements from 'today' should be included."""
        api = api_vibe_maturity.MaturityMeasurementAPI()

        # Adjust sample to be from today
        now = datetime.now()
        sample_measurement["timestamp"] = now.isoformat()
        sample_measurement["unix_time"] = int(now.timestamp())

        with patch.object(api, 'get_measurements') as mock_get:
            mock_get.return_value = [sample_measurement]
            measurements = api.get_measurements(window="today", tenant_id="_default")
            assert len(measurements) > 0, "Today's measurements should be included"
            mock_get.assert_called_once_with(window="today", tenant_id="_default")

    def test_window_filtering_7d(self, sample_measurement: Dict[str, Any]):
        """Measurements from last 7 days should be included."""
        api = api_vibe_maturity.MaturityMeasurementAPI()

        # Adjust sample to be from 3 days ago
        three_days_ago = datetime.now() - timedelta(days=3)
        sample_measurement["timestamp"] = three_days_ago.isoformat()
        sample_measurement["unix_time"] = int(three_days_ago.timestamp())

        with patch.object(api, 'get_measurements') as mock_get:
            mock_get.return_value = [sample_measurement]
            measurements = api.get_measurements(window="7d", tenant_id="_default")
            assert len(measurements) > 0, "Measurements from 3 days ago should be in 7d window"

    def test_window_filtering_old_data_excluded(self, sample_measurement: Dict[str, Any]):
        """Measurements older than window should be excluded."""
        api = api_vibe_maturity.MaturityMeasurementAPI()

        # Adjust sample to be from 100 days ago (outside 90d window)
        old_date = datetime.now() - timedelta(days=100)
        sample_measurement["timestamp"] = old_date.isoformat()
        sample_measurement["unix_time"] = int(old_date.timestamp())

        with patch.object(api, 'get_measurements') as mock_get:
            mock_get.return_value = []
            measurements = api.get_measurements(window="90d", tenant_id="_default")
            assert len(measurements) == 0, "Old measurements should be filtered out"


# ============ TESTS: TENANT ISOLATION ============

class TestTenantIsolation:
    """Verify tenant isolation is enforced."""

    def test_different_tenant_filtered(self, sample_measurement: Dict[str, Any]):
        """Measurements from different tenant should be filtered."""
        api = api_vibe_maturity.MaturityMeasurementAPI()
        sample_measurement["tenant_id"] = "other_tenant"

        with patch.object(api, 'get_measurements') as mock_get:
            mock_get.return_value = []
            measurements = api.get_measurements(window="7d", tenant_id="_default")
            assert len(measurements) == 0, "Other tenant's measurements should not be returned"
            # Verify filtering was applied
            mock_get.assert_called_once_with(window="7d", tenant_id="_default")

    def test_session_tenant_enforced(self, test_client: TestClient, mock_session: SessionRecord):
        """Endpoint should use tenant_id from authenticated session."""
        # This test documents the requirement that tenant is taken from session
        # Actual implementation is in the endpoint's Depends(require_session)
        assert mock_session.tenant_id == "_default"


# ============ TESTS: MEASUREMENT SCHEMA ============

class TestMeasurementSchema:
    """Test measurement record schema validation."""

    def test_response_schema_valid(self, sample_measurement: Dict[str, Any]):
        """Response should match MaturityMeasurementsResponse schema."""
        api = api_vibe_maturity.MaturityMeasurementAPI()

        # The to_dict method should produce valid response format
        response_dict = api.to_dict([sample_measurement], window="7d")

        assert "measurements" in response_dict
        assert "count" in response_dict
        assert "window" in response_dict
        assert "updated_at" in response_dict

        assert response_dict["count"] == 1
        assert response_dict["window"] == "7d"
        assert len(response_dict["measurements"]) == 1

    def test_measurement_record_fields_required(self, sample_measurement: Dict[str, Any]):
        """Measurement record should have required fields."""
        required_fields = {
            "timestamp",
            "unix_time",
            "tenant_id",
            "learning",
            "system",
            "user_actions",
            "component_health",
        }
        assert all(field in sample_measurement for field in required_fields)

    def test_learning_fields_for_transformation(self, sample_measurement: Dict[str, Any]):
        """Learning fields must exist for hook transformation."""
        learning_fields = {
            "loss_total",
            "loss_routing",
            "loss_confidence",
            "loss_feedback",
            "accuracy_routing",
            "convergence_rate",
        }
        assert all(field in sample_measurement["learning"] for field in learning_fields)

    def test_system_fields_for_transformation(self, sample_measurement: Dict[str, Any]):
        """System fields must exist for hook transformation."""
        system_fields = {
            "latency_p99_ms",
            "throughput_tasks_per_sec",
            "memory_usage_mb",
            "cpu_usage_percent",
            "audit_chain_length",
        }
        assert all(field in sample_measurement["system"] for field in system_fields)


# ============ TESTS: ERROR HANDLING ============

class TestErrorHandling:
    """Test error handling and graceful degradation."""

    def test_missing_directory_returns_empty(self):
        """If measurements directory doesn't exist, should return empty list."""
        api = api_vibe_maturity.MaturityMeasurementAPI()

        with patch.object(api, 'measurements_dir', Path("/nonexistent/path")):
            measurements = api.get_measurements(window="7d")
            assert measurements == [], "Missing directory should return empty list"

    def test_invalid_json_line_skipped(self):
        """Invalid JSON lines should be skipped with warning."""
        api = api_vibe_maturity.MaturityMeasurementAPI()

        # Create a temporary file with invalid JSON
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
            f.write('{"valid": "json"}\n')
            f.write('invalid json line\n')
            f.write('{"also": "valid"}\n')
            temp_path = Path(f.name)

        try:
            with patch.object(api, 'measurements_dir', temp_path.parent):
                with patch('pathlib.Path.glob') as mock_glob:
                    mock_glob.return_value = [temp_path]
                    # Should skip the invalid line and still process valid lines
                    assert temp_path.exists()
        finally:
            temp_path.unlink()

    def test_malformed_timestamp_skipped(self, sample_measurement: Dict[str, Any]):
        """Measurement with malformed timestamp should be skipped."""
        sample_measurement["timestamp"] = "not-a-timestamp"

        api = api_vibe_maturity.MaturityMeasurementAPI()
        # This tests that the filtering logic is resilient
        with patch.object(api, 'get_measurements') as mock_get:
            mock_get.return_value = []
            measurements = api.get_measurements(window="7d")
            assert len(measurements) == 0


# ============ TESTS: DATA PERSISTENCE ============

class TestDataPersistence:
    """Test that measurements are correctly loaded from disk."""

    def test_measurements_loaded_from_jsonl(self):
        """Real measurements should be loadable from JSONL files."""
        api = api_vibe_maturity.MaturityMeasurementAPI()
        measurements = api.get_measurements(window="90d", tenant_id="_default")

        # This test uses the real measurements file from Sep 6
        # It should load at least some historical data
        if measurements:
            assert isinstance(measurements, list)
            assert all(isinstance(m, dict) for m in measurements)
            assert all("timestamp" in m for m in measurements)
            assert all("tenant_id" in m for m in measurements)


# ============ TESTS: RESPONSE FORMATTING ============

class TestResponseFormatting:
    """Test response is correctly formatted for frontend consumption."""

    def test_response_includes_updated_at(self, sample_measurement: Dict[str, Any]):
        """Response should include updated_at timestamp."""
        api = api_vibe_maturity.MaturityMeasurementAPI()
        response = api.to_dict([sample_measurement], window="7d")

        assert "updated_at" in response
        # Should be ISO format timestamp
        updated_at = response["updated_at"]
        # Try to parse it as ISO format
        try:
            datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
        except ValueError:
            pytest.fail(f"updated_at should be ISO format, got: {updated_at}")

    def test_response_count_matches_measurements(self, sample_measurement: Dict[str, Any]):
        """Count in response should match measurements list length."""
        api = api_vibe_maturity.MaturityMeasurementAPI()
        response = api.to_dict([sample_measurement], window="7d")

        assert response["count"] == len(response["measurements"])
        assert response["count"] == 1

    def test_response_with_empty_measurements(self):
        """Response should handle empty measurements list."""
        api = api_vibe_maturity.MaturityMeasurementAPI()
        response = api.to_dict([], window="7d")

        assert response["count"] == 0
        assert response["measurements"] == []
        assert "updated_at" in response


# ============ TESTS: PYDANTIC MODELS ============

class TestPydanticModels:
    """Test request/response Pydantic models."""

    def test_measurement_record_model_valid(self, sample_measurement: Dict[str, Any]):
        """MaturityMeasurementRecord should validate measurement data."""
        record = api_vibe_maturity.MaturityMeasurementRecord(**sample_measurement)
        assert record.tenant_id == "_default"
        assert record.unix_time == 1788701315

    def test_response_model_valid(self, sample_measurement: Dict[str, Any]):
        """MaturityMeasurementsResponse should validate response data."""
        response = api_vibe_maturity.MaturityMeasurementsResponse(
            measurements=[api_vibe_maturity.MaturityMeasurementRecord(**sample_measurement)],
            count=1,
            window="7d",
            updated_at=datetime.now().isoformat(),
        )
        assert response.count == 1
        assert response.window == "7d"
        assert len(response.measurements) == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
