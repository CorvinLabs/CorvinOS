"""Auth + CSRF on every control-plane route — through the real HTTP boundary.

Every route of the four control-plane routers (overrides, plugins, snapshots,
subsystems) must answer:

* 401 without a session cookie (reads AND mutations),
* 403 on a mutation with a valid session but no / a wrong ``x-csrf-token``,
* neither 401 nor 403 with a valid session (+ valid CSRF on mutations).

Routes are enumerated from the routers themselves, so a route added later is
covered without editing this file.

Round-4 rewrite (adversarial review 2026-09-27): the previous version imported
``core.console.auth`` (does not exist — collection error), built a
``SessionRecord`` with fields it does not have, and otherwise asserted the
return values of its own mocks (``mock_auth.is_approver.return_value = False``
→ ``assert result is False``) — it could not fail on a real defect.
"""
from __future__ import annotations

import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from corvin_console import auth as session_auth
from corvin_console.routes import (
    control_plane_overrides,
    control_plane_plugins,
    control_plane_snapshots,
    control_plane_subsystems,
)

_ROUTERS = (
    control_plane_overrides.router,
    control_plane_plugins.router,
    control_plane_snapshots.router,
    control_plane_subsystems.router,
)
_MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


def _routes() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for router in _ROUTERS:
        for route in router.routes:
            path = re.sub(r"\{[^}]+\}", "x1", route.path)
            for method in sorted(route.methods or ()):
                if method in ("HEAD", "OPTIONS"):
                    continue
                out.append((method, path))
    return out


_ALL = _routes()
_MUT = [r for r in _ALL if r[0] in _MUTATING]


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    app = FastAPI()
    for router in _ROUTERS:
        app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def session(client):
    rec = session_auth.create_session(tenant_id="_default")
    return rec, session_auth.derive_csrf_token(rec.csrf_secret, rec.sid)


def test_route_enumeration_is_not_vacuous():
    # Positive control: all four routers contribute, reads and mutations.
    assert len(_ALL) >= 25, _ALL
    assert len(_MUT) >= 12, _MUT
    for prefix in ("/control-plane/overrides", "/control-plane/plugins",
                   "/control-plane/snapshots", "/control-plane/subsystems"):
        assert any(p.startswith(prefix) for _, p in _ALL), prefix


@pytest.mark.parametrize("method,path", _ALL)
def test_no_session_is_401(client, method, path):
    r = client.request(method, path, json={})
    assert r.status_code == 401, (method, path, r.status_code, r.text[:200])


@pytest.mark.parametrize("method,path", _MUT)
def test_mutation_without_csrf_is_403(client, session, method, path):
    rec, _token = session
    client.cookies.set("corvin_console_sid", rec.sid)
    r = client.request(method, path, json={})
    assert r.status_code == 403, (method, path, r.status_code, r.text[:200])
    assert "csrf" in r.text.lower(), r.text[:200]  # the CSRF guard, not a later gate


@pytest.mark.parametrize("method,path", _MUT)
def test_mutation_with_wrong_csrf_is_403(client, session, method, path):
    rec, _token = session
    client.cookies.set("corvin_console_sid", rec.sid)
    r = client.request(method, path, json={}, headers={"x-csrf-token": "0" * 64})
    assert r.status_code == 403, (method, path, r.status_code, r.text[:200])
    assert "csrf" in r.text.lower(), r.text[:200]


@pytest.mark.parametrize("method,path", _ALL)
def test_valid_session_and_csrf_pass_the_guard(client, session, method, path):
    rec, token = session
    client.cookies.set("corvin_console_sid", rec.sid)
    headers = {"x-csrf-token": token} if method in _MUTATING else {}
    r = client.request(method, path, json={}, headers=headers)
    # Whatever the handler answers (404/422/501 …), the auth guard let it
    # through. The override routes additionally sit behind the per-user
    # consent gate (deny-by-default, GDPR Art. 6/7): a 403 there must be THAT
    # gate, never the session/CSRF guard.
    assert r.status_code != 401, (method, path, r.status_code, r.text[:200])
    if r.status_code == 403:
        assert "consent required" in r.text.lower(), (method, path, r.text[:200])
        assert path.startswith("/control-plane/overrides"), path
