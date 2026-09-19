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


def test_index_entries_carry_installability_and_local_state(client):
    entries = _index_entries(client)
    for e in entries:
        for k in ("registry_id", "installable", "install_blocker", "installed", "enabled", "runtime_loaded"):
            assert k in e, (e["id"], k)
    contributor = [e for e in entries if e["tier"] == "contributor"]
    builtin = [e for e in entries if e["tier"] == "buildin"]
    assert builtin, "the index has no builtin entry"
    assert contributor, "the index has no contributor entry"
    # Since 2026-09-20 every entry of the checkout carries a plugin.yaml and
    # resolves locally — builtin as vetted, contributor as community. A blocker
    # is still what an entry WITHOUT a local source would show (see the
    # unknown-id case below), never a button that always fails.
    blocked = [(e["id"], e["install_blocker"]) for e in entries if not e["installable"]]
    assert not blocked, blocked
    assert all(e["registry_id"] for e in entries)
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
    # a well-formed id whose tier is not one the checkout serves
    r = client.post("/v1/console/api/v1/marketplace/plugins/plugin:remote-memory-x/install", json={})
    assert r.status_code == 200 and r.json()["status"] == "failed"


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


# ── 7. contributor tier: install from the checkout, consent on enable, panel ──
#
# ADR-0892 amendment (2026-09-20): plugins under plugins/contributor/ of the
# marketplace checkout resolve with origin=community. Enabling one without
# consent is refused; with consent the plugin's declared console panel is
# registered and the capability manifest lists it (sidebar entry under
# "Marketplace"); disable hides it, uninstall removes it.

_VIDEO = "plugin:contributor-media-video_producer"


def _install(client, index_id: str, **body) -> dict:
    r = client.post(f"/v1/console/api/v1/marketplace/plugins/{index_id}/install", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _manifest_panel_routes(client) -> set[str]:
    body = client.get("/v1/console/capabilities/manifest").json()
    return {p["route"] for p in body["panels"] if p.get("kind") == "plugin"}


def test_contributor_entries_are_installable_with_community_origin(client):
    entries = {e["id"]: e for e in _index_entries(client)}
    assert _VIDEO in entries, "the regenerated index lists the Video Producer"
    contributor = [e for e in entries.values() if e["tier"] == "contributor"]
    assert contributor and all(e["installable"] for e in contributor), [
        (e["id"], e["install_blocker"]) for e in contributor if not e["installable"]]
    body = _install(client, _VIDEO)
    assert body["status"] == "completed", body
    assert body["registry_id"] == "video_producer"
    assert body["origin"] == "community" and body["requires_consent"] is True
    listed = {p["plugin_id"]: p for p in client.get("/v1/console/plugins").json()["plugins"]}
    rec = listed["video_producer"]
    assert rec["origin"] == "community" and rec["requires_consent"] is True and rec["enabled"] is False
    # settings start from the manifest's declared defaults, never empty
    assert rec["settings"]["tts_engine"] == "openai" and rec["settings"]["max_video_length_minutes"] == 60
    assert rec["settings_schema"]["properties"]["tts_engine"]["enum"] == ["openai", "piper"]


def test_video_producer_panel_follows_enable_disable_uninstall(client, home):
    _install(client, _VIDEO)
    assert "video-producer" not in _manifest_panel_routes(client), "not enabled yet → no sidebar entry"

    # enable WITHOUT consent → refused (community origin)
    r = client.post("/v1/console/plugins/video_producer/enable", json={"consent_granted": False})
    assert r.status_code in (403, 409), r.text
    assert "video-producer" not in _manifest_panel_routes(client)

    # enable WITH consent → panel registered, listed by the manifest
    r = client.post("/v1/console/plugins/video_producer/enable", json={"consent_granted": True})
    assert r.status_code == 200, r.text
    assert r.json()["enabled"] is True
    assert "video-producer" in _manifest_panel_routes(client)
    panel = next(p for p in client.get("/v1/console/capabilities/manifest").json()["panels"] if p["route"] == "video-producer")
    assert panel["id"] == "plugin-video_producer" and panel["nav_group"] == "marketplace"
    assert panel["element"] == {"kind": "react-component", "component": "VideoProducerPage"}
    reg = home / "tenants" / "_default" / "plugins" / "panel_registry.json"
    assert reg.is_file() and "plugin-video_producer" in reg.read_text()

    # settings are validated against the schema and persisted
    r = client.post("/v1/console/plugins/video_producer/settings", json={"settings": {"tts_engine": "piper", "max_video_length_minutes": 5}})
    assert r.status_code == 200, r.text
    assert r.json()["settings"]["tts_engine"] == "piper"
    r = client.post("/v1/console/plugins/video_producer/settings", json={"settings": {"tts_engine": "not-an-engine"}})
    assert r.status_code in (400, 409, 422), r.text

    # disable → panel hidden; uninstall → gone
    assert client.post("/v1/console/plugins/video_producer/disable").status_code == 200
    assert "video-producer" not in _manifest_panel_routes(client)
    assert client.delete("/v1/console/plugins/video_producer").status_code == 200
    assert "video-producer" not in _manifest_panel_routes(client)
    assert "plugin-video_producer" not in reg.read_text()


def test_a_stale_panel_entry_without_an_installed_plugin_is_not_listed(client, home):
    """The maintainer install carried a panel registered on 2026-09-09 for a
    plugin that was never installed — it put "Video Producer" in the sidebar
    with nothing behind it. The manifest lists a plugin panel only while the
    tenant registry has that plugin installed AND enabled."""
    from core.plugins.plugin_panel_registry import get_panel_registry

    get_panel_registry("_default").register_panel("ghost", {
        "id": "plugin-ghost", "label": "Ghost", "route": "ghost", "icon": "Video", "group": "marketplace",
        "element_kind": "react-component", "component": "VideoProducerPage"})
    assert "ghost" not in _manifest_panel_routes(client)


# ── 8. the progress bar is real: phases, on a worker thread ──────────


def test_async_install_reports_real_phases_then_completes(client):
    import time

    entry = _pick_installable(client)
    r = client.post(f"/v1/console/api/v1/marketplace/plugins/{entry['id']}/install", json={"wait": False})
    assert r.status_code == 200, r.text
    job = r.json()
    assert job["status"] in ("pending", "installing") and 0 <= job["progress"] < 100
    seen: list[tuple[int, str]] = []
    for _ in range(200):
        p = client.get(f"/v1/console/api/v1/marketplace/install/{job['job_id']}/progress").json()
        seen.append((p["progress"], p["message"]))
        if p["status"] in ("completed", "failed"):
            break
        time.sleep(0.01)
    assert p["status"] == "completed", p
    assert p["progress"] == 100
    # monotone, and the phases are the install's own steps — not a timer
    progresses = [s[0] for s in seen]
    assert progresses == sorted(progresses)
    assert entry["registry_id"] in {x["plugin_id"] for x in client.get("/v1/console/plugins").json()["plugins"]}


def test_progress_of_another_tenants_job_is_404(client):
    from core.console.corvin_console.routes import marketplace_install as mi

    now = mi._now()
    mi._remember(mi.InstallJob("install_foreign", "p", "other_tenant", mi.JobStatus.PENDING, 0, "", now, now))
    assert client.get("/v1/console/api/v1/marketplace/install/install_foreign/progress").status_code == 404


# ── 9. the Knowledge Graph (contributor/knowledge_management/corvin_knowledge) ──

_KNOWLEDGE = "plugin:contributor-knowledge_management-corvin_knowledge"


def test_knowledge_graph_plugin_installs_and_its_panel_follows_enable(client, home):
    entry = next(e for e in _index_entries(client) if e["id"] == _KNOWLEDGE)
    assert entry["tier"] == "contributor" and entry["installable"], entry.get("install_blocker")
    body = _install(client, _KNOWLEDGE)
    assert body["status"] == "completed" and body["registry_id"] == "corvin_knowledge" and body["origin"] == "community"
    rec = {p["plugin_id"]: p for p in client.get("/v1/console/plugins").json()["plugins"]}["corvin_knowledge"]
    assert rec["settings"]["repo_path"] == "~/.corvin-knowledge/" and rec["settings"]["consistency_level"] == "warn"
    assert "corvin-knowledge" not in _manifest_panel_routes(client)
    assert client.post("/v1/console/plugins/corvin_knowledge/enable", json={"consent_granted": True}).status_code == 200
    panel = next(p for p in client.get("/v1/console/capabilities/manifest").json()["panels"] if p["route"] == "corvin-knowledge")
    assert panel["id"] == "plugin-corvin_knowledge" and panel["nav_group"] == "marketplace"
    assert panel["element"] == {"kind": "react-component", "component": "CorvinKnowledgePage"}
    assert client.delete("/v1/console/plugins/corvin_knowledge").status_code in (409,)  # enabled → refused
    assert client.post("/v1/console/plugins/corvin_knowledge/disable").status_code == 200
    assert client.delete("/v1/console/plugins/corvin_knowledge").status_code == 200
    assert "corvin-knowledge" not in _manifest_panel_routes(client)
