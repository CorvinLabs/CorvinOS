"""E2E: Feature 1 — Console Panels → Live APIs (Phase 2).

The panels (licensing audit, monitoring metrics, model selection) read the
``features_phase2`` router through the real ``/v1/console`` mount. Since
6ee192386 (ADR-0856) those routes are session-authenticated, return only
measured data (``available`` says whether a source answered) and the old
``POST /models/config`` — which answered ``success: True`` and persisted
nothing — is an honest 501 naming the real writer (``/settings/engine``).

The request goes over the real ASGI transport via the shared ``client`` /
``async_client`` / ``anon_client`` fixtures (tests/fixtures_console.py).
Schema checks of the three GET panels also live in test_vibe_dashboard_wiring.py;
this file covers the model-config pair and concurrency.
"""
from __future__ import annotations

import asyncio

import pytest

BASE = "/v1/console/v1"


class TestModelSelectionPanel:
    def test_panel_fetch_config_reads_the_tenant_pins(self, client):
        response = client.get(f"{BASE}/models/config")
        assert response.status_code == 200, response.text[:300]
        data = response.json()
        assert {"config", "available", "timestamp"} <= set(data)
        if data["available"]:
            assert data["config"]["tenant_id"] == "_default"
            assert {"default_engine", "os_model", "worker_model"} <= set(data["config"])

    def test_panel_save_config_is_refused_and_names_the_real_writer(self, client):
        response = client.post(
            f"{BASE}/models/config?default_model=claude-sonnet-5&cost_threshold=0.25"
        )
        assert response.status_code == 501
        assert "/settings/engine" in response.json()["detail"]

    def test_config_refuses_unauthenticated_caller(self, anon_client):
        assert anon_client.get(f"{BASE}/models/config").status_code == 401


class TestCrossPanelIntegration:
    ENDPOINTS = (
        f"{BASE}/licensing/audit-events",
        f"{BASE}/monitoring/metrics",
        f"{BASE}/models/available",
        f"{BASE}/models/config",
    )

    def test_all_endpoints_live(self, client):
        for endpoint in self.ENDPOINTS:
            response = client.get(endpoint)
            assert response.status_code == 200, f"{endpoint}: {response.text[:200]}"
            assert "available" in response.json(), endpoint

    @pytest.mark.asyncio
    async def test_concurrent_panel_requests(self, async_client):
        responses = await asyncio.gather(
            *(async_client.get(endpoint) for endpoint in self.ENDPOINTS[:3])
        )
        assert [r.status_code for r in responses] == [200, 200, 200]
