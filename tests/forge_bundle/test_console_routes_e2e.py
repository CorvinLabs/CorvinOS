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


def test_R2B_6_7_another_tenant_is_refused_on_every_route(tmp_corvin_home, make_skill, make_tool):
    make_skill("summarize", "1.0.0")
    make_tool("csv.count")
    client, csrf = _client_for("acme")
    for r in (client.get(f"{BASE}/exportable"), client.get(f"{BASE}/quarantine"),
              _upload(client, csrf, "validate", b"x"), _upload(client, csrf, "import", b"x"),
              _export(client, csrf, [{"kind": "tool", "id": "csv.count", "version": "0.2.0"}])):
        assert r.status_code == 403 and "owner's tenant" in r.json()["detail"], r.text
        assert "summarize" not in r.text and "csv.count" not in r.text


def test_R2B_5_a_corrupt_store_answers_503_not_500(console_client, tmp_corvin_home):
    client, _csrf = console_client
    broken = tmp_corvin_home / "skills_gen" / "broken"
    broken.mkdir(parents=True)
    (broken / "skill.json").write_text("[]")
    from core.orchestration.layer_forge.orchestrator import layer_forge_home
    (layer_forge_home(T) / "registry" / "x@1.0.0.json").write_text("{not json")
    r = client.get(f"{BASE}/exportable")
    assert r.status_code == 503


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


def test_R2B_11_cli_refuses_a_directory_as_output(cli_runner, make_tool, chain_events, tmp_path):
    make_tool("csv.count")
    proc = cli_runner(["export", "--id", "b", "--version", "1.0.0", "--output", str(tmp_path), "--tool", "csv.count@0.2.0"])
    assert proc.returncode == 2 and "is a directory" in proc.stderr
    assert "forge_bundle.exported" not in [e["event_type"] for e in chain_events()]


def test_R2B_12_cli_never_follows_a_planted_temp_symlink(cli_runner, make_tool, tmp_path):
    make_tool("csv.count")
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me")
    (tmp_path / ".b.zip.tmp").symlink_to(victim)
    proc = cli_runner(["export", "--id", "b", "--version", "1.0.0",
                       "--output", str(tmp_path / "b.zip"), "--tool", "csv.count@0.2.0"])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert victim.read_text() == "keep me"
    assert (tmp_path / "b.zip").read_bytes()[:2] == b"PK"


def test_R2B_13_export_leaves_the_skill_folder_untouched(console_client, make_skill):
    client, csrf = console_client
    folder = make_skill("summarize", "1.0.0")
    before = {p.relative_to(folder): p.read_bytes() for p in folder.rglob("*") if p.is_file()}
    r = _export(client, csrf, [{"kind": "skill", "id": "summarize", "version": "1.0.0"}])
    assert r.status_code == 200, r.text
    after = {p.relative_to(folder): p.read_bytes() for p in folder.rglob("*") if p.is_file()}
    assert after == before


def test_R3B_1_2_3_hostile_json_never_answers_500(console_client, make_layer):
    import io
    import json as _json
    import zipfile as _zf
    client, csrf = console_client
    deep = io.BytesIO()
    with _zf.ZipFile(deep, "w") as zf:
        zf.writestr("forge-bundle.json", b"[" * 100000 + b"]" * 100000)
    for path in ("validate", "import"):
        r = _upload(client, csrf, path, deep.getvalue())
        assert r.status_code in (200, 422), (path, r.status_code, r.text[:200])
    assert _upload(client, csrf, "validate", deep.getvalue()).json()["stage"] == "envelope"


def test_R3B_7_a_reject_outage_says_reject_not_create(console_client, make_tool, monkeypatch):
    client, csrf = console_client
    make_tool("csv.count")
    bundle = _export(client, csrf, [{"kind": "tool", "id": "csv.count", "version": "0.2.0"}]).content
    from forge.multi_registry import MultiRegistry
    MultiRegistry(tenant_id=T).delete("csv.count")
    _upload(client, csrf, "import", bundle)
    qid = client.get(f"{BASE}/quarantine").json()["items"][0]["quarantine_id"]

    from core.forge_bundle import audit
    def down(event, **_k):
        raise audit.ForgeBundleAuditError(event)
    monkeypatch.setattr(audit, "emit", down)
    r = client.post(f"{BASE}/quarantine/{qid}/reject", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 503 and "not rejected" in r.json()["detail"]


def test_R3B_6_inventory_refusal_has_one_shape_on_both_routes(console_client, tmp_corvin_home, make_tool):
    client, csrf = console_client
    make_tool("csv.count")
    bundle = _export(client, csrf, [{"kind": "tool", "id": "csv.count", "version": "0.2.0"}]).content
    reg = tmp_corvin_home / "skills_installed" / "skills_registry.json"
    reg.parent.mkdir(parents=True, exist_ok=True)
    reg.write_text("[]")
    for path in ("validate", "import"):
        r = _upload(client, csrf, path, bundle)
        assert r.status_code == 503 and r.json()["detail"]["stage"] == "inventory", (path, r.text)


def _fill(fbr, kind):
    held = [fbr._heavy[kind].acquire(blocking=False) for _ in range(fbr._HEAVY_SLOTS)]
    assert all(held)
    return held


def _free(fbr, kind, held):
    for ok in held:
        if ok:
            fbr._heavy[kind].release()


@pytest.mark.parametrize("kind,retry_after", [("validate", "2"), ("import", "15")])
def test_ADV03_heavy_routes_answer_429_instead_of_holding_a_worker(console_client, kind, retry_after):
    """At most two handlers of each kind run at once; the next one is refused
    immediately with Retry-After, it never waits on a pool worker (ADV-03)."""
    from corvin_console.routes import forge_bundle_routes as fbr

    client, csrf = console_client
    held = _fill(fbr, kind)
    try:
        r = _upload(client, csrf, kind, b"not a zip")
        assert r.status_code == 429, (kind, r.text)
        assert r.headers["retry-after"] == retry_after
    finally:
        _free(fbr, kind, held)
    # Slots free again: the same upload reaches the validator (and is refused there).
    assert _upload(client, csrf, "import", b"not a zip").status_code == 422


def test_R4_A3_1_a_validate_flood_cannot_lock_the_operator_out_of_importing(console_client):
    """The UI validates on every file pick. With one shared pool, two busy validates
    made the operator's own import answer 429 (6 of 8 attempts, 2026-10-08 review)."""
    from corvin_console.routes import forge_bundle_routes as fbr

    client, csrf = console_client
    held = _fill(fbr, "validate")
    try:
        assert _upload(client, csrf, "validate", b"x").status_code == 429          # the flood is shed ...
        assert _upload(client, csrf, "import", b"not a zip").status_code == 422   # ... the import still runs
    finally:
        _free(fbr, "validate", held)
    held = _fill(fbr, "import")
    try:
        assert _upload(client, csrf, "import", b"x").status_code == 429
        assert _upload(client, csrf, "validate", b"not a zip").status_code == 200  # and the other way round
    finally:
        _free(fbr, "import", held)


def test_ADV03_a_refused_or_failing_request_never_leaks_a_slot(console_client):
    from corvin_console.routes import forge_bundle_routes as fbr

    client, csrf = console_client
    for _ in range(fbr._HEAVY_SLOTS * 3):          # 422 path, 400 path
        _upload(client, csrf, "import", b"not a zip")
        _upload(client, csrf, "validate", b"")
    for kind in ("validate", "import"):
        held = [fbr._heavy[kind].acquire(blocking=False) for _ in range(fbr._HEAVY_SLOTS)]
        try:
            assert all(held), f"a {kind} slot leaked"
        finally:
            _free(fbr, kind, held)


# ── second refutation round: plugin decision memory (R4-A9-2 / R4-A9-3) ──────

PLUGINS = "/v1/console/plugin-uploads"


def _package(make_plugin_package):
    """ONE package file and its bytes. make_plugin_package stamps the current time into the
    ZIP, so calling it twice across a second boundary yields a different package (a different
    content hash, hence no remembered decision) — the first version of these tests did, and
    failed only in a slow full run."""
    pkg = make_plugin_package("acme-audit-sink", "0.5.0")
    return pkg, pkg.read_bytes()


def _upload_plugin(client, csrf, data):
    return client.post(PLUGINS, headers={"X-CSRF-Token": csrf},
                       files={"file": ("acme.zip", data, "application/zip")})


def _bundle_of(pkg):
    from core.forge_bundle import PluginSelection, build_bundle

    return build_bundle(bundle_id="b", bundle_version="1.0.0", tenant_id=T,
                        selections=[PluginSelection("acme-audit-sink", "0.5.0", package_path=pkg)]).data


def test_R4_A9_2_a_manual_upload_withdraws_a_rejection(console_client, make_plugin_package):
    """The memory must never become a lock: the owner can always clear it by
    uploading the package themselves (the only way to clear one)."""
    client, csrf = console_client
    pkg, data = _package(make_plugin_package)
    bundle = _bundle_of(pkg)
    uid = _upload_plugin(client, csrf, data).json()["upload_id"]
    assert client.post(f"{PLUGINS}/{uid}/reject", headers={"X-CSRF-Token": csrf}).status_code == 200

    out = _upload(client, csrf, "import", bundle).json()["outcomes"][0]
    assert out["status"] == "failed" and "already been decided (rejected)" in out["detail"]

    assert _upload_plugin(client, csrf, data).status_code == 200   # the owner decides anew
    out = _upload(client, csrf, "import", bundle).json()["outcomes"][0]
    assert out["status"] == "pending_approval", out        # memory withdrawn; and it is merely pending


def test_R4_A9_2_an_unreadable_record_does_not_lock_a_plugin_out(make_plugin_package):
    from core.plugins.staging import StagingManager

    m = StagingManager(T)
    uid = "0123456789abcdef"
    m.decisions_root.mkdir(parents=True, exist_ok=True)
    for body in ("", "{not json", '{"decision": "banana"}', "[]"):
        (m.decisions_root / f"{uid}.json").write_text(body)
        assert m.get_decision(uid) is None, body
    (m.decisions_root / f"{uid}.json").write_text('{"decision": "rejected"}')   # positive control
    assert m.get_decision(uid) == "rejected"
    assert m.clear_decision(uid) is True and m.clear_decision(uid) is False
    assert m.get_decision(uid) is None


def test_R4_A9_3_rejecting_a_reupload_of_an_installed_plugin_keeps_the_approval(console_client, make_plugin_package):
    client, csrf = console_client
    _pkg, data = _package(make_plugin_package)
    uid = _upload_plugin(client, csrf, data).json()["upload_id"]
    assert client.post(f"{PLUGINS}/{uid}/approve", headers={"X-CSRF-Token": csrf}).status_code == 200
    from core.plugins.staging import StagingManager
    assert StagingManager(T).get_decision(uid) == "approved"

    # The owner uploads the installed package again and rejects THAT upload:
    # the installed ZIP stays, so the memory must keep saying "approved".
    assert _upload_plugin(client, csrf, data).json()["upload_id"] == uid
    assert client.post(f"{PLUGINS}/{uid}/reject", headers={"X-CSRF-Token": csrf}).status_code == 200
    assert StagingManager(T).get_decision(uid) == "approved"
