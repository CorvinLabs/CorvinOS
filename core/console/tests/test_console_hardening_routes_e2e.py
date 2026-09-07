"""Regression tests for the 2026-09-07 console hardening (adversarial round 1,
F-C4 / F-C5 / F-C9 / SSRF) — driven through the REAL console router with
``TestClient`` and a real session record (auth deps are exercised, not
overridden), under a temp ``CORVIN_HOME``.

Covers:
* ``routes/vibe/context_inspector.py`` — ``/vibe/health`` and ``/vibe/tasks/list``
  are session-gated, read the tenant's chain under CORVIN_HOME (the previous
  ``audit_chain_path`` NameError made the task list permanently empty), and the
  unauthenticated ``/vibe/tasks/debug`` duplicate is gone;
* ``routes/vibe/audit_graph.py`` — ``/audit/graph`` is session-gated and builds
  the DAG from ``rec.tenant_id``'s chain (no hardcoded ``_default`` / repo path);
* ``routes/l5_metrics_api.py`` — mounted at ``/v1/console/metrics/l5`` (the
  double-prefixed ``/v1/console/v1/metrics/l5`` is gone) and the two alert
  mutations require CSRF + tenant match;
* ``routes/custom_provider.py`` — operator-supplied endpoints go through the
  SSRF guard: loopback allowed (Ollama), private/link-local/metadata refused,
  and a refused endpoint is never fetched.
"""
from __future__ import annotations

import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_CONSOLE = _HERE.parent
_REPO = _CONSOLE.parents[1]
for _p in [str(_CONSOLE), str(_REPO)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

pytestmark = pytest.mark.filterwarnings("ignore")


def _reset_modules() -> None:
    for key in list(sys.modules):
        if any(key.startswith(p) for p in ("corvin_console", "corvin_gateway", "forge")):
            del sys.modules[key]


@contextmanager
def _sandbox(tmp_path: Path, tenant_id: str = "_default"):
    """Real console router + real session under a temp CORVIN_HOME."""
    home = tmp_path / "corvin_home"
    tenant_home = home / "tenants" / tenant_id
    for sub in ("auth", "forge", "console/sessions"):
        (tenant_home / "global" / sub).mkdir(parents=True, exist_ok=True)
    prev = {k: os.environ.get(k) for k in ("CORVIN_HOME", "CORVIN_TENANT_ID", "VOICE_AUDIT_PATH")}
    os.environ["CORVIN_HOME"] = str(home)
    os.environ["CORVIN_TENANT_ID"] = tenant_id
    os.environ["VOICE_AUDIT_PATH"] = str(home / "audit.jsonl")
    try:
        _reset_modules()
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from corvin_console import auth as _auth
        from corvin_console.app import router

        rec = _auth.create_session(tenant_id=tenant_id, token_fingerprint="test-fp")
        csrf = _auth.derive_csrf_token(rec.csrf_secret, rec.sid)
        app = FastAPI()
        app.include_router(router, prefix="/v1/console")
        client = TestClient(app, raise_server_exceptions=False)
        yield client, home, rec.sid, csrf
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _reset_modules()


def _write_chain(home: Path, tenant_id: str, events: list[dict]) -> Path:
    chain = home / "tenants" / tenant_id / "global" / "forge" / "audit.jsonl"
    chain.parent.mkdir(parents=True, exist_ok=True)
    chain.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    return chain


_CHAIN = [
    {"event_type": "task.started", "task_id": "task-A", "ts": 100.0,
     "timestamp_utc": "2026-09-07T00:00:01Z", "hash": "h1", "prev_hash": "", "details": {}},
    {"event_type": "task.finished", "task_id": "task-A", "ts": 101.0,
     "timestamp_utc": "2026-09-07T00:00:02Z", "hash": "h2", "prev_hash": "h1", "details": {}},
    {"event_type": "task.started", "task_id": "task-B", "ts": 102.0,
     "timestamp_utc": "2026-09-07T00:00:03Z", "hash": "h3", "prev_hash": "h2", "details": {}},
]


def _authed(client, sid, csrf=None):
    client.cookies.set("corvin_console_sid", sid)
    if csrf:
        client.headers.update({"X-CSRF-Token": csrf})
    return client


class TestVibeContextInspector:
    def test_health_and_task_list_require_a_session(self, tmp_path):
        with _sandbox(tmp_path) as (client, home, sid, csrf):
            assert client.get("/v1/console/vibe/health").status_code == 401
            assert client.get("/v1/console/vibe/tasks/list").status_code == 401

    def test_debug_duplicate_is_gone(self, tmp_path):
        with _sandbox(tmp_path) as (client, home, sid, csrf):
            _authed(client, sid)
            assert client.get("/v1/console/vibe/tasks/debug").status_code == 404

    def test_task_list_reads_the_tenant_chain_under_corvin_home(self, tmp_path):
        with _sandbox(tmp_path) as (client, home, sid, csrf):
            _write_chain(home, "_default", _CHAIN)
            _authed(client, sid)
            r = client.get("/v1/console/vibe/health")
            assert r.status_code == 200 and r.json()["status"] == "ok"
            r = client.get("/v1/console/vibe/tasks/list?limit=20")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["total"] == 2
            assert {t["task_id"] for t in body["tasks"]} == {"task-A", "task-B"}
            assert body["latest_task_id"] == "task-B"
            r = client.get("/v1/console/vibe/task/task-A/context-layers")
            assert r.status_code == 200, r.text
            assert r.json()["task_id"] == "task-A"


class TestVibeAuditGraph:
    def test_requires_a_session(self, tmp_path):
        with _sandbox(tmp_path) as (client, home, sid, csrf):
            assert client.get("/v1/console/audit/graph").status_code == 401

    def test_builds_dag_from_the_session_tenant_chain(self, tmp_path):
        with _sandbox(tmp_path) as (client, home, sid, csrf):
            _write_chain(home, "_default", _CHAIN)
            # a foreign tenant's chain must NOT leak into this session's graph
            _write_chain(home, "other", [{"event_type": "x", "ts": 1.0, "hash": "zz", "prev_hash": ""}])
            _authed(client, sid)
            r = client.get("/v1/console/audit/graph?limit=500")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["total_events"] == 3
            assert {n["id"] for n in body["nodes"]} == {"h1", "h2", "h3"}
            assert {(e["from_node"], e["to_node"]) for e in body["edges"]} == {("h1", "h2"), ("h2", "h3")}
            assert body["critical_path"] == ["h1", "h2", "h3"]

    def test_missing_chain_is_reported_not_500(self, tmp_path):
        with _sandbox(tmp_path) as (client, home, sid, csrf):
            _authed(client, sid)
            r = client.get("/v1/console/audit/graph")
            assert r.status_code == 200
            assert r.json()["anomalies"][0]["type"] == "no_audit_chain"


class TestL5MetricsRouter:
    def test_mounted_under_console_prefix_once(self, tmp_path):
        with _sandbox(tmp_path) as (client, home, sid, csrf):
            _authed(client, sid)
            assert client.get("/v1/console/v1/metrics/l5/status").status_code == 404
            r = client.get("/v1/console/metrics/l5/status")
            assert r.status_code == 200, r.text
            assert "all_healthy" in r.json()
            assert client.get("/v1/console/metrics/l5/alerts").status_code == 200

    def test_status_denies_a_foreign_tenant(self, tmp_path):
        with _sandbox(tmp_path) as (client, home, sid, csrf):
            _authed(client, sid)
            assert client.get("/v1/console/metrics/l5/status?tenant_id=other").status_code == 403

    def test_alert_mutations_require_csrf(self, tmp_path):
        with _sandbox(tmp_path) as (client, home, sid, csrf):
            _authed(client, sid)  # session but NO CSRF header
            for verb in ("acknowledge", "resolve"):
                r = client.post(f"/v1/console/metrics/l5/alerts/a1/{verb}", json={"tenant_id": "_default"})
                assert r.status_code == 403, (verb, r.text)
            _authed(client, sid, csrf)
            for verb in ("acknowledge", "resolve"):
                # passes CSRF + tenant check, then the (empty) monitor says unknown alert
                r = client.post(f"/v1/console/metrics/l5/alerts/a1/{verb}", json={"tenant_id": "_default"})
                assert r.status_code == 404, (verb, r.text)
                r = client.post(f"/v1/console/metrics/l5/alerts/a1/{verb}", json={"tenant_id": "other"})
                assert r.status_code == 403, (verb, r.text)


class TestCustomProviderSsrfGuard:
    @pytest.fixture(autouse=True)
    def _no_network(self, monkeypatch):
        """Any fetch attempt is a test failure — the guard must refuse BEFORE httpx."""
        import httpx

        calls: list[str] = []

        class _Boom:
            def __init__(self, *a, **k):
                calls.append("client")

            async def __aenter__(self):
                raise AssertionError("httpx.AsyncClient was opened for a blocked endpoint")

            async def __aexit__(self, *a):
                return False

        self.calls = calls
        monkeypatch.setattr(httpx, "AsyncClient", _Boom)

    @pytest.mark.parametrize("endpoint", [
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.7/search",
        "http://192.168.1.50:11434/api",
        "http://metadata.google.internal/computeMetadata/v1/",
        "http://[fd00:ec2::254]/",
        "ftp://example.com/x",
        "http://0x7f000001.example.invalid/",
    ])
    def test_blocked_endpoints_are_never_fetched(self, tmp_path, endpoint):
        with _sandbox(tmp_path) as (client, home, sid, csrf):
            _authed(client, sid, csrf)
            r = client.post("/v1/console/custom-provider/test-api",
                            json={"endpoint": endpoint, "method": "GET"})
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["status"] == "failed"
            assert body["error"].startswith("endpoint blocked"), body
            assert self.calls == []

    def test_create_refuses_a_blocked_endpoint(self, tmp_path):
        with _sandbox(tmp_path) as (client, home, sid, csrf):
            _authed(client, sid, csrf)
            r = client.post("/v1/console/custom-provider/create", json={
                "provider_id": "evil", "name": "evil", "endpoint": "http://169.254.169.254/",
            })
            assert r.status_code == 400
            assert "endpoint blocked" in r.json()["detail"]

    def test_loopback_is_allowed_for_local_model_servers(self, tmp_path):
        """Ollama & co. run on the same host — the guard must let loopback through
        (the fetch then reaches httpx, which the fixture turns into a visible call)."""
        with _sandbox(tmp_path) as (client, home, sid, csrf):
            _authed(client, sid, csrf)
            for endpoint in ("http://127.0.0.1:11434/api/tags", "http://localhost:11434/api/tags", "http://[::1]:11434/"):
                before = len(self.calls)
                r = client.post("/v1/console/custom-provider/test-api",
                                json={"endpoint": endpoint, "method": "GET"})
                assert r.status_code == 200
                assert r.json()["status"] == "failed"
                assert not r.json()["error"].startswith("endpoint blocked"), (endpoint, r.json())
                assert len(self.calls) == before + 1, endpoint
