"""Plugin UPDATE through the real console router (HTTP, TestClient).

A plugin author changes their plugin in the marketplace source; an operator who
installed it must be able to see the update and apply it. Builds a throwaway
marketplace source tree (CORVIN_MARKETPLACE_ROOT) + index, installs v1.0.0,
bumps the source to v1.1.0 and drives ``GET /updates`` and ``POST /update``.
"""
from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
for _p in reversed([
    "core/console", "core/gateway", "core/license", "core/compliance",
    "corvin_operator/forge", "corvin_operator/skill-forge",
    "corvin_operator/bridges/shared", "core/plugins",
]):
    if str(REPO / _p) not in sys.path:
        sys.path.insert(0, str(REPO / _p))

BASE = "/v1/console/api/v1/marketplace"
INDEX_ID = "plugin:buildin-memory-update_probe"
PLUGIN_ID = "update-probe"

def _fake_session_record(auth_mod, tenant_id: str):
    now = 1_000_000.0
    values: dict[str, object] = {}
    for f in dataclasses.fields(auth_mod.SessionRecord):
        if f.default is not dataclasses.MISSING or f.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
            continue
        ann = str(f.type)
        if "float" in ann:
            values[f.name] = now + (3600 if f.name == "expires_at" else 0)
        elif "bool" in ann:
            values[f.name] = False
        elif f.name == "tier":
            tier = getattr(auth_mod, "Tier", None)
            values[f.name] = next(iter(tier)) if tier else "owner"
        elif f.name == "tenant_id":
            values[f.name] = tenant_id
        else:
            values[f.name] = f"test-{f.name}"
    return auth_mod.SessionRecord(**values)



def _manifest(version: str, **over) -> dict:
    m = {
        "plugin_id": PLUGIN_ID, "plugin_type": "recall_backend", "version": version,
        "display_name": "Update Probe", "origin": "builtin", "boot_layer": "installed",
        "locality": "local", "network_egress": "none", "egress_hosts": [],
        "pii_risk": "none", "requires_consent": False, "audit_required": True,
        "capabilities_version": "1.0", "capabilities": [],
        "settings_schema": {"type": "object", "properties": {
            "keep": {"type": "string", "default": "a"},
            "old_only": {"type": "string", "default": "x"}}},
    }
    m.update(over)
    return m


@pytest.fixture()
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "tenants" / "_default" / "global" / "forge").mkdir(parents=True)
    root = tmp_path / "mkt"
    pdir = root / "plugins" / "buildin" / "memory" / "update_probe"
    pdir.mkdir(parents=True)
    index = tmp_path / "plugins.json"
    entry = {"id": INDEX_ID, "name": "Update Probe", "version": "1.0.0", "tier": "buildin",
             "category": "memory"}
    index.write_text(json.dumps({"version": "3", "generated_at": "t0", "plugins": [entry],
                                 "by_id": {INDEX_ID: entry}}))
    monkeypatch.setenv("CORVIN_HOME", str(home))
    monkeypatch.setenv("VOICE_AUDIT_PATH", str(home / "tenants/_default/global/forge/audit.jsonl"))
    monkeypatch.setenv("CORVIN_MARKETPLACE_ROOT", str(root / "plugins" / "buildin"))
    monkeypatch.setenv("CORVIN_MARKETPLACE_INDEX", str(index))

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import corvin_console.app as capp
    from corvin_console import auth as auth_mod, deps
    from corvin_console.routes import marketplace

    marketplace.set_marketplace_index_path(index)
    marketplace._RESOLVE_CACHE.clear()
    app = FastAPI()
    app.include_router(capp.router, prefix="/v1/console")
    rec = _fake_session_record(auth_mod, "_default")
    app.dependency_overrides[deps.require_session] = lambda: rec
    app.dependency_overrides[deps.require_csrf] = lambda: rec
    from corvin_console import feature_flags
    monkeypatch.setattr(feature_flags, "is_enabled", lambda *a, **k: True)

    def write(version, **over):
        (pdir / "plugin.yaml").write_text(yaml.safe_dump(_manifest(version, **over)))

    write("1.0.0")
    with TestClient(app) as c:
        yield c, write


def _install(c):
    r = c.post(f"{BASE}/plugins/{INDEX_ID}/install", json={})
    assert r.json()["status"] == "completed", r.text


def test_no_update_when_source_equals_installed(env):
    c, _write = env
    _install(c)
    r = c.get(f"{BASE}/updates")
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 0
    assert c.post(f"{BASE}/plugins/{INDEX_ID}/update", json={}).status_code == 409


def test_update_roundtrip_keeps_settings_and_reports_versions(env):
    c, write = env
    _install(c)
    reg_id = PLUGIN_ID
    r = c.post(f"/v1/console/plugins/{reg_id}/settings", json={"settings": {"keep": "mine", "old_only": "y"}})
    assert r.status_code == 200, r.text
    write("1.1.0", settings_schema={"type": "object", "properties": {
        "keep": {"type": "string", "default": "a"}, "added": {"type": "string", "default": "new"}}})

    up = c.get(f"{BASE}/updates").json()
    assert [(u["installed_version"], u["latest_version"]) for u in up["updates"]] == [("1.0.0", "1.1.0")]
    listing = c.get(f"{BASE}/plugins?limit=10").json()["plugins"][0]
    assert (listing["installed_version"], listing["latest_version"], listing["update_available"]) == ("1.0.0", "1.1.0", True)

    r = c.post(f"{BASE}/plugins/{INDEX_ID}/update", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["from_version"], body["to_version"]) == ("1.0.0", "1.1.0")
    assert body["settings_dropped"] == ["old_only"]

    got = c.get(f"/v1/console/plugins/{reg_id}").json()
    rec = got.get("plugin") or got
    assert rec["version"] == "1.1.0"
    assert rec["settings"] == {"keep": "mine", "added": "new"}
    assert c.get(f"{BASE}/updates").json()["count"] == 0


def test_update_that_widens_declarations_needs_approval(env):
    c, write = env
    _install(c)
    write("1.1.0", network_egress="external", egress_hosts=["api.example.com"])
    r = c.post(f"{BASE}/plugins/{INDEX_ID}/update", json={})
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["error"] == "update_refused"
    assert any("network_egress" in e for e in detail["escalations"])
    # Refused means untouched.
    assert c.get(f"{BASE}/updates").json()["updates"][0]["installed_version"] == "1.0.0"
    r = c.post(f"{BASE}/plugins/{INDEX_ID}/update", json={"approve_escalations": True})
    assert r.status_code == 200, r.text
    assert r.json()["to_version"] == "1.1.0"


def test_downgrade_is_refused(env):
    c, write = env
    _install(c)
    write("0.9.0")
    assert c.post(f"{BASE}/plugins/{INDEX_ID}/update", json={}).status_code == 409
    assert c.get(f"{BASE}/updates").json()["count"] == 0


def test_update_keeps_an_enabled_plugin_enabled(env):
    c, write = env
    _install(c)
    assert c.post(f"/v1/console/plugins/{PLUGIN_ID}/enable", json={}).status_code == 200
    write("1.2.0")
    r = c.post(f"{BASE}/plugins/{INDEX_ID}/update", json={})
    assert r.status_code == 200, r.text
    assert r.json()["enabled"] is True
    got = c.get(f"/v1/console/plugins/{PLUGIN_ID}").json()
    assert got["enabled"] is True and got["version"] == "1.2.0"
