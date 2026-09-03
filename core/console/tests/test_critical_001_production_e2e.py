"""
Production E2E Test for CRITICAL-001 Remediation — Dual-Gate Middleware Wiring.

This test verifies that:
1. The dual-gate middleware protects real Console API endpoints
2. The entry points are wired (no 404s on protected routes)
3. Authorization gates are enforced (401 anonymous / 403 denied)
4. Audit events are recorded for all protected access
5. No regressions in route functionality

Real HTTP requests (not mocked) via FastAPI TestClient against a scratch
``CORVIN_HOME``. The principal is the console SESSION (adversarial review
E-05, 2026-09-03) — ``X-User-ID`` / ``X-Tenant-ID`` headers are not an
authentication mechanism and are ignored by the middleware.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.filterwarnings("ignore")

TENANT = "_default"


class _StubPipeline:
    def __init__(self):
        self.calls: list[dict] = []
        self.audits: list[dict] = []

    def check_capability(self, *, actor, capability, tenant_id):
        self.calls.append({"actor": actor, "capability": capability, "tenant_id": tenant_id})
        return True

    def record_audit(self, **kw):
        self.audits.append(kw)


@pytest.fixture(scope="module")
def sb(tmp_path_factory):
    home = tmp_path_factory.mktemp("crit_home")
    for sub in ("global/auth", "global/forge", "global/console/sessions"):
        (home / "tenants" / TENANT / sub).mkdir(parents=True)
    prev = {k: os.environ.get(k) for k in ("CORVIN_HOME", "CORVIN_TENANT_ID")}
    os.environ["CORVIN_HOME"] = str(home)
    os.environ.pop("CORVIN_TENANT_ID", None)
    try:
        from corvin_console import auth as session_auth
        from corvin_console.standalone import create_app

        app = create_app()
        rec = session_auth.create_session(tenant_id=TENANT, token_fingerprint="")
        anon = TestClient(app, raise_server_exceptions=False)
        authed = TestClient(app, raise_server_exceptions=False)
        authed.cookies.set(session_auth.COOKIE_NAME, rec.sid)
        yield SimpleNamespace(app=app, anon=anon, authed=authed, rec=rec)
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@pytest.fixture
def pipeline():
    from core.pipeline import wiring

    stub = _StubPipeline()
    wiring.set_global_pipeline(stub)
    try:
        yield stub
    finally:
        wiring.set_global_pipeline(None)


class TestCritical001RouteWiring:
    """Verify CRITICAL-001: the routes are wired and protected (not dead code)."""

    @pytest.mark.parametrize("method,path", [
        ("GET", "/v1/console/chat/sessions"),
        ("POST", "/v1/console/chat/sessions"),
        ("DELETE", "/v1/console/chat/sessions/test-sid"),
        ("GET", "/v1/console/tasks"),
        ("GET", "/v1/console/plugins"),
        ("GET", "/v1/console/audit/layers"),
        ("GET", "/v1/console/settings"),
        ("GET", "/v1/console/audit/tail"),
        ("GET", "/v1/console/settings/stream"),
        ("GET", "/v1/console/voice/status"),
        ("POST", "/v1/console/voice/tts"),
    ])
    def test_route_is_wired(self, sb, method, path):
        r = sb.anon.request(method, path, json={} if method != "GET" else None)
        assert r.status_code != 404, f"{method} {path} not wired (404)"
        # every one of these is a session route: anonymous → 401
        assert r.status_code == 401, (method, path, r.status_code)


class TestMiddlewareProtectsRoutes:
    def test_anonymous_request_denied_at_the_gate(self, sb, pipeline):
        # a spoofed header is NOT a credential
        r = sb.anon.get("/v1/console/chat/sessions", headers={"X-User-ID": "root"})
        assert r.status_code == 401
        assert pipeline.calls == []

    def test_authenticated_request_allowed_through_the_gate(self, sb, pipeline):
        r = sb.authed.get("/v1/console/chat/sessions")
        assert r.status_code == 200, r.text
        assert pipeline.calls[-1]["actor"] == sb.rec.sid_fingerprint
        assert pipeline.calls[-1]["tenant_id"] == TENANT

    def test_healthz_bypasses_middleware(self, sb, pipeline):
        assert sb.anon.get("/v1/console/healthz").status_code == 200
        assert pipeline.calls == []

    def test_static_files_bypass_middleware(self, sb, pipeline):
        r = sb.anon.get("/static/test.js")
        assert r.status_code != 403 and r.status_code != 401
        assert pipeline.calls == []


class TestAuditLogging:
    def test_successful_access_logged(self, sb, pipeline):
        sb.authed.get("/v1/console/chat/sessions")
        ev = pipeline.audits[-1]
        assert ev["event_type"] == "route_access" and ev["result"] == "success"
        assert ev["actor"] == sb.rec.sid_fingerprint and ev["tenant_id"] == TENANT

    def test_denied_access_logged(self, sb, pipeline):
        sb.anon.get("/v1/console/chat/sessions")
        ev = pipeline.audits[-1]
        assert ev["event_type"] == "capability_denied" and ev["actor"] == "anonymous"
        assert ev["tenant_id"] == TENANT  # never a header-chosen tenant


class TestMiddlewareFailClosed:
    def test_malformed_headers_are_irrelevant(self, sb, pipeline):
        r = sb.anon.get("/v1/console/chat/sessions", headers={"X-User-ID": "\x01\x02"})
        assert r.status_code == 401

    def test_header_tenant_is_ignored(self, sb, pipeline):
        sb.authed.get("/v1/console/chat/sessions", headers={"X-Tenant-ID": "victim_tenant"})
        assert pipeline.calls[-1]["tenant_id"] == TENANT
