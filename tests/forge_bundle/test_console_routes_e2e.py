"""Forge Bundle console routes — real HTTP through the console router (ADR-2229 Phase 4).

The router is mounted under ``/v1/console`` exactly as corvin_gateway/app.py
mounts it, with a real session cookie and CSRF token. The first test is the
reachability proof the previous version of these routes never had: they were
registered at ``/v1/console/v1/console/forge-bundles/...`` and answered 404 at
every path the frontend calls.
"""
from __future__ import annotations

import io
import zipfile

import pytest

from core.forge_bundle import ToolQuarantine

T = "_default"
BASE = "/v1/console/forge-bundles"


def _export(client, csrf, selections, bundle_id="acme-automation", version="2.0.0"):
    return client.post(f"{BASE}/export", headers={"X-CSRF-Token": csrf}, json={
        "bundle_id": bundle_id, "bundle_version": version, "description": "e2e",
        "selections": selections,
    })


def _upload(client, csrf, path, data):
    return client.post(f"{BASE}/{path}", headers={"X-CSRF-Token": csrf},
                       files={"file": ("bundle.zip", data, "application/zip")})


def _drop_originals():
    from forge.multi_registry import MultiRegistry

    from core.orchestration.layer_forge.orchestrator import layer_forge_home

    MultiRegistry(tenant_id=T).delete("csv.count")
    (layer_forge_home(T) / "registry" / "acme.audit-l34@1.0.0.json").unlink()


def test_routes_answer_at_the_path_the_frontend_calls_and_not_doubled(console_client):
    client, _csrf = console_client
    assert client.get(f"{BASE}/quarantine").status_code == 200
    assert client.get(f"/v1/console{BASE}/quarantine").status_code == 404  # positive control


def test_routes_require_a_session(console_client):
    client, _csrf = console_client
    client.cookies.clear()
    assert client.get(f"{BASE}/quarantine").status_code == 401
    assert client.get(f"{BASE}/exportable").status_code == 401


def test_mutations_require_csrf(console_client):
    client, _csrf = console_client
    r = client.post(f"{BASE}/import", files={"file": ("b.zip", b"x", "application/zip")})
    assert r.status_code == 403


def test_full_round_trip_export_import_review(console_client, make_skill, make_tool, make_layer, chain_events):
    client, csrf = console_client
    make_skill("summarize", "1.0.0")
    make_tool("csv.count")
    make_layer("acme.audit-l34", "1.0.0")

    inv = client.get(f"{BASE}/exportable").json()
    assert {s["id"] for s in inv["skills"]} == {"summarize"}
    assert "csv.count" in {t["id"] for t in inv["tools"]}
    assert {(l["id"], l["version"]) for l in inv["layers"]} == {("acme.audit-l34", "1.0.0")}

    r = _export(client, csrf, [
        {"kind": "skill", "id": "summarize", "version": "1.0.0"},
        {"kind": "tool", "id": "csv.count", "version": "0.2.0"},
        {"kind": "layer", "id": "acme.audit-l34", "version": "1.0.0"},
    ])
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/zip"
    assert 'filename="acme-automation-2.0.0.zip"' in r.headers["content-disposition"]
    bundle = r.content
    _drop_originals()

    v = _upload(client, csrf, "validate", bundle).json()
    assert v["valid"] is True and v["origin_verified"] is False
    assert {a["kind"] for a in v["artifacts"]} == {"skill", "tool", "layer"}

    r = _upload(client, csrf, "import", bundle)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["failed_count"] == 0
    assert {o["kind"]: o["status"] for o in body["outcomes"]} == {
        "skill": "installed", "tool": "quarantined", "layer": "forged"}

    q = client.get(f"{BASE}/quarantine").json()
    assert q["count"] == 1 and q["items"][0]["tool_id"] == "csv.count"
    assert q["items"][0]["origin_verified"] is False
    qid = q["items"][0]["quarantine_id"]

    r = client.post(f"{BASE}/quarantine/{qid}/accept", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.text
    from forge.multi_registry import MultiRegistry
    assert MultiRegistry(tenant_id=T).get("csv.count").meta["origin"] == "forge_bundle"
    assert client.get(f"{BASE}/quarantine").json()["count"] == 0

    types = [e["event_type"] for e in chain_events()]
    for t in ("forge_bundle.exported", "forge_bundle.import_validated",
              "forge_bundle.artifact_staged", "forge_bundle.quarantine_accepted"):
        assert t in types, t


def test_reject_route_and_unknown_id(console_client, make_tool):
    client, csrf = console_client
    make_tool("csv.count")
    bundle = _export(client, csrf, [{"kind": "tool", "id": "csv.count", "version": "0.2.0"}]).content
    from forge.multi_registry import MultiRegistry
    MultiRegistry(tenant_id=T).delete("csv.count")
    _upload(client, csrf, "import", bundle)
    qid = client.get(f"{BASE}/quarantine").json()["items"][0]["quarantine_id"]

    assert client.post(f"{BASE}/quarantine/{'0' * 32}/reject", headers={"X-CSRF-Token": csrf}).status_code == 404
    assert client.post(f"{BASE}/quarantine/*/reject", headers={"X-CSRF-Token": csrf}).status_code == 404
    r = client.post(f"{BASE}/quarantine/{qid}/reject", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    assert MultiRegistry(tenant_id=T).get("csv.count") is None


def test_rejected_upload_answers_422_with_the_stage(console_client):
    client, csrf = console_client
    r = _upload(client, csrf, "import", b"not a zip")
    assert r.status_code == 422
    assert r.json()["detail"]["stage"] == "container"
    v = _upload(client, csrf, "validate", b"not a zip").json()
    assert v == {"valid": False, "stage": "container", "reason": v["reason"], "origin_verified": False}


def test_oversized_upload_is_refused(console_client):
    client, csrf = console_client
    r = _upload(client, csrf, "import", b"0" * (50 * 1024 * 1024 + 1))
    assert r.status_code == 413
    assert ToolQuarantine(T).list() == []


def test_plugin_export_is_refused_in_the_console(console_client):
    client, csrf = console_client
    r = _export(client, csrf, [{"kind": "plugin", "id": "x", "version": "1.0.0"}])
    assert r.status_code == 400 and "CLI" in r.json()["detail"]


def test_export_of_a_missing_artifact_is_422(console_client):
    client, csrf = console_client
    r = _export(client, csrf, [{"kind": "skill", "id": "nope", "version": "1.0.0"}])
    assert r.status_code == 422 and "skill not found" in r.json()["detail"]


def test_audit_outage_answers_503_and_changes_nothing(console_client, make_tool, monkeypatch):
    client, csrf = console_client
    make_tool("csv.count")
    bundle = _export(client, csrf, [{"kind": "tool", "id": "csv.count", "version": "0.2.0"}]).content
    from forge.multi_registry import MultiRegistry
    MultiRegistry(tenant_id=T).delete("csv.count")

    from core.forge_bundle import import_module
    from core.forge_bundle.audit import ForgeBundleAuditError

    def down(event, **_k):
        raise ForgeBundleAuditError(event)

    monkeypatch.setattr(import_module, "emit", down)
    r = _upload(client, csrf, "import", bundle)
    assert r.status_code == 503
    assert ToolQuarantine(T).list() == []


def test_no_route_in_the_module_carries_an_absolute_prefix():
    from corvin_console.routes import forge_bundle_routes

    assert forge_bundle_routes.router.prefix == ""
    for route in forge_bundle_routes.router.routes:
        assert route.path.startswith("/forge-bundles/"), route.path


def _client_for(tenant: str):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from corvin_console import auth
    from corvin_console.app import router

    rec = auth.create_session(tenant_id=tenant, token_fingerprint="test-fp-2")
    app = FastAPI()
    app.include_router(router, prefix="/v1/console")
    client = TestClient(app, raise_server_exceptions=False)
    client.cookies.set("corvin_console_sid", rec.sid)
    return client, auth.derive_csrf_token(rec.csrf_secret, rec.sid)


def test_another_tenant_never_sees_or_exports_host_wide_skills(tmp_corvin_home, make_skill):
    make_skill("summarize", "1.0.0")
    client, csrf = _client_for("acme")
    assert client.get(f"{BASE}/exportable").json()["skills"] == []
    r = _export(client, csrf, [{"kind": "skill", "id": "summarize", "version": "1.0.0"}])
    assert r.status_code == 403 and "install owner" in r.json()["detail"]


def test_an_audit_outage_mid_import_answers_503_with_what_landed(console_client, make_tool, make_layer, monkeypatch):
    client, csrf = console_client
    make_tool("csv.count")
    make_layer("acme.audit-l34", "1.0.0")
    bundle = _export(client, csrf, [{"kind": "tool", "id": "csv.count", "version": "0.2.0"},
                                    {"kind": "layer", "id": "acme.audit-l34", "version": "1.0.0"}]).content
    _drop_originals()

    from core.forge_bundle import import_module
    from core.forge_bundle.audit import ForgeBundleAuditError
    real, n = import_module.emit, {"started": 0}

    def flaky(event, **kw):
        if event == "forge_bundle.artifact_intake_started":
            n["started"] += 1
            if n["started"] == 2:
                raise ForgeBundleAuditError(event)
        return real(event, **kw)

    monkeypatch.setattr(import_module, "emit", flaky)
    r = _upload(client, csrf, "import", bundle)
    assert r.status_code == 503
    detail = r.json()["detail"]
    assert "stopped" in detail["message"]
    assert [o["status"] for o in detail["outcomes"]][1:] == ["not_attempted"]
    assert detail["outcomes"][0]["status"] != "not_attempted"


def test_cli_refuses_a_missing_output_directory_before_recording(cli_runner, make_tool, chain_events, tmp_path):
    make_tool("csv.count")
    proc = cli_runner(["export", "--id", "b", "--version", "1.0.0",
                       "--output", str(tmp_path / "missing" / "b.zip"), "--tool", "csv.count@0.2.0"])
    assert proc.returncode == 2 and "does not exist" in proc.stderr
    assert "forge_bundle.exported" not in [e["event_type"] for e in chain_events()]
