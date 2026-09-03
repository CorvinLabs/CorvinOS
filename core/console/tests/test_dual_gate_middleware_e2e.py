"""
E2E Tests for Dual-Gate Middleware (ADR-0300/0301, CRITICAL-001 remediation).

Real HTTP requests through ``corvin_console.standalone.create_app()`` with a
stub ``DualGatePipeline`` installed via ``wiring.set_global_pipeline`` — the
same hook the bootstrap uses — so the middleware is exercised end to end:

* the principal (actor, tenant) comes from the authenticated session cookie
  ONLY (adversarial review E-05, 2026-09-03); ``X-User-ID`` / ``X-Tenant-ID``
  request headers are ignored;
* an anonymous caller is denied at the gate (401) without reaching the
  capability checker;
* the public allowlist (``deps.PUBLIC_PATH_PREFIXES``) bypasses the gate;
* a broken capability gate denies (403), a failed post-audit denies (500) —
  fail-closed in both directions;
* the ``call_site_registry`` entry points are mounted (not dead code).
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.filterwarnings("ignore")

TENANT = "_default"


class _StubPipeline:
    """Records what the middleware asked; behaviour switchable per test."""

    def __init__(self):
        self.capability_calls: list[dict] = []
        self.audit_calls: list[dict] = []
        self.capability_error: Exception | None = None
        self.capability_result = True
        self.audit_error: Exception | None = None

    def check_capability(self, *, actor, capability, tenant_id):
        self.capability_calls.append(
            {"actor": actor, "capability": capability, "tenant_id": tenant_id}
        )
        if self.capability_error is not None:
            raise self.capability_error
        return self.capability_result

    def record_audit(self, **kw):
        self.audit_calls.append(kw)
        if self.audit_error is not None:
            raise self.audit_error


@pytest.fixture(scope="module")
def sb(tmp_path_factory):
    home = tmp_path_factory.mktemp("dg_home")
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


class TestDualGateMiddlewareProtectsAllRoutes:
    def test_get_request_goes_through_the_gate(self, sb, pipeline):
        r = sb.authed.get("/v1/console/chat/sessions")
        assert r.status_code == 200, r.text
        assert pipeline.capability_calls, "middleware did not consult the capability gate"
        assert pipeline.audit_calls[-1]["event_type"] == "route_access"

    def test_post_request_goes_through_the_gate(self, sb, pipeline):
        r = sb.authed.post("/v1/console/chat/sessions", json={"title": "t"})
        assert r.status_code != 404
        # prefix-based inference: "/v1/..." resolves to the generic (fail-closed) write
        assert pipeline.capability_calls[-1]["capability"] in ("write", "write_chat_sessions")

    def test_delete_request_goes_through_the_gate(self, sb, pipeline):
        r = sb.authed.delete("/v1/console/chat/sessions/test-sid")
        assert r.status_code != 404
        assert pipeline.capability_calls[-1]["capability"] == "delete"

    def test_public_allowlist_skips_the_gate(self, sb, pipeline):
        assert sb.anon.get("/v1/console/healthz").status_code == 200
        assert sb.anon.get("/v1/console/version").status_code == 200
        assert pipeline.capability_calls == [] and pipeline.audit_calls == []

    def test_principal_comes_from_the_session_not_headers(self, sb, pipeline):
        spoof = {"X-User-ID": "root", "X-Tenant-ID": "victim_tenant"}
        r = sb.authed.get("/v1/console/chat/sessions", headers=spoof)
        assert r.status_code == 200
        call = pipeline.capability_calls[-1]
        assert call["actor"] == sb.rec.sid_fingerprint
        assert call["tenant_id"] == TENANT
        assert pipeline.audit_calls[-1]["actor"] == sb.rec.sid_fingerprint
        assert pipeline.audit_calls[-1]["tenant_id"] == TENANT

    def test_anonymous_is_denied_before_the_checker(self, sb, pipeline):
        r = sb.anon.get("/v1/console/chat/sessions", headers={"X-User-ID": "root"})
        assert r.status_code == 401
        assert pipeline.capability_calls == []
        assert pipeline.audit_calls[-1]["event_type"] == "capability_denied"
        assert pipeline.audit_calls[-1]["actor"] == "anonymous"

    def test_audit_event_has_required_fields(self, sb, pipeline):
        sb.authed.get("/v1/console/chat/sessions")
        ev = pipeline.audit_calls[-1]
        for key in ("event_type", "actor", "action", "resource", "result", "tenant_id", "details"):
            assert key in ev, key
        assert ev["details"]["method"] == "GET"
        assert ev["details"]["path"] == "/v1/console/chat/sessions"


class TestCritical001Validation:
    """CRITICAL-001: All entry points must be wired and reachable (not dead code)."""

    def test_chat_routes_exist(self, sb):
        assert sb.anon.get("/v1/console/chat/sessions").status_code != 404

    def test_entry_points_are_reachable_not_dead_code(self, sb):
        from core.pipeline.call_site_registry import get_registry

        registry = get_registry()
        chat_routes = registry.by_category("flask_route")[:3]  # Sample 3
        for ep in chat_routes:
            if ep.http_method and ep.http_path:
                if ep.http_method == "GET":
                    r = sb.anon.get(ep.http_path)
                elif ep.http_method == "POST":
                    r = sb.anon.post(ep.http_path, json={})
                elif ep.http_method == "DELETE":
                    r = sb.anon.delete(ep.http_path)
                else:
                    continue
                assert r.status_code != 404, f"Entry point {ep.name} at {ep.http_path} is dead code (404)"


class TestMiddlewareFailClosedBehavior:
    def test_gate_failure_denies_access(self, sb, pipeline):
        pipeline.capability_error = RuntimeError("gate broken")
        r = sb.authed.get("/v1/console/chat/sessions")
        assert r.status_code == 403

    def test_capability_denied_is_403_for_authenticated(self, sb, pipeline):
        pipeline.capability_result = False
        r = sb.authed.get("/v1/console/chat/sessions")
        assert r.status_code == 403
        assert pipeline.audit_calls[-1]["event_type"] == "capability_denied"

    def test_audit_failure_denies_access(self, sb, pipeline):
        pipeline.audit_error = RuntimeError("chain unreachable")
        r = sb.authed.get("/v1/console/chat/sessions")
        assert r.status_code == 500
        assert r.json() == {"error": "Audit system error"}

    def test_pipeline_off_is_a_quiet_pass_through(self, sb):
        from core.pipeline import wiring

        wiring.set_global_pipeline(None)
        assert sb.authed.get("/v1/console/chat/sessions").status_code == 200
        assert sb.anon.get("/v1/console/chat/sessions").status_code == 401  # route's own gate


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
