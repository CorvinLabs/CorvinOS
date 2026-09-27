"""
E2E tests for the Phase 10 skills console router (``routes/phase_10_skills.py``).

Rewritten 2026-09-27 (adversarial review). The router is NOT WIRED (no app
mounts it) and every handler used to return invented data — threats, success
rates, policy history — with no session guard. The previous tests called the
async handlers without awaiting them and indexed the coroutine, so all 22
failed; the ones that would have "passed" asserted the fabricated numbers.

What is proven now, through a real FastAPI app + TestClient:
* no session → 401 on every route;
* a mutation without the CSRF token → 403;
* with a session (and CSRF on mutations) → 501 ``not_implemented`` — no route
  answers 200 with invented data.
"""

from __future__ import annotations

import pytest

GETS = [
    "/v1/console/skills/workflow-optimizer/status",
    "/v1/console/skills/workflow-optimizer/metrics",
    "/v1/console/skills/security-orchestrator/threats/active",
    "/v1/console/skills/security-orchestrator/status",
    "/v1/console/skills/security-orchestrator/policy/current",
    "/v1/console/skills/security-orchestrator/policy/history",
    "/v1/console/skills/flow-guard/flows",
    "/v1/console/skills/flow-guard/policy",
    "/v1/console/skills/learning/feedback",
    "/v1/console/skills/skills/registry",
    "/v1/console/skills/skills/health",
]
POSTS = [
    "/v1/console/skills/workflow-optimizer/execute",
    "/v1/console/skills/security-orchestrator/threats/detect",
    "/v1/console/skills/security-orchestrator/threats/clear/t1",
    "/v1/console/skills/flow-guard/flows/review/f1",
    "/v1/console/skills/learning/feedback",
    "/v1/console/skills/skills/os.x/enable",
    "/v1/console/skills/skills/os.x/disable",
]


@pytest.fixture
def client_and_csrf(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    (tmp_path / "tenants" / "_default" / "global" / "console" / "sessions").mkdir(parents=True)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from corvin_console import auth as _auth
    from corvin_console.routes.phase_10_skills import router

    app = FastAPI()
    app.include_router(router)
    rec = _auth.create_session(tenant_id="_default", token_fingerprint="")
    client = TestClient(app, raise_server_exceptions=False)
    return client, rec.sid, _auth.derive_csrf_token(rec.csrf_secret, rec.sid)


def test_every_route_is_registered():
    from corvin_console.routes.phase_10_skills import router

    from starlette.routing import Match

    for method, path in [("GET", p) for p in GETS] + [("POST", p) for p in POSTS]:
        scope = {"type": "http", "method": method, "path": path}
        assert any(r.matches(scope)[0] == Match.FULL for r in router.routes), (method, path)


def test_no_session_is_401(client_and_csrf):
    client, _sid, _csrf = client_and_csrf
    for path in GETS:
        assert client.get(path).status_code == 401, path
    for path in POSTS:
        assert client.post(path).status_code == 401, path


def test_mutation_without_csrf_is_403(client_and_csrf):
    client, sid, _csrf = client_and_csrf
    client.cookies.set("corvin_console_sid", sid)
    for path in POSTS:
        assert client.post(path).status_code == 403, path


def test_authenticated_routes_are_501_not_fabricated(client_and_csrf):
    client, sid, csrf = client_and_csrf
    client.cookies.set("corvin_console_sid", sid)
    responses = [client.get(p) for p in GETS]
    responses += [client.post(p, headers={"X-CSRF-Token": csrf}) for p in POSTS]
    for r in responses:
        assert r.status_code == 501, (r.request.url, r.status_code, r.text)
        assert r.json()["detail"]["status"] == "not_implemented"
        assert "attacker@example.com" not in r.text
