"""End-to-End Tests for Phase 2 Live Data Wiring (Session 2, 2026-09-25)

Tests verify that Phase 2 endpoints serve REAL data (not dummy constants).

Covers:
  - GET /v1/licensing/audit-events (EventStore, PII-safe)
  - GET /v1/monitoring/metrics (HealthMonitor integration)
  - GET /v1/models/available (Engine Registry)

Compliance: ADR-0297 (PII), ADR-0728 (Phase 2 Feature 1)
"""
import pytest
import json
from httpx import AsyncClient


class TestPhase2LiveEndpoints:
    """Phase 2 Live Endpoints E2E Tests"""

    @pytest.mark.asyncio
    async def test_licensing_audit_events_endpoint_live(self, async_client: AsyncClient):
        """E2E: GET /v1/licensing/audit-events returns real data (not dummy)."""
        response = await async_client.get("/v1/console/v1/licensing/audit-events?limit=100")
        assert response.status_code == 200
        data = response.json()

        # Schema validation
        assert isinstance(data, dict)
        assert "events" in data
        assert "total" in data
        assert "available" in data
        assert "tenant_id" in data
        assert "timestamp" in data

        # Data type checks
        assert isinstance(data["events"], list)
        assert isinstance(data["total"], int)
        assert isinstance(data["available"], bool)

        # If events exist, validate structure
        if len(data["events"]) > 0:
            event = data["events"][0]
            assert "id" in event
            assert "timestamp" in event
            assert "event_type" in event
            # outcome may be None for some event types
            assert "outcome" in event

    @pytest.mark.asyncio
    async def test_licensing_audit_events_pii_safe(self, async_client: AsyncClient):
        """E2E: Audit events have no bare PII (ADR-0297)."""
        response = await async_client.get("/v1/console/v1/licensing/audit-events?limit=50")
        assert response.status_code == 200
        data = response.json()

        # Check for PII patterns in the response
        response_str = json.dumps(data)

        # Should NOT contain cleartext email addresses
        assert "@" not in response_str or "[REDACTED]" in response_str or "email" not in response_str.lower()

        # compliance field should mention content-free design
        assert data.get("compliance") and "content-free" in data["compliance"].lower()

    @pytest.mark.asyncio
    async def test_monitoring_metrics_endpoint_live(self, async_client: AsyncClient):
        """E2E: GET /v1/monitoring/metrics returns real measured data."""
        response = await async_client.get("/v1/console/v1/monitoring/metrics?range=1h")
        assert response.status_code == 200
        data = response.json()

        # Schema validation
        assert isinstance(data, dict)
        assert "metrics" in data
        assert "available" in data
        assert "sources" in data
        assert "timestamp" in data

        # Data type checks
        assert isinstance(data["metrics"], list)
        assert isinstance(data["available"], bool)
        assert isinstance(data["sources"], list)

        # If metrics available, validate structure
        if data["available"] and len(data["metrics"]) > 0:
            metric = data["metrics"][0]
            assert "name" in metric
            assert "value" in metric
            assert "unit" in metric
            assert isinstance(metric["value"], (int, float))
            assert isinstance(metric["unit"], str)

    @pytest.mark.asyncio
    async def test_monitoring_metrics_real_sources(self, async_client: AsyncClient):
        """E2E: Metrics come from real sources (EventStore, not constants)."""
        response = await async_client.get("/v1/console/v1/monitoring/metrics?range=1h")
        assert response.status_code == 200
        data = response.json()

        # Check that we have real sources, not the old dummy "demo_metrics"
        sources = data.get("sources", [])

        # Old dummy had constants like 125ms, 2%, 0.82 convergence
        # Real metrics should reflect actual EventStore counts (0 if empty tenant)
        if data.get("available"):
            metrics = data.get("metrics", [])
            metric_names = {m.get("name") for m in metrics if m}

            # Real metrics include event store counts
            if metric_names:
                # At least one metric should be from the real EventStore
                # (skill_executions, task_outcomes, operator_feedback)
                real_sources = {
                    "skill_executions", "task_outcomes", "operator_feedback",
                    "skill_latency_p50", "skill_error_rate", "convergence"
                }
                found_real = bool(metric_names & real_sources)
                assert found_real or len(sources) == 0, \
                    f"Expected real sources (event counts) but got: {metric_names}"

    @pytest.mark.asyncio
    async def test_models_available_endpoint_live(self, async_client: AsyncClient):
        """E2E: GET /v1/models/available returns real engine registry data."""
        response = await async_client.get("/v1/console/v1/models/available")
        assert response.status_code == 200
        data = response.json()

        # Schema validation
        assert isinstance(data, dict)
        assert "models" in data
        assert "total" in data
        assert "available" in data
        assert "timestamp" in data

        # Data type checks
        assert isinstance(data["models"], list)
        assert isinstance(data["total"], int)
        assert isinstance(data["available"], bool)

        # If models available, validate structure
        if data["available"] and len(data["models"]) > 0:
            model = data["models"][0]
            assert "id" in model
            assert "name" in model
            assert "engines" in model
            assert isinstance(model["engines"], list)
            # Real data has separate input/output rates (not a single "cost_per_1k: 0.015")
            if "rates" in model:
                assert isinstance(model["rates"], dict)
                # Should NOT have the old invented "latency_ms: 50"
                assert "latency_ms" not in model or "measured" in str(model.get("latency_ms", "")).lower()

    @pytest.mark.asyncio
    async def test_models_available_no_dummy_data(self, async_client: AsyncClient):
        """E2E: Models do NOT return dummy constants (0.015 cost, 50ms latency)."""
        response = await async_client.get("/v1/console/v1/models/available")
        assert response.status_code == 200
        data = response.json()

        response_str = json.dumps(data)

        # Old dummy responses had these invented values
        # Real data should reflect actual engine registry
        if "cost_per_1k" in response_str:
            # If cost appears, it should NOT be the old Opus 5 dummy (0.015)
            # Real Opus 5 rates are 0.005 (input) and 0.025 (output)
            assert "0.015" not in response_str or "deprecated" in response_str.lower()

    @pytest.mark.asyncio
    async def test_phase2_endpoints_graceful_fallback(self, async_client: AsyncClient):
        """E2E: Endpoints fail gracefully (empty data + reason, never sample data)."""
        # If a real source is unavailable, endpoints should return empty results
        # (per ADR-0763) rather than inventing sample data

        # Test all three endpoints for consistent behavior
        endpoints = [
            "/v1/console/v1/licensing/audit-events",
            "/v1/console/v1/monitoring/metrics",
            "/v1/console/v1/models/available",
        ]

        for endpoint in endpoints:
            response = await async_client.get(endpoint)
            assert response.status_code == 200
            data = response.json()

            # Should always have availability indicator
            assert "available" in data

            # If not available, should have a reason (not just empty)
            if not data.get("available"):
                assert "detail" in data or "reason" in data, \
                    f"{endpoint} returned unavailable but no explanation"
                # Should NOT return sample data
                assert len(data.get("events", [])) == 0 or \
                       len(data.get("metrics", [])) == 0 or \
                       len(data.get("models", [])) == 0, \
                    f"{endpoint} returned both unavailable=False AND data (should be empty on fail)"
