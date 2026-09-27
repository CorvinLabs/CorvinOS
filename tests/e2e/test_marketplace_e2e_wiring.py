"""
E2E Wiring Proof for Marketplace API (ADR-0511).

Drives the REAL console routes (``/v1/console/api/v1/marketplace/*``) with a real
console session, against an ADR-0511 index written into a scratch directory
and selected through ``CORVIN_MARKETPLACE_INDEX`` — the operator override
``routes.marketplace.resolve_index_path`` honours first.

Rewritten 2026-09-27 (adversarial review): the previous "wiring proof" read
``corvin_operator/marketplace/index/plugins.json`` (moved to the
Corvin-Marketplace repo long ago, so the test could not pass) and then built
dicts it called "API responses" without sending a single request.
"""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

P = "/v1/console/api/v1/marketplace"


def _plugin(pid: str, name: str, category: str, tier: str = "buildin") -> dict:
    return {"id": pid, "name": name, "version": "1.0.0", "category": category,
            "tier": tier, "description": name, "license": "Apache-2.0"}


@pytest.fixture
def index(tmp_path):
    plugins = [
        _plugin("plugin:buildin-memory-recall_backend", "CEL Session Recall", "memory"),
        _plugin("plugin:buildin-sec-audit", "Audit Export", "security_compliance"),
        _plugin("plugin:contrib-integration-x", "X Connector", "integration", tier="contributor"),
    ]
    by_category, by_tier = {}, {}
    for p in plugins:
        by_category.setdefault(p["category"], []).append(p["id"])
        by_tier.setdefault(p["tier"], []).append(p["id"])
    data = {
        "version": "2.0", "schema": "ADR-0511", "plugin_count": len(plugins),
        "plugins": plugins, "by_id": {p["id"]: p for p in plugins},
        "by_category": by_category, "by_tier": by_tier,
    }
    path = tmp_path / "plugins.json"
    path.write_text(json.dumps(data))
    return path


@pytest.fixture
def client(tmp_path, monkeypatch, index):
    home = tmp_path / "corvin_home"
    for sub in ("auth", "forge", "console/sessions"):
        (home / "tenants" / "_default" / "global" / sub).mkdir(parents=True)
    monkeypatch.setenv("CORVIN_HOME", str(home))
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    monkeypatch.setenv("CORVIN_MARKETPLACE_INDEX", str(index))

    from core.console.corvin_console import auth as _auth
    from core.console.corvin_console.app import router
    from core.console.corvin_console.routes import marketplace as mp

    assert mp.resolve_index_path() == index  # positive control: the override is honoured
    prev = (mp._index_manager._index_path, mp._index_manager._index)
    mp.set_marketplace_index_path(mp.resolve_index_path())

    rec = _auth.create_session(tenant_id="_default", token_fingerprint="test-fp")
    app = FastAPI()
    app.include_router(router, prefix="/v1/console")
    c = TestClient(app, raise_server_exceptions=False)
    c.cookies.set("corvin_console_sid", rec.sid)
    yield c
    mp._index_manager._index_path, mp._index_manager._index = prev


def test_marketplace_e2e_wiring(client):
    r = client.get(f"{P}/plugins")
    assert r.status_code == 200, r.text
    ids = [p["id"] for p in r.json()["plugins"]]
    assert set(ids) == {"plugin:buildin-memory-recall_backend", "plugin:buildin-sec-audit",
                        "plugin:contrib-integration-x"}  # contributor tier is discoverable too

    r = client.get(f"{P}/plugins?category=memory")
    assert [p["id"] for p in r.json()["plugins"]] == ["plugin:buildin-memory-recall_backend"]

    r = client.get(f"{P}/plugins/plugin:buildin-memory-recall_backend")
    assert r.status_code == 200 and r.json()["name"] == "CEL Session Recall"
    assert client.get(f"{P}/plugins/plugin:nope").status_code == 404

    r = client.get(f"{P}/stats")
    assert r.status_code == 200
    body = r.json()
    assert body["total_plugins"] == 3
    assert body["by_tier"] == {"buildin": 2, "contributor": 1}
    assert body["schema_version"] == "ADR-0511"


def test_marketplace_requires_session(client):
    client.cookies.clear()
    assert client.get(f"{P}/plugins").status_code == 401
