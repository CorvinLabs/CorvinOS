"""Vibe Dashboard wiring — the tabs it renders and the endpoints they read.

Rewritten 2026-09-28 (adversarial review round 7). The previous suite
accepted ``200/401/404`` for every endpoint (so a missing route passed),
guarded every schema check behind ``if status == 200`` against an
unauthenticated app (so no schema was ever checked), and asserted that the
``LicensingAuditTab`` / ``ModelSelectionTab`` components exist — both were
retired on purpose (ADR-0908: the audit view lives in the Compliance panel,
the models view in /app/models). A ``TestLearningLoopIntegrationTab`` probed
``/v1/console/v1/learning/events``, a route that does not exist, and passed on
the 404.

What this file proves now:
  * the endpoints the dashboard's tabs fetch answer 200 with their schema
    through the real ``/v1/console`` mount (authenticated fixture);
  * the same endpoints refuse an unauthenticated caller;
  * the dashboard source renders exactly the current tab set, the retired
    tab components are gone, and ``?tab=models`` redirects to the Models panel.

ADR-0728: Live Data Wiring · ADR-0908: retired tabs
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
VIBE = REPO / "core/console/corvin_console/web-next/src/pages/vibe-engineering"

# Endpoints the dashboard's tabs (and the panels its retired tabs moved to) read.
ENDPOINTS = {
    "/v1/console/v1/monitoring/metrics?range=1h": {"metrics", "available", "sources", "timestamp"},
    "/v1/console/v1/licensing/audit-events?limit=10": {"events", "total", "available", "tenant_id"},
    "/v1/console/v1/models/available": {"models", "total", "available", "timestamp"},
}


class TestDashboardEndpoints:
    @pytest.mark.parametrize("url,keys", list(ENDPOINTS.items()))
    def test_endpoint_answers_with_schema(self, client, url, keys):
        response = client.get(url)
        assert response.status_code == 200, response.text[:300]
        data = response.json()
        assert keys <= set(data), f"{url} missing {keys - set(data)}"

    @pytest.mark.parametrize("url", list(ENDPOINTS))
    def test_endpoint_refuses_unauthenticated_caller(self, anon_client, url):
        assert anon_client.get(url).status_code == 401

    def test_audit_filter_parameters_do_not_500(self, client):
        response = client.get("/v1/console/v1/licensing/audit-events?limit=50&status=denied")
        assert response.status_code == 200

    def test_audit_events_carry_no_raw_email(self, client):
        data = client.get("/v1/console/v1/licensing/audit-events?limit=50").json()
        for event in data["events"]:
            for key in ("user_id", "user_id_redacted"):
                value = event.get(key) or ""
                assert not ("@" in value and "." in value.split("@")[-1]), event


class TestDashboardSource:
    def _source(self) -> str:
        return (VIBE / "VibeDashboard.tsx").read_text(encoding="utf-8")

    def test_current_tabs_are_imported_and_rendered(self):
        src = self._source()
        for component, tab in (
            ("MaturityDashboard", "maturity"),
            ("LearningLoopsTab", "loops"),
            ("MonitoringTab", "metrics"),
        ):
            assert f"import {{ {component} }}" in src, component
            assert f"activeTab === '{tab}' && <{component} />" in src, tab

    def test_retired_tabs_stay_retired(self):
        assert not (VIBE / "tabs/LicensingAuditTab.tsx").exists()
        assert not (VIBE / "tabs/ModelSelectionTab.tsx").exists()
        src = self._source()
        assert "LicensingAuditTab" not in src
        assert "ModelSelectionTab" not in src

    def test_models_tab_redirects_to_models_panel(self):
        src = self._source()
        assert "raw === 'models'" in src
        assert '<Navigate to="/app/models?tab=catalog" replace />' in src

    def test_monitoring_tab_reads_the_tested_endpoint(self):
        tab = (VIBE / "tabs/MonitoringTab.tsx").read_text(encoding="utf-8")
        assert "/v1/console/v1/monitoring/metrics?range=1h" in tab
