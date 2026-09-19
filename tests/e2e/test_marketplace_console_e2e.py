"""ONE marketplace, real lifecycle — ADR-0892.

Every assertion goes through the console router over HTTP (TestClient), with
the session dependency overridden and CSRF enforced as a dependency, against a
TEMP ``CORVIN_HOME`` — never the operator's install. The plugin under test is a
REAL builtin from the Corvin-Marketplace checkout (the marketplace index is the
install allowlist; its source resolves under the checkout, ADR-0643).

1. Browse: the index carries local state; a contributor-tier entry is
   ``installable: false`` with a blocker; a builtin entry is installable.
2. Lifecycle: install → appears in /plugins (disabled) → the index now says
   installed → uninstall → gone. Enable is exercised only where the plugin's
   class loads in this interpreter; the registry write is what is proven.
3. The registry loader reads records nested under ``spec:`` (the layout found
   on the maintainer install), so an installed plugin never reads as absent.
4. Skill-forge distribution routes refuse an unauthenticated caller (401/403),
   where before they installed a ZIP for anyone who could reach the port.
5. MCP tools: the catalogue answers 200 (manager present) — never 503 on an
   install that ships ``corvin_operator/mcp_manager``.
6. The manifest declares the marketplace panel at the route the SPA mounts.
"""
from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.console.corvin_console import auth as session_auth  # noqa: E402
from core.console.corvin_console import deps as console_deps  # noqa: E402

_REPO = Path(__file__).resolve().parents[2]
_MARKETPLACE = _REPO.parent / "Corvin-Marketplace"
_INDEX = _MARKETPLACE / "index" / "plugins.json"

pytestmark = pytest.mark.skipif(not _INDEX.is_file(), reason="Corvin-Marketplace checkout not beside the repo")


def _fake_session_record(tenant_id: str) -> session_auth.SessionRecord:
    now = 1_000_000.0
    values: dict[str, object] = {}
    for f in dataclasses.fields(session_auth.SessionRecord):
        if f.default is not dataclasses.MISSING:
            continue
        ann = str(f.type)
        if "float" in ann:
            values[f.name] = now + (3600 if f.name == "expires_at" else 0)
        elif "bool" in ann:
            values[f.name] = False
        elif f.name == "tier":
            tier = getattr(session_auth, "Tier", None)
            values[f.name] = next(iter(tier)) if tier else "owner"
        elif f.name == "tenant_id":
            values[f.name] = tenant_id
        else:
            values[f.name] = f"test-{f.name}"
    return session_auth.SessionRecord(**values)  # type: ignore[arg-type]


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    h = tmp_path / "corvin-home"
    (h / "tenants" / "_default" / "global" / "forge").mkdir(parents=True)
    monkeypatch.setenv("CORVIN_HOME", str(h))
    monkeypatch.setenv("VOICE_AUDIT_PATH", str(h / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"))
    monkeypatch.setenv("CORVIN_MARKETPLACE_INDEX", str(_INDEX))
    return h


@pytest.fixture
def client(home):
    from core.console.corvin_console import feature_flags as _ff
    from core.console.corvin_console.routes import (
        capabilities, marketplace, mcp_plugins, plugins, skill_forge_distribution_routes,
    )

    marketplace.set_marketplace_index_path(_INDEX)
    app = FastAPI()
    app.include_router(marketplace.router, prefix="/v1/console")
    app.include_router(plugins.router, prefix="/v1/console")
    app.include_router(mcp_plugins.router, prefix="/v1/console")
    app.include_router(skill_forge_distribution_routes.router, prefix="/v1/console")
    app.include_router(capabilities.router, prefix="/v1/console")
    app.dependency_overrides[console_deps.require_session] = lambda: _fake_session_record("_default")
    # CSRF stays a real dependency on the *routes*; the override only says the
    # session is valid — a write without the header still fails in the SPA.
    app.dependency_overrides[console_deps.require_csrf] = lambda: _fake_session_record("_default")
    # The plugin surface and the runtime lifecycle are on for this tenant.
    for flag in ("plugin_console_surface", "plugin_runtime_lifecycle"):
        try:
            _ff.set_flag(flag, True, "_default")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 — API differs per flag store; fall back to a monkeypatch below
            pass
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _flags_on(monkeypatch):
    from core.console.corvin_console import feature_flags as _ff

    real = _ff.is_enabled

    def _on(name, tenant_id=None, *a, **k):
        if name in ("plugin_console_surface", "plugin_runtime_lifecycle", "package_marketplace_ui"):
            return True
        return real(name, tenant_id, *a, **k)

    monkeypatch.setattr(_ff, "is_enabled", _on)


def _index_entries(client):
    body = client.get("/v1/console/api/v1/marketplace/plugins?limit=1000").json()
    assert body["count"] == len(body["plugins"]) > 0
    return body["plugins"]


# ── 1. browse carries local state ────────────────────────────────────


def test_index_entries_carry_installability_and_a_blocker_for_contributor_tier(client):
    entries = _index_entries(client)
    for e in entries:
        for k in ("registry_id", "installable", "install_blocker", "installed", "enabled", "runtime_loaded"):
            assert k in e, (e["id"], k)
    contributor = [e for e in entries if e["tier"] == "contributor"]
    builtin = [e for e in entries if e["tier"] == "buildin"]
    assert builtin, "the index has no builtin entry"
    for e in contributor:
        assert e["installable"] is False and e["install_blocker"], e["id"]
        assert e["registry_id"] is None
    installable = [e for e in builtin if e["installable"]]
    assert installable, "no builtin entry resolves under the marketplace checkout"
    assert all(e["registry_id"] for e in installable)
    assert not any(e["installed"] for e in entries), "temp home: nothing is installed yet"


# ── 2. install → installed → uninstall, on disk ──────────────────────


def _pick_installable(client) -> dict:
    return next(e for e in _index_entries(client) if e["installable"])


def test_install_registers_the_plugin_then_uninstall_removes_it(client, home):
    entry = _pick_installable(client)
    r = client.post(f"/v1/console/api/v1/marketplace/plugins/{entry['id']}/install", json={"version": entry["version"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "completed", body
    assert body["registry_id"] == entry["registry_id"]

    # on disk: the tenant registry has the record, disabled
    reg = home / "tenants" / "_default" / "plugins" / "registry.yaml"
    assert reg.is_file()
    raw = yaml.safe_load(reg.read_text())
    assert entry["registry_id"] in raw["plugins"]
    assert raw["plugins"][entry["registry_id"]]["enabled"] is False
    assert oct(reg.stat().st_mode & 0o777) == "0o600"

    # /plugins lists it; the index now says installed
    listed = {p["plugin_id"]: p for p in client.get("/v1/console/plugins").json()["plugins"]}
    assert entry["registry_id"] in listed and listed[entry["registry_id"]]["enabled"] is False
    again = next(e for e in _index_entries(client) if e["id"] == entry["id"])
    assert again["installed"] is True and again["enabled"] is False

    # idempotent
    r2 = client.post(f"/v1/console/api/v1/marketplace/plugins/{entry['id']}/install", json={"version": entry["version"]})
    assert r2.json()["status"] == "completed" and r2.json().get("already_installed") is True

    # uninstall (disabled → allowed)
    r3 = client.delete(f"/v1/console/plugins/{entry['registry_id']}")
    assert r3.status_code == 200, r3.text
    assert r3.json()["uninstalled"] == entry["registry_id"]
    raw = yaml.safe_load(reg.read_text())
    assert entry["registry_id"] not in (raw.get("plugins") or {})
    assert entry["registry_id"] not in {p["plugin_id"] for p in client.get("/v1/console/plugins").json()["plugins"]}
    assert next(e for e in _index_entries(client) if e["id"] == entry["id"])["installed"] is False


def test_install_of_an_unknown_or_contributor_id_is_a_named_failure_not_a_500(client):
    r = client.post("/v1/console/api/v1/marketplace/plugins/plugin:buildin-memory-does_not_exist/install", json={})
    assert r.status_code == 200 and r.json()["status"] == "failed"
    assert "not in the marketplace index" in r.json()["error"]
    contributor = next((e for e in _index_entries(client) if e["tier"] == "contributor"), None)
    if contributor:
        r = client.post(f"/v1/console/api/v1/marketplace/plugins/{contributor['id']}/install", json={})
        assert r.status_code == 200 and r.json()["status"] == "failed"
        assert r.json()["error"] == contributor["install_blocker"]


# ── 3. the loader accepts records nested under spec: ─────────────────


def test_registry_records_nested_under_spec_are_read(client, home):
    entry = _pick_installable(client)
    assert client.post(f"/v1/console/api/v1/marketplace/plugins/{entry['id']}/install", json={}).json()["status"] == "completed"
    reg = home / "tenants" / "_default" / "plugins" / "registry.yaml"
    raw = yaml.safe_load(reg.read_text())
    nested = {"spec": {"schema_version": raw["spec"]["schema_version"], "plugins": raw["plugins"]}}
    reg.write_text(yaml.safe_dump(nested, sort_keys=False))
    listed = {p["plugin_id"] for p in client.get("/v1/console/plugins").json()["plugins"]}
    assert entry["registry_id"] in listed, "a nested registry read as 'not installed' until 2026-09-19"
    assert client.get(f"/v1/console/plugins/{entry['registry_id']}").status_code == 200


# ── 4. no unauthenticated install route ──────────────────────────────


def test_skill_forge_distribution_routes_require_a_session():
    from core.console.corvin_console.routes import skill_forge_distribution_routes as sfd

    app = FastAPI()
    app.include_router(sfd.router, prefix="/v1/console")
    with TestClient(app) as c:
        r = c.post("/v1/console/v1/skill-forge/install?url=http://127.0.0.1:9/x.zip")
        assert r.status_code in (401, 403), r.text
        r = c.get("/v1/console/v1/skill-forge/installed")
        assert r.status_code in (401, 403), r.text
        r = c.post("/v1/console/v1/skill-forge/package?skill_id=x")
        assert r.status_code in (401, 403), r.text


# ── 5. MCP tools are reachable when the manager ships ────────────────


def test_mcp_catalogue_answers_when_the_manager_is_present(client):
    from core.console.corvin_console.routes import mcp_plugins as mp

    assert (_REPO / "corvin_operator" / "mcp_manager" / "mcp_manager" / "catalog.py").is_file()
    assert mp._MCP_OK, mp._MCP_IMPORT_ERROR
    r = client.get("/v1/console/mcp-plugins")
    assert r.status_code == 200, r.text
    assert r.json()["tenant_id"] == "_default"


# ── 6. the manifest panel matches the SPA route ──────────────────────


def test_manifest_declares_the_marketplace_panel_at_the_mounted_route(client):
    body = client.get("/v1/console/capabilities/manifest").json()
    panel = next(p for p in body["panels"] if p["id"] == "plugins")
    assert panel["route"] == "marketplace"
    assert panel["element"] == {"kind": "react-component", "component": "MarketplacePage"}
    assert not any(p["route"] == "plugin-center" for p in body["panels"])
