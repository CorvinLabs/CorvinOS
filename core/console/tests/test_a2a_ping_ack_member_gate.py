"""`/v1/a2a/ping` and `/v1/a2a/friendship-ack` answer the member gate on EVERY host.

Until now only the stdlib handler (`a2a_http_server.py`) called `receiver._network_denied()`; the
console (`standalone.py`) and gateway (`app.py`) routes skipped it, so a Free install served by them
answered a ping / friendship ack. The gate now lives in the shared cores
(`process_ping_request`, `process_friendship_ack_request`).

Real HTTP requests through `corvin_console.standalone.create_app()` and the gateway app.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest.mock as mock
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

ROUTES = ("/v1/a2a/ping", "/v1/a2a/friendship-ack")


@pytest.fixture()
def env(tmp_path):
    d = {
        "CORVIN_HOME": str(tmp_path / "home"),
        "VOICE_AUDIT_PATH": str(tmp_path / "audit.jsonl"),
        "REMOTE_ORIGINS_DIR": str(tmp_path / "o"),
        "REMOTE_ENDPOINTS_DIR": str(tmp_path / "e"),
        "REMOTE_PENDING_FRIENDSHIPS_DIR": str(tmp_path / "p"),
        "CORVIN_A2A_RELAY_URL": "off",
    }
    for k in ("o", "e", "p"):
        (tmp_path / k).mkdir()
    with mock.patch.dict(os.environ, d):
        yield


def _console_client():
    from fastapi.testclient import TestClient
    from corvin_console.standalone import create_app
    return TestClient(create_app(), base_url="http://127.0.0.1")


def _gateway_client():
    from fastapi.testclient import TestClient
    import corvin_gateway.app as gw
    return TestClient(gw.app, base_url="http://127.0.0.1")


HOSTS = {"console": _console_client, "gateway": _gateway_client}


@pytest.mark.licence_tier("free")
@pytest.mark.parametrize("host", sorted(HOSTS))
@pytest.mark.parametrize("route", ROUTES)
def test_free_install_answers_the_refusal(env, host, route):
    c = HOSTS[host]()
    r = c.post(route, json={})
    assert r.status_code == 403, r.text
    assert r.json() == {"reason": "license_required"}


@pytest.mark.licence_tier("member")
@pytest.mark.parametrize("host", sorted(HOSTS))
@pytest.mark.parametrize("route", ROUTES)
def test_member_install_still_answers(env, host, route):
    c = HOSTS[host]()
    r = c.post(route, json={})
    assert r.status_code == 400, r.text      # past the gate: the shared core's field check
    assert r.json().get("reason") != "license_required"
