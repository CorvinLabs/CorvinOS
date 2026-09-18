"""
E2E tests for Model Selection Learning Dashboard — ADR-0377 Phase 2b

Tests:
1. Dashboard loads without error
2. Shows live threshold status
3. Manual override works (slider → API → threshold updated → dashboard reflects)
4. Reset works (clears store, restarts learning)
5. Export/import round-trip (export → modify externally → import)
6. Audit trail captures all operator actions
7. API endpoints return correct data
"""

from __future__ import annotations

import pytest
from pathlib import Path
from fastapi.testclient import TestClient

from core.learning.learned_threshold_store import (
    get_store,
    reset_store,
    StoredThreshold,
)


@pytest.fixture(autouse=True)
def _isolated_corvin_home(tmp_path, monkeypatch):
    """Never touch the operator's real store or chain (ADR-0885 step 3).

    These tests reset and override the learned-threshold store; until the auth
    fixture was fixed they answered 401 before reaching it, and the first run
    that got through mutated the REAL tenant store. Both the store and every
    chain reader resolve through CORVIN_HOME."""
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    reset_store("_default")
    yield
    reset_store("_default")


@pytest.fixture
def test_client():
    """FastAPI test client."""
    from core.console.corvin_console.app import router as console_router
    from fastapi import FastAPI

    # Mirror the real deployment: corvin_gateway/app.py mounts the console
    # router under /v1/console. Without this prefix the test hits paths that
    # only exist in this standalone app, never in production (ADR-0377 P2b
    # double-prefix bug, 2026-09-12 — the router's own prefix used to bake in
    # "/v1/console" too, which produced /v1/console/v1/console/... at the real
    # gateway mount and 404'd the browser).
    app = FastAPI()
    app.include_router(console_router, prefix="/v1/console")

    # ADR-0885 step 3: require_session validates a COOKIE, so the old
    # X-Session-ID headers never authenticated anything and 13 of these tests
    # answered 401. Override the two auth dependencies with a fake owner
    # record (the pattern core/console/tests/test_model_ranking_route.py uses).
    import dataclasses
    from core.console.corvin_console import auth as session_auth
    from core.console.corvin_console import deps as console_deps

    def _fake_record(tenant_id: str = "_default"):
        now = 1_000_000.0
        values = {}
        for f in dataclasses.fields(session_auth.SessionRecord):
            if f.default is not dataclasses.MISSING:
                continue
            ann = str(f.type)
            if "float" in ann:
                values[f.name] = now + (3600 if f.name == "expires_at" else 0)
            elif "bool" in ann:
                values[f.name] = False
            elif f.name == "tier":
                tier = getattr(session_auth, "Tier", None)
                values[f.name] = next(iter(tier)) if tier else "owner"
            elif f.name == "tenant_id":
                values[f.name] = tenant_id
            else:
                values[f.name] = f"test-{f.name}"
        return session_auth.SessionRecord(**values)

    app.dependency_overrides[console_deps.require_session] = lambda: _fake_record()
    app.dependency_overrides[console_deps.require_csrf] = lambda: _fake_record()

    return TestClient(app)


@pytest.fixture
def auth_headers():
    """Mock auth headers."""
    return {
        "X-Session-ID": "test_session",
        "X-Tenant-ID": "_default",
    }


@pytest.fixture
def store():
    """Fresh store for each test."""
    reset_store("_default")
    return get_store("_default")


class TestAPIEndpoints:
    """Test API endpoints."""

    def test_get_status_endpoint(self, test_client, auth_headers, store):
        """GET /v1/console/learning/model-cost-optimizer/status works."""
        response = test_client.get(
            "/v1/console/learning/model-cost-optimizer/status",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()

        assert "converged_count" in data
        assert "total_count" in data
        assert "thresholds" in data
        assert "cost_savings_percent" in data
        assert "cost_baseline_usd" in data
        assert "cost_current_usd" in data
        assert "accuracy_percent" in data
        assert "last_updated" in data

    def test_status_with_data(self, test_client, auth_headers, store):
        """Status endpoint returns correct data when thresholds exist."""
        st = StoredThreshold(
            task_type="code_gen",
            subsystem="analyzer",
            tenant_id="_default",
            learned_threshold=0.42,
            sample_count=25,
            converged=True,
        )
        store.set_threshold(st)

        response = test_client.get(
            "/v1/console/learning/model-cost-optimizer/status",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()

        assert data["converged_count"] == 1
        assert data["total_count"] == 1
        assert len(data["thresholds"]) == 1
        assert data["thresholds"][0]["learned_threshold"] == 0.42
        assert data["thresholds"][0]["converged"] is True

    def test_export_endpoint(self, test_client, auth_headers, store):
        """GET /v1/console/learning/model-cost-optimizer/export returns JSON."""
        st = StoredThreshold(
            task_type="code_gen",
            subsystem="analyzer",
            tenant_id="_default",
            learned_threshold=0.42,
            sample_count=25,
            converged=True,
        )
        store.set_threshold(st)

        response = test_client.get(
            "/v1/console/learning/model-cost-optimizer/export",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()

        assert data["version"] == "1"
        assert data["tenant_id"] == "_default"
        assert len(data["thresholds"]) == 1

    def test_reset_endpoint(self, test_client, auth_headers, store):
        """POST /v1/console/learning/model-cost-optimizer/reset clears thresholds."""
        st = StoredThreshold(
            task_type="code_gen",
            subsystem="analyzer",
            tenant_id="_default",
            learned_threshold=0.42,
            sample_count=25,
            converged=True,
        )
        store.set_threshold(st)

        assert len(store.get_all()) == 1

        response = test_client.post(
            "/v1/console/learning/model-cost-optimizer/reset",
            json={"reason": "Test reset"},
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()

        assert data["status"] == "ok"
        assert len(store.get_all()) == 0


class TestDashboardPanel:
    """The panel is registered — read from the registry SOURCE (a TSX file is
    not a Python module; the previous version of this class imported it as one
    and could never pass). ADR-0885: the cost panel is the Usage & Cost tab of
    the Models console, and /app/model-cost-optimizer redirects there."""

    _WEB = Path(__file__).resolve().parents[2] / "core/console/corvin_console/web-next/src"

    def test_models_panel_in_registry(self):
        registry = (self._WEB / "panels/registry.tsx").read_text(encoding="utf-8")
        assert 'rc("models", "Models", ModelsPage' in registry
        assert 'rc("model-cost-optimizer"' not in registry

    def test_old_route_redirects_to_the_usage_cost_tab(self):
        app_tsx = (self._WEB / "App.tsx").read_text(encoding="utf-8")
        assert 'path="model-cost-optimizer"' in app_tsx
        assert 'to="/app/models?tab=usage-cost"' in app_tsx

    def test_usage_cost_tab_exists(self):
        assert (self._WEB / "pages/models/tabs/usage-cost.tsx").exists()


class TestOperatorControls:
    """Test operator control workflows."""

    def test_manual_override_workflow(self, test_client, auth_headers, store):
        """Operator can manually override a threshold."""
        st = StoredThreshold(
            task_type="code_gen",
            subsystem="analyzer",
            tenant_id="_default",
            learned_threshold=0.42,
            sample_count=25,
            converged=True,
        )
        store.set_threshold(st)

        # Override threshold
        override_response = test_client.post(
            "/v1/console/learning/model-cost-optimizer/override",
            json={
                "task_type": "code_gen",
                "new_threshold": 0.55,
                "reason": "Test override",
            },
            headers=auth_headers,
        )

        assert override_response.status_code == 200
        assert override_response.json()["status"] == "ok"

        # Verify new threshold is used
        status_response = test_client.get(
            "/v1/console/learning/model-cost-optimizer/status",
            headers=auth_headers,
        )
        data = status_response.json()
        assert data["thresholds"][0]["learned_threshold"] == 0.55

    def test_export_import_roundtrip(self, test_client, auth_headers, store):
        """Export and import thresholds roundtrip works."""
        st = StoredThreshold(
            task_type="code_gen",
            subsystem="analyzer",
            tenant_id="_default",
            learned_threshold=0.42,
            sample_count=25,
            converged=True,
        )
        store.set_threshold(st)

        # Export
        export_response = test_client.get(
            "/v1/console/learning/model-cost-optimizer/export",
            headers=auth_headers,
        )
        export_data = export_response.json()

        # Reset
        store.reset_all()
        assert len(store.get_all()) == 0

        # Import
        import_response = test_client.post(
            "/v1/console/learning/model-cost-optimizer/import",
            json=export_data,
            headers=auth_headers,
        )

        assert import_response.status_code == 200
        assert import_response.json()["imported_count"] == 1

        # Verify imported
        assert len(store.get_all()) == 1
        assert store.get_threshold("code_gen", "analyzer") == 0.42


class TestDataValidation:
    """Test input validation."""

    def test_override_invalid_threshold(self, test_client, auth_headers):
        """Override with invalid threshold is rejected."""
        response = test_client.post(
            "/v1/console/learning/model-cost-optimizer/override",
            json={
                "task_type": "code_gen",
                "new_threshold": 0.05,  # Too low
                "reason": "Test",
            },
            headers=auth_headers,
        )

        assert response.status_code == 400

    def test_override_high_invalid_threshold(self, test_client, auth_headers):
        """Override with high invalid threshold is rejected."""
        response = test_client.post(
            "/v1/console/learning/model-cost-optimizer/override",
            json={
                "task_type": "code_gen",
                "new_threshold": 0.95,  # Too high
                "reason": "Test",
            },
            headers=auth_headers,
        )

        assert response.status_code == 400

    def test_import_invalid_version(self, test_client, auth_headers):
        """Import with invalid version is rejected."""
        response = test_client.post(
            "/v1/console/learning/model-cost-optimizer/import",
            json={
                "version": "2",  # Invalid
                "tenant_id": "_default",
                "thresholds": [],
            },
            headers=auth_headers,
        )

        assert response.status_code == 400


class TestTenantIsolation:
    """Test tenant isolation in API endpoints."""

    def test_api_respects_tenant_id(self, test_client, auth_headers):
        """API respects tenant_id from auth headers."""
        # This is a placeholder — real implementation would test
        # that tenant_id from SessionRecord is used for filtering

        response = test_client.get(
            "/v1/console/learning/model-cost-optimizer/status",
            headers=auth_headers,
        )

        assert response.status_code == 200


class TestErrorHandling:
    """Test error handling in API."""

    def test_missing_session_is_rejected(self):
        """Without a session cookie the route answers 401 (the auth dependency is
        the real one here — no override)."""
        from core.console.corvin_console.app import router as console_router
        from fastapi import FastAPI
        app = FastAPI()
        app.include_router(console_router, prefix="/v1/console")
        response = TestClient(app).get("/v1/console/learning/model-cost-optimizer/status")
        assert response.status_code == 401

    def test_api_graceful_on_missing_store(self, test_client, auth_headers):
        """API handles missing store gracefully."""
        reset_store("_default")

        response = test_client.get(
            "/v1/console/learning/model-cost-optimizer/status",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total_count"] == 0


class TestAuditIntegration:
    """Test audit trail integration."""

    def test_threshold_change_audited(self, store):
        """Threshold changes are logged to audit trail."""
        # This is a placeholder for full audit integration testing
        # Real implementation would verify audit events are written

        st = StoredThreshold(
            task_type="code_gen",
            subsystem="analyzer",
            tenant_id="_default",
            learned_threshold=0.42,
            sample_count=25,
            converged=True,
        )

        # This should trigger an audit event
        # In real tests, we'd mock the audit backend and verify the call
        store.set_threshold(st)

        # Verify threshold was stored
        assert store.get_threshold("code_gen", "analyzer") == 0.42

    def test_reset_audited(self, store):
        """Reset action is audited."""
        st = StoredThreshold(
            task_type="code_gen",
            subsystem="analyzer",
            tenant_id="_default",
            learned_threshold=0.42,
            sample_count=25,
            converged=True,
        )
        store.set_threshold(st)

        # Reset should trigger an audit event
        store.reset_all()

        # Verify reset worked
        assert len(store.get_all()) == 0
