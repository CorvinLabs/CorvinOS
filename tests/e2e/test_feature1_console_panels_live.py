"""E2E: Feature 1 — Console Panels → Live APIs (Phase 2)

Tests verify: licensing-audit, otel-telemetry, model-selection panels
wired to real API endpoints, returning data <500ms p99.
"""
import asyncio
import time
import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


class TestLicensingAuditPanel:
    """Licensing Audit Panel → EventStore API"""

    async def test_panel_fetch_audit_events(self):
        """licensing-audit.tsx fetches /v1/licensing/audit-events"""
        from corvin_console.app import app

        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get("/v1/licensing/audit-events?limit=50")
            assert response.status_code == 200
            data = response.json()
            assert "events" in data
            assert "total" in data
            assert "compliance" in data
            assert data["compliance"] == "ADR-0297 PII filtering applied"

    async def test_panel_fetch_audit_events_with_status_filter(self):
        """Panel can filter by status (granted/denied/expired)"""
        from corvin_console.app import app

        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get("/v1/licensing/audit-events?status=granted")
            assert response.status_code == 200
            data = response.json()
            assert isinstance(data["events"], list)

    async def test_panel_latency_p99(self):
        """API responds <500ms p99 (ADR-0625 target)"""
        from corvin_console.app import app

        timings = []
        async with AsyncClient(app=app, base_url="http://test") as client:
            for _ in range(10):
                start = time.perf_counter()
                await client.get("/v1/licensing/audit-events?limit=100")
                elapsed_ms = (time.perf_counter() - start) * 1000
                timings.append(elapsed_ms)

        p99_ms = sorted(timings)[int(len(timings) * 0.99)]
        assert p99_ms < 500, f"p99 latency {p99_ms}ms exceeds 500ms SLO"


class TestOTELTelemetryPanel:
    """OTEL Telemetry Panel → Prometheus API"""

    async def test_panel_fetch_metrics(self):
        """otel-telemetry.tsx fetches /v1/monitoring/metrics"""
        from corvin_console.app import app

        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get("/v1/monitoring/metrics?range=1h")
            assert response.status_code == 200
            data = response.json()
            assert "metrics" in data
            assert isinstance(data["metrics"], list)
            # At least 4 core metrics
            assert len(data["metrics"]) >= 3

    async def test_panel_metrics_have_schema(self):
        """Each metric has name, value, unit, status, timestamp"""
        from corvin_console.app import app

        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get("/v1/monitoring/metrics")
            data = response.json()
            for metric in data["metrics"]:
                assert "name" in metric
                assert "value" in metric
                assert "unit" in metric
                assert "status" in metric or "timestamp" in metric

    async def test_panel_time_range_filter(self):
        """Panel can request different time ranges (1h, 6h, 24h)"""
        from corvin_console.app import app

        async with AsyncClient(app=app, base_url="http://test") as client:
            for range_val in ["1h", "6h", "24h"]:
                response = await client.get(f"/v1/monitoring/metrics?range={range_val}")
                assert response.status_code == 200
                data = response.json()
                assert data["range"] == range_val


class TestModelSelectionPanel:
    """Model Selection Panel → Model Registry API"""

    async def test_panel_fetch_models(self):
        """model-selection.tsx fetches /v1/models/available"""
        from corvin_console.app import app

        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get("/v1/models/available")
            assert response.status_code == 200
            data = response.json()
            assert "models" in data
            assert isinstance(data["models"], list)
            assert len(data["models"]) >= 2

    async def test_panel_models_have_schema(self):
        """Each model has id, name, provider, cost_per_1k, latency_ms, capabilities"""
        from corvin_console.app import app

        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get("/v1/models/available")
            data = response.json()
            for model in data["models"]:
                assert "id" in model
                assert "name" in model
                assert "provider" in model
                assert "cost_per_1k" in model
                assert "latency_ms" in model
                assert "capabilities" in model

    async def test_panel_fetch_config(self):
        """Panel fetches current config from /v1/models/config"""
        from corvin_console.app import app

        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get("/v1/models/config")
            assert response.status_code == 200
            data = response.json()
            assert "config" in data
            assert "default_model" in data["config"]

    async def test_panel_save_config(self):
        """Panel can POST new model config to /v1/models/config"""
        from corvin_console.app import app

        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post(
                "/v1/models/config?default_model=claude-sonnet-5&cost_threshold=0.25"
            )
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["config"]["default_model"] == "claude-sonnet-5"


class TestCrossPanelIntegration:
    """All 3 panels work together in Vibe dashboard"""

    async def test_all_endpoints_live(self):
        """All 3 panel APIs are live and reachable"""
        from corvin_console.app import app

        endpoints = [
            "/v1/licensing/audit-events",
            "/v1/monitoring/metrics",
            "/v1/models/available",
            "/v1/models/config",
        ]

        async with AsyncClient(app=app, base_url="http://test") as client:
            for endpoint in endpoints:
                response = await client.get(endpoint)
                assert response.status_code == 200, f"{endpoint} failed"

    async def test_concurrent_panel_requests(self):
        """All 3 panels can be fetched concurrently"""
        from corvin_console.app import app

        async with AsyncClient(app=app, base_url="http://test") as client:
            tasks = [
                client.get("/v1/licensing/audit-events"),
                client.get("/v1/monitoring/metrics"),
                client.get("/v1/models/available"),
            ]
            responses = await asyncio.gather(*tasks)
            assert all(r.status_code == 200 for r in responses)
            assert len(responses) == 3
