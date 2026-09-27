"""E2E tests for the Context Engineering config API (``routes/context_engineering.py``).

The router is NOT mounted by the console app (see its module docstring), so the
tests mount it on a FastAPI app of their own and drive it over HTTP with a real
console session against a scratch CORVIN_HOME.

Rewritten 2026-09-27 (adversarial review): the previous file used
``AsyncClient(app=...)`` against ``corvin_console.app.app`` (which does not
exist), so nothing ran — and had it run, it would have written the operator's
live ``~/.corvin`` through an unauthenticated route taking ``tenant_id`` from
the query string.
"""
from __future__ import annotations

import json

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.console.corvin_console.context_engineering_config import (
    ContextEngineeringConfigManager,
)

P = "/v1/console/context-engineering"


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "corvin_home"
    for t in ("_default", "acme"):
        for sub in ("auth", "forge", "console/sessions"):
            (h / "tenants" / t / "global" / sub).mkdir(parents=True)
    monkeypatch.setenv("CORVIN_HOME", str(h))
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    return h


def _client(tenant_id: str, *, csrf: bool = True) -> TestClient:
    from core.console.corvin_console import auth as _auth
    from core.console.corvin_console.routes import context_engineering as ce

    rec = _auth.create_session(tenant_id=tenant_id, token_fingerprint="test-fp")
    app = FastAPI()
    app.include_router(ce.router)
    c = TestClient(app, raise_server_exceptions=False)
    c.cookies.set("corvin_console_sid", rec.sid)
    if csrf:
        c.headers.update({"X-CSRF-Token": _auth.derive_csrf_token(rec.csrf_secret, rec.sid)})
    return c


def test_requires_session(home):
    c = _client("_default")
    c.cookies.clear()
    assert c.get(f"{P}/config").status_code == 401


def test_mutation_requires_csrf(home):
    assert _client("_default", csrf=False).post(f"{P}/config", json={"enabled": False}).status_code == 403


def test_get_default_update_persist_reset_audited(home):
    c = _client("_default")
    r = c.get(f"{P}/config")
    assert r.status_code == 200 and r.json()["enabled"] is True

    r = c.post(f"{P}/config", json={"stages": {"memory_lookup": {"max_results": 5}}})
    assert r.status_code == 200, r.text
    cfg = home / "tenants" / "_default" / "global" / "context-engineering.yaml"
    assert yaml.safe_load(cfg.read_text())["stages"]["memory_lookup"]["max_results"] == 5
    assert c.get(f"{P}/config").json()["stages"]["memory_lookup"]["max_results"] == 5

    assert c.post(f"{P}/config", json={"no_such_field": 1}).status_code == 400

    r = c.delete(f"{P}/config")
    assert r.status_code == 200
    assert r.json()["config"]["stages"]["memory_lookup"]["max_results"] == 10

    chain = home / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
    actions = [json.loads(l).get("details", {}).get("action") for l in chain.read_text().splitlines()]
    assert "context_engineering.config_updated" in actions
    assert "context_engineering.config_reset" in actions


def test_tenant_comes_from_session_not_query(home):
    _client("acme").post(f"{P}/config", json={"enabled": False})
    # a query-string tenant is ignored: _default still sees its own config
    r = _client("_default").get(f"{P}/config", params={"tenant_id": "acme"})
    assert r.json()["enabled"] is True
    assert not (home / "tenants" / "_default" / "global" / "context-engineering.yaml").exists()


@pytest.mark.parametrize("method,path", [("get", "/quota"), ("post", "/quota/reset"), ("get", "/metrics")])
def test_unmeasured_endpoints_fail_closed(home, method, path):
    r = getattr(_client("_default"), method)(f"{P}{path}")
    assert r.status_code == 501
    assert r.json()["detail"]["status"] == "not_implemented"


def test_manager_refuses_traversing_tenant_id(home):
    with pytest.raises(ValueError):
        ContextEngineeringConfigManager("../../etc")


def test_manager_deep_merge():
    base = {"a": {"b": 1, "c": 2}, "d": 3}
    assert ContextEngineeringConfigManager._deep_merge(base, {"a": {"b": 9}}) == {"a": {"b": 9, "c": 2}, "d": 3}
