"""SRF-1 (review 2026-11-05): DNS rebinding against the credential-less local login.

A page on ``evil.example`` rebinds its name to 127.0.0.1: the TCP peer IS loopback,
the browser sends ``Host: evil.example:8765``. Before the fix that minted a console
session and ``/auth/whoami`` handed it the CSRF token. Driven over HTTP through the
real hosts (gateway app and the standalone console) with a crafted Host / Origin.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

EVIL = "evil.example:8765"
LOOPBACK_PEER = ("127.0.0.1", 40000)


@pytest.fixture
def audit_calls(monkeypatch):
    from corvin_console import host_guard

    # Spy on the chain write every admitted refusal goes through (the middleware
    # and the router dependency both end there; the throttle sits in front).
    calls: list[dict] = []
    real = host_guard.write_refusal

    def _spy(**kw):
        calls.append(kw)
        return real(**kw)

    monkeypatch.setattr(host_guard, "write_refusal", _spy)
    host_guard.reset_audit_throttle()
    return calls


@pytest.fixture(scope="module")
def gateway_app():
    from corvin_gateway.app import app

    return app


@pytest.fixture(scope="module")
def standalone_app():
    from corvin_console import standalone

    return standalone.create_app()


def _hosts(app):
    return TestClient(app, base_url="http://127.0.0.1:8765", client=LOOPBACK_PEER)


@pytest.mark.parametrize("which", ["gateway_app", "standalone_app"])
def test_rebound_host_gets_no_session(which, request, audit_calls):
    app = request.getfixturevalue(which)
    c = _hosts(app)
    r = c.get("/v1/console/auth/local-login", headers={"host": EVIL}, follow_redirects=False)
    assert r.status_code in (400, 403), r.status_code
    assert "set-cookie" not in r.headers
    w = c.get("/v1/console/auth/whoami", headers={"host": EVIL})
    assert w.status_code == 400
    assert "csrf_token" not in w.text
    reasons = {a["reason"] for a in audit_calls}
    assert "host_not_allowed" in reasons


@pytest.mark.parametrize("which", ["gateway_app", "standalone_app"])
def test_loopback_host_still_logs_in(which, request):
    app = request.getfixturevalue(which)
    c = _hosts(app)
    r = c.get("/v1/console/auth/local-login", headers={"host": "localhost:8765"},
              follow_redirects=False)
    assert r.status_code in (302, 303, 307), r.text
    w = c.get("/v1/console/auth/whoami", headers={"host": "localhost:8765"})
    assert w.status_code == 200 and len(w.json()["csrf_token"]) == 64


def test_allowlisted_public_name_reaches_the_api_but_not_local_login(gateway_app, monkeypatch,
                                                                      audit_calls):
    monkeypatch.setenv("CORVIN_ALLOWED_HOSTS", "testserver,corvin.example.com")
    c = _hosts(gateway_app)
    h = {"host": "corvin.example.com"}
    assert c.get("/v1/console/healthz", headers=h).status_code == 200
    r = c.get("/v1/console/auth/local-login", headers=h, follow_redirects=False)
    assert r.status_code == 403
    assert "set-cookie" not in r.headers
    assert any(a["reason"] == "local_login_host" for a in audit_calls)


def test_ip_literal_host_is_allowed(gateway_app):
    c = _hosts(gateway_app)
    assert c.get("/v1/console/healthz", headers={"host": "192.168.1.5:8765"}).status_code == 200
    assert c.get("/v1/console/healthz", headers={"host": "[::1]:8765"}).status_code == 200


def test_gateway_own_routes_are_guarded(gateway_app):
    c = _hosts(gateway_app)
    assert c.get("/healthz", headers={"host": EVIL}).status_code == 400
    assert c.get("/healthz", headers={"host": "127.0.0.1:8765"}).status_code == 200


def _login(c: TestClient) -> None:
    r = c.get("/v1/console/auth/local-login", headers={"host": "127.0.0.1:8765"},
              follow_redirects=False)
    assert r.status_code in (302, 303, 307), r.text


@pytest.mark.parametrize("which", ["gateway_app", "standalone_app"])
def test_chat_websocket_refuses_a_foreign_origin(which, request, audit_calls):
    app = request.getfixturevalue(which)
    c = _hosts(app)
    _login(c)
    url = "/v1/console/chat/sessions/does-not-exist/stream"
    # A page on another localhost port rides the Strict cookie: Origin differs.
    with pytest.raises(WebSocketDisconnect) as ei:
        with c.websocket_connect(url, headers={"host": "127.0.0.1:8765",
                                               "origin": "http://localhost:3000"}) as ws:
            ws.receive_json()
    assert ei.value.code == 1008
    assert any(a["reason"] == "origin_not_allowed" for a in audit_calls)
    # Same-origin passes the guard and reaches the route's own auth/session check
    # (4401 no cookie on the WS transport / 4404 unknown session) - never 1008.
    with pytest.raises(WebSocketDisconnect) as ei:
        with c.websocket_connect(url, headers={"host": "127.0.0.1:8765",
                                               "origin": "http://127.0.0.1:8765"}) as ws:
            ws.receive_json()
    assert ei.value.code in (4401, 4404)


def test_refusal_is_recorded_in_the_audit_chain(gateway_app, monkeypatch):
    from forge import paths as forge_paths

    from corvin_console import host_guard

    host_guard.reset_audit_throttle()
    chain = forge_paths.tenant_audit_chain("_default")
    before = chain.read_text(encoding="utf-8").count("\n") if chain.exists() else 0
    c = _hosts(gateway_app)
    assert c.get("/v1/console/auth/whoami", headers={"host": "rebind.attacker.test"}).status_code == 400
    lines = chain.read_text(encoding="utf-8").splitlines()[before:]
    recs = [json.loads(ln) for ln in lines if '"http.host_rejected"' in ln]
    assert recs, "no http.host_rejected record"
    d = recs[-1]["details"]
    assert d["reason"] == "host_not_allowed" and d["surface"] == "gateway"
    assert "attacker" not in json.dumps(recs[-1]), "the host value itself must not be stored"
    assert "_dropped_fields" not in d, d


def test_parsing_rules():
    from corvin_console import host_guard as hg

    assert hg.host_allowed("localhost:8765")
    assert hg.host_allowed("app.localhost")
    assert hg.host_allowed("127.0.0.2")
    assert hg.host_allowed(None)
    assert not hg.host_allowed("localhost.evil.example")
    assert not hg.host_allowed("evil.example")
    assert hg.origin_allowed(None, "127.0.0.1:8765", "ws")
    assert hg.origin_allowed("http://127.0.0.1:8765", "127.0.0.1:8765", "ws")
    assert hg.origin_allowed("http://localhost:5173", "127.0.0.1:8765", "ws")
    assert not hg.origin_allowed("null", "127.0.0.1:8765", "ws")
    assert not hg.origin_allowed("http://127.0.0.1:9999", "127.0.0.1:8765", "ws")
    assert hg.is_loopback_host("::1") and hg.is_loopback_host("[::1]:8765")
    assert not hg.is_loopback_host("192.168.1.5")
