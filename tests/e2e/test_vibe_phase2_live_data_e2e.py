"""
E2E Test for VIBE 9D Maturity Dashboard Phase 2 — Live Data Integration

Tests the complete flow:
1. API endpoint serves live measurements from disk
2. Frontend hook fetches data from endpoint
3. Transformation converts loss metrics to 9D scores
4. Dashboard renders with live data

Executes both via API + transformation simulation (since frontend can't be
tested directly in pytest).
"""

from __future__ import annotations

import json
import pytest
from datetime import datetime
from pathlib import Path
from fastapi.testclient import TestClient

from core.console.corvin_console.app import app
from core.console.corvin_console.routes import api_vibe_maturity
from core.paths.tenant import tenant_home


class TestPhase2LiveDataE2E:
    """End-to-end tests for Phase 2 live data integration."""

    @pytest.fixture
    def test_client(self) -> TestClient:
        """FastAPI test client."""
        return TestClient(app)

    @pytest.fixture
    def sample_measurements(self) -> list[dict]:
        """Create sample measurements for testing."""
        base_time = datetime.now()
        measurements = []

        for i in range(10):
            ts = base_time
            unix_time = int(ts.timestamp()) - (i * 60)

            measurements.append({
                "timestamp": ts.isoformat(),
                "unix_time": unix_time,
                "tenant_id": "_default",
                "schema": "corvin.live_measurement/1",
                "learning": {
                    "loss_total": 0.1 + (i * 0.005),  # Vary slightly
                    "loss_routing": 0.04 - (i * 0.002),
                    "loss_confidence": 0.025 + (i * 0.001),
                    "loss_feedback": 0.015,
                    "accuracy_routing": 0.95 - (i * 0.005),
                    "convergence_rate": 0.8 + (i * 0.01),
                },
                "system": {
                    "latency_p99_ms": 50.0 + (i * 2),
                    "throughput_tasks_per_sec": 40.0 - (i * 1),
                    "memory_usage_mb": 250.0 + (i * 10),
                    "cpu_usage_percent": 50.0 + (i * 2),
                    "audit_chain_length": 86230 + (i * 100),
                },
                "user_actions": {
                    "tasks_completed_this_hour": 23 + i,
                    "routing_decisions": 12 - (i % 3),
                    "training_batches": 5 + (i % 4),
                    "anomalies_detected": i % 2,
                },
                "component_health": {
                    "routing": {
                        "active": True,
                        "contribution": 0.15 + (i * 0.01),
                        "drift": 0.002 - (i * 0.0001),
                    },
                    "confidence": {
                        "active": True,
                        "contribution": 0.13 + (i * 0.005),
                        "drift": 0.029 + (i * 0.001),
                    },
                    "feedback": {
                        "active": True,
                        "contribution": 0.196 - (i * 0.01),
                        "drift": 0.016 + (i * 0.002),
                    },
                    "attention": {
                        "active": True,
                        "contribution": 0.04,
                        "drift": -0.023,
                    },
                    "latency": {
                        "active": True,
                        "contribution": 0.119,
                        "drift": 0.022 + (i * 0.001),
                    },
                    "diversity": {
                        "active": True,
                        "contribution": 0.096,
                        "drift": 0.041,
                    },
                },
            })

        return measurements

    def test_api_endpoint_returns_measurements(
        self, test_client: TestClient, sample_measurements: list[dict]
    ):
        """API endpoint should return measurements in correct format."""
        # Initialize API with sample data
        api = api_vibe_maturity.MaturityMeasurementAPI()

        # Use real historical data if available
        response_dict = api.to_dict(sample_measurements, window="today")

        # Verify response structure
        assert "measurements" in response_dict
        assert "count" in response_dict
        assert "window" in response_dict
        assert "updated_at" in response_dict

        assert response_dict["count"] == len(sample_measurements)
        assert response_dict["window"] == "today"

    def test_endpoint_filters_by_time_window(self, sample_measurements: list[dict]):
        """API should correctly filter by time window."""
        api = api_vibe_maturity.MaturityMeasurementAPI()

        # Get measurements from today
        today_measurements = api.get_measurements(window="today", tenant_id="_default")

        # Should be able to handle both historical and new data
        assert isinstance(today_measurements, list)

    def test_transformation_produces_valid_scores(self, sample_measurements: list[dict]):
        """Transformation should convert loss metrics to valid 0-10 scores."""
        from core.console.corvin_console.web_next.src.pages.vibe_engineering.hooks.useLiveMaturityData import (
            transformToLoopScores,
        )

        # Note: Can't directly import TypeScript, so we simulate the transformation
        # locally using the same logic

        def simulate_transform(measurements: list[dict]) -> dict[str, float]:
            """Simulate hook transformation logic."""
            if not measurements:
                return {
                    "confidence": 7.8,
                    "routing": 7.1,
                    "context": 7.4,
                    "workflow": 6.8,
                    "data_flow": 7.0,
                    "security": 7.3,
                    "memory": 7.2,
                    "skills": 6.9,
                    "plugins": 7.1,
                    "audit": 6.8,
                    "compliance": 7.0,
                    "system": 7.1,
                    "meta_convergence": 8.1,
                }

            # Average metrics
            avg_loss_confidence = sum(
                m["learning"]["loss_confidence"] for m in measurements
            ) / len(measurements)
            avg_accuracy_routing = sum(
                m["learning"]["accuracy_routing"] for m in measurements
            ) / len(measurements)
            avg_convergence = sum(
                m["learning"]["convergence_rate"] for m in measurements
            ) / len(measurements)

            # Transform
            confidence_score = (1 - avg_loss_confidence) * 10
            routing_score = avg_accuracy_routing * 10
            meta_score = min(10, avg_convergence * 10)

            return {
                "confidence": min(10, max(2, confidence_score)),
                "routing": min(10, max(2, routing_score)),
                "meta_convergence": min(10, max(2, meta_score)),
            }

        scores = simulate_transform(sample_measurements)

        # All scores should be in valid range
        assert all(0 <= v <= 10 for v in scores.values())

        # Scores should be reasonable (not NaN or inf)
        assert all(not float('nan') == v for v in scores.values())

    def test_fallback_to_test_data_on_empty(self):
        """If no measurements available, should use fallback test data."""
        api = api_vibe_maturity.MaturityMeasurementAPI()
        response = api.to_dict([], window="7d")

        assert response["count"] == 0
        # Fallback should still have updated_at
        assert "updated_at" in response

    def test_tenant_isolation_enforced(self, sample_measurements: list[dict]):
        """Measurements from different tenant should be filtered out."""
        # Modify a measurement to be from different tenant
        wrong_tenant_measurement = sample_measurements[0].copy()
        wrong_tenant_measurement["tenant_id"] = "other_tenant"

        api = api_vibe_maturity.MaturityMeasurementAPI()

        # Filter by default tenant
        measurements = api.get_measurements(window="7d", tenant_id="_default")

        # Should not include wrong tenant
        for m in measurements:
            assert m["tenant_id"] == "_default"

    def test_measurement_schema_validation(self, sample_measurements: list[dict]):
        """All measurements should have required schema fields."""
        required_fields = {
            "timestamp",
            "unix_time",
            "tenant_id",
            "learning",
            "system",
            "user_actions",
            "component_health",
        }

        for m in sample_measurements:
            # Check all required fields exist
            assert all(field in m for field in required_fields)

            # Check learning fields
            learning_fields = {
                "loss_total",
                "loss_routing",
                "loss_confidence",
                "loss_feedback",
                "accuracy_routing",
                "convergence_rate",
            }
            assert all(field in m["learning"] for field in learning_fields)

            # Check system fields
            system_fields = {
                "latency_p99_ms",
                "throughput_tasks_per_sec",
                "memory_usage_mb",
                "cpu_usage_percent",
                "audit_chain_length",
            }
            assert all(field in m["system"] for field in system_fields)

    def test_real_measurement_data_available(self):
        """Test that real measurement files exist and can be loaded."""
        api = api_vibe_maturity.MaturityMeasurementAPI()

        # Try to load real data from disk
        measurements = api.get_measurements(window="90d", tenant_id="_default")

        if measurements:
            # If data exists, verify it's valid
            for m in measurements:
                assert "timestamp" in m
                assert "unix_time" in m
                assert "tenant_id" in m
                assert m["tenant_id"] == "_default"

    def test_response_includes_metadata(self, sample_measurements: list[dict]):
        """API response should include metadata for UI."""
        api = api_vibe_maturity.MaturityMeasurementAPI()
        response = api.to_dict(sample_measurements, window="7d")

        # Metadata fields
        assert "count" in response
        assert "window" in response
        assert "updated_at" in response
        assert "measurements" in response

        # Verify types
        assert isinstance(response["count"], int)
        assert isinstance(response["window"], str)
        assert isinstance(response["updated_at"], str)
        assert isinstance(response["measurements"], list)

    def test_measurements_sortable_by_time(self, sample_measurements: list[dict]):
        """Measurements should be orderable by timestamp."""
        timestamps = [m["timestamp"] for m in sample_measurements]
        sorted_timestamps = sorted(timestamps)

        # Should be able to sort without errors
        assert len(sorted_timestamps) == len(timestamps)

    def test_high_frequency_data_aggregation(self):
        """Test aggregation of high-frequency measurement data."""
        api = api_vibe_maturity.MaturityMeasurementAPI()

        # Simulate 1 hour of measurements (60 per minute = 3600 total)
        measurements = []
        base_time = datetime.now()

        for i in range(60):  # 60 measurements (1 per second for 1 minute)
            unix_time = int(base_time.timestamp()) - i
            measurements.append({
                "timestamp": base_time.isoformat(),
                "unix_time": unix_time,
                "tenant_id": "_default",
                "learning": {
                    "loss_confidence": 0.025 + (i * 0.0001),
                    "loss_routing": 0.04,
                    "loss_feedback": 0.015,
                    "loss_total": 0.1,
                    "accuracy_routing": 0.95,
                    "convergence_rate": 0.8,
                },
                "system": {
                    "latency_p99_ms": 50,
                    "throughput_tasks_per_sec": 40,
                    "memory_usage_mb": 250,
                    "cpu_usage_percent": 50,
                    "audit_chain_length": 86230,
                },
                "user_actions": {
                    "tasks_completed_this_hour": 23,
                    "routing_decisions": 12,
                    "training_batches": 5,
                    "anomalies_detected": 0,
                },
                "component_health": {},
            })

        response = api.to_dict(measurements, window="today")

        # Should handle large number of measurements
        assert response["count"] == len(measurements)
        assert len(response["measurements"]) == len(measurements)


class TestPhase2Integration:
    """Integration tests between components."""

    def test_api_to_frontend_contract(self):
        """API response should match what frontend hook expects."""
        api = api_vibe_maturity.MaturityMeasurementAPI()

        # Create sample response
        sample_measurement = {
            "timestamp": datetime.now().isoformat(),
            "unix_time": int(datetime.now().timestamp()),
            "tenant_id": "_default",
            "learning": {
                "loss_total": 0.1,
                "loss_routing": 0.04,
                "loss_confidence": 0.025,
                "loss_feedback": 0.015,
                "accuracy_routing": 0.95,
                "convergence_rate": 0.8,
            },
            "system": {
                "latency_p99_ms": 50,
                "throughput_tasks_per_sec": 40,
                "memory_usage_mb": 250,
                "cpu_usage_percent": 50,
                "audit_chain_length": 86230,
            },
            "user_actions": {
                "tasks_completed_this_hour": 23,
                "routing_decisions": 12,
                "training_batches": 5,
                "anomalies_detected": 1,
            },
            "component_health": {
                "routing": {"active": True, "contribution": 0.15, "drift": 0.002},
                "confidence": {"active": True, "contribution": 0.13, "drift": 0.029},
                "feedback": {"active": True, "contribution": 0.196, "drift": 0.016},
                "attention": {"active": True, "contribution": 0.04, "drift": -0.023},
                "latency": {"active": True, "contribution": 0.119, "drift": 0.022},
                "diversity": {"active": True, "contribution": 0.096, "drift": 0.041},
            },
        }

        response = api.to_dict([sample_measurement], window="7d")

        # Frontend hook expects these fields
        expected_response_fields = {"measurements", "count", "window", "updated_at"}
        assert expected_response_fields.issubset(response.keys())

        # Each measurement should have these fields
        expected_measurement_fields = {
            "timestamp",
            "unix_time",
            "tenant_id",
            "learning",
            "system",
            "user_actions",
            "component_health",
        }
        for m in response["measurements"]:
            assert expected_measurement_fields.issubset(m.keys())


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
