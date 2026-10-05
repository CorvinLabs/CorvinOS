"""HTTP E2E proof for the Layer Forge console routes (ADR-2222).

Mounts the REAL console router (``corvin_console.app.router``) under
``/v1/console`` and drives it with an authenticated session cookie + CSRF
header, against a sandboxed CORVIN_HOME. Side effects are checked on disk:
the tenant's registry files and its hash-chained audit log.
"""
from __future__ import annotations

import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[2]
for _p in [str(_REPO / "corvin_operator"), str(_REPO / "corvin_operator" / "license"),
           str(_REPO / "corvin_operator" / "forge"), str(_REPO / "corvin_operator" / "bridges" / "shared"),
           str(_REPO / "core" / "console"), str(_REPO)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

TENANT = "_default"
BASE = "/v1/console/layer-forge/definitions"


def _reset_modules():
    for key in list(sys.modules):
        if any(key.startswith(p) for p in ("corvin_console", "corvin_gateway", "forge")):
            del sys.modules[key]


@contextmanager
def _client(tmp_path: Path):
    home = tmp_path / "corvin_home"
    th = home / "tenants" / TENANT
    for sub in ("global/auth", "global/forge", "global/console/sessions"):
        (th / sub).mkdir(parents=True)
    keys = ("CORVIN_HOME", "CORVIN_TENANT_ID", "VOICE_AUDIT_PATH")
    prev = {k: os.environ.get(k) for k in keys}
    os.environ["CORVIN_HOME"] = str(home)
    os.environ["CORVIN_TENANT_ID"] = TENANT
    os.environ["VOICE_AUDIT_PATH"] = str(home / "audit.jsonl")
    try:
        _reset_modules()
        from corvin_console import auth as _auth
        from corvin_console.app import router
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        rec = _auth.create_session(tenant_id=TENANT, token_fingerprint="test-fp")
        csrf = _auth.derive_csrf_token(rec.csrf_secret, rec.sid)
        app = FastAPI()
        app.include_router(router, prefix="/v1/console")
        client = TestClient(app, raise_server_exceptions=False)
        client.cookies.set("corvin_console_sid", rec.sid)
        yield client, csrf, home
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _reset_modules()


def _lf_events(home: Path) -> list[dict]:
    chain = home / "tenants" / TENANT / "global" / "forge" / "audit.jsonl"
    if not chain.exists():
        return []
    recs = [json.loads(l) for l in chain.read_text().splitlines() if l.strip()]
    return [r for r in recs if str(r.get("event_type", "")).startswith("layer_forge.")]


def _manifest(**kw):
    m = {"id": "console.rule", "version": "0.1.0", "targets": [{"layer_id": "L34"}],
         "quality_gates": [{"gate_id": "schema", "test_path": "tests/layer_forge/test_schema.py"}]}
    m.update(kw)
    return m


def test_routes_are_mounted_in_the_real_console_router(tmp_path):
    with _client(tmp_path) as (client, _csrf, _home):
        paths = set(client.get("/openapi.json").json()["paths"])
    assert BASE in paths
    assert BASE + "/{entry_id}" in paths
    assert BASE + "/{entry_id}/{version}/transition" in paths


def test_full_lifecycle_over_http(tmp_path):
    with _client(tmp_path) as (client, csrf, home):
        h = {"X-CSRF-Token": csrf}

        assert client.get(BASE).json() == {"items": [], "count": 0}

        r = client.post(BASE, json=_manifest(), headers=h)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "SUCCESS" and body["registry_key"] == "console.rule@0.1.0"
        assert body["gate_verdicts"][0]["status"] == "PASS"

        r = client.get(f"{BASE}/console.rule")
        assert r.status_code == 200 and r.json()["status"] == "accepted"
        assert client.get(BASE).json()["count"] == 1

        r = client.post(f"{BASE}/console.rule/0.1.0/transition", json={"to_status": "deployed"}, headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "deployed"

        r = client.post(f"{BASE}/console.rule/0.1.0/transition", json={"to_status": "proposed"}, headers=h)
        assert r.status_code == 409

        r = client.post(BASE, json=_manifest(), headers=h)
        assert r.status_code == 409

        events = _lf_events(home)
    proposed = [e for e in events if e["event_type"] == "layer_forge.definition_proposed"]
    assert len(proposed) == 1
    assert proposed[0]["details"]["actor"] == "console"
    assert proposed[0]["details"]["gates_skipped"] is False
    assert proposed[0]["details"]["tenant_id"] == TENANT
    transitions = [(e["details"]["from_status"], e["details"]["to_status"])
                   for e in events if e["event_type"] == "layer_forge.definition_transitioned"]
    assert transitions == [("proposed", "accepted"), ("accepted", "deployed")]


def test_mutations_require_csrf_and_reads_require_a_session(tmp_path):
    with _client(tmp_path) as (client, _csrf, home):
        assert client.post(BASE, json=_manifest()).status_code == 403
        assert client.post(f"{BASE}/x/0.1.0/transition", json={"to_status": "accepted"}).status_code == 403
        client.cookies.clear()
        assert client.get(BASE).status_code == 401
        assert _lf_events(home) == []


def test_invalid_and_unsafe_input_is_refused(tmp_path):
    with _client(tmp_path) as (client, csrf, home):
        h = {"X-CSRF-Token": csrf}
        evil = _manifest(quality_gates=[{"gate_id": "e", "test_path": "/etc/passwd"}])
        r = client.post(BASE, json=evil, headers=h)
        assert r.status_code == 422
        assert r.json()["detail"]["phase"] == "validate"

        failing = _manifest(id="console.failing",
                            quality_gates=[{"gate_id": "ghost", "test_path": "tests/nope_missing.py"}])
        r = client.post(BASE, json=failing, headers=h)
        assert r.status_code == 422 and r.json()["detail"]["phase"] == "test"

        assert client.get(f"{BASE}/..%2F..%2Fetc").status_code == 404
        assert client.get(f"{BASE}/console.failing").status_code == 404
        r = client.post(f"{BASE}/console.rule/0.1.0/transition", json={"to_status": "bogus"}, headers=h)
        assert r.status_code == 422
        events = _lf_events(home)
    assert not [e for e in events if e["event_type"] == "layer_forge.definition_proposed"]
    assert len([e for e in events if e["event_type"] == "layer_forge.definition_rejected"]) == 2
