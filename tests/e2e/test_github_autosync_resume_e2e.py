"""GitHub auto-sync survives a restart of the host that actually serves the
console.

Until 2026-09-20 the boot-time resume lived only in ``corvin_console.app``'s
lifespan. The live host is ``corvin_gateway.app`` (service ``corvin-webui``),
which includes the console's ROUTER and static files but never runs its
lifespan — so every gateway restart stopped the sync worker for good while
the GitHub page kept saying "resumes on its own after a server restart". On
the maintainer host the last sync was 2026-09-10; the worker read
``running: false, sync_count: 0`` on 2026-09-20 with ``auto_sync: true`` in
the config.

1. ``resume_auto_sync(home)`` starts exactly the tenants whose
   ``github-config.json`` says ``auto_sync: true`` — and the worker then
   reports ``running`` over the real HTTP route;
2. reachability: BOTH hosts' lifespans call it (positive control on the
   source, so a future refactor that drops the call fails here, not in
   production).
"""
from __future__ import annotations

import dataclasses
import inspect
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.console.corvin_console import auth as session_auth
from core.console.corvin_console import deps as console_deps


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
    for tid, auto in (("_default", True), ("paused", False), ("nocfg", None)):
        d = h / "tenants" / tid
        d.mkdir(parents=True)
        if auto is not None:
            (d / "github-config.json").write_text(json.dumps({
                "url": "https://github.com/example/tenant", "auto_sync": auto, "owner": "example", "repo": "tenant",
            }))
    monkeypatch.setenv("CORVIN_HOME", str(h))
    return h


@pytest.fixture
def workers(monkeypatch):
    """Isolated worker registry; never lets a test thread reach GitHub."""
    from core.console.corvin_console.routes import github_sync as gs

    monkeypatch.setattr(gs, "_workers", {})
    monkeypatch.setattr(gs.GitHubSyncWorker, "_do_sync", lambda self, config: None)
    monkeypatch.setattr(gs.GitHubSyncWorker, "_log_audit", lambda self, *a, **k: None)
    yield gs
    for w in list(gs._workers.values()):
        w.running = False


def test_resume_starts_only_auto_sync_tenants_and_the_route_reports_running(home, workers, monkeypatch):
    gs = workers
    monkeypatch.setattr(gs, "_tenant_path", lambda tid: home / "tenants" / tid)
    assert gs.resume_auto_sync(home) == 1
    assert gs.get_worker("_default").running is True
    assert "paused" not in gs._workers and "nocfg" not in gs._workers

    # idempotent: a second boot-resume does not start a second thread
    assert gs.resume_auto_sync(home) == 0

    from core.console.corvin_console.routes import github as gh

    app = FastAPI()
    app.include_router(gh.router, prefix="/v1/console")
    app.dependency_overrides[console_deps.require_session] = lambda: _fake_session_record("_default")
    with TestClient(app) as c:
        st = c.get("/v1/console/github/worker/status")
        assert st.status_code == 200, st.text
        assert st.json()["running"] is True and st.json()["interval_seconds"] == 300


def test_resume_without_tenants_is_a_noop(tmp_path, workers):
    assert workers.resume_auto_sync(tmp_path / "nothing") == 0


def test_both_hosts_call_the_resume_from_their_lifespan():
    """Reachability: the gateway is the live host; the console lifespan is
    what ``corvinos-serve`` runs. A grep on the source is the positive control
    — it fails the moment either call site is dropped."""
    import core.console.corvin_console.app as console_app
    import corvin_gateway.app as gateway_app

    gw = inspect.getsource(gateway_app._lifespan)
    assert "resume_auto_sync" in gw and "github_sync" in gw
    con = inspect.getsource(console_app.create_app)
    assert "resume_auto_sync" in con
