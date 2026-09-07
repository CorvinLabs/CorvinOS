"""``POST /api/learning/subscribe`` must require CSRF and must be bounded.

Round-4 console review, F5. The route carried ``require_session`` only. Because
the handler reads nothing but query params, a cross-site POST with
``content-type: text/plain;charset=UTF-8`` stays a CORS *simple* request: a real
browser sends it with the console cookie and no preflight, and every such call
allocated a permanent server-side subscriber (the store had no cap and its only
eviction path, ``prune_stale_subscribers``, had zero callers in the whole tree).

These tests drive the REAL router through FastAPI's TestClient with a REAL
console session — no direct calls into the handler.

Run:  .venv/bin/python -m pytest core/console/tests/test_learning_dashboard_subscribe_guard.py
"""
from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[2]
_CONSOLE = _REPO / "core" / "console"
_PLUGINS = _REPO / "core" / "plugins"
for _p in (str(_REPO), str(_CONSOLE), str(_PLUGINS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

TENANT = "_default"
_PURGED = ("corvin_console", "corvin_gateway", "forge")


def _reset_modules(restore: dict | None = None) -> None:
    for key in list(sys.modules):
        if key.startswith(_PURGED):
            del sys.modules[key]
    if restore:
        sys.modules.update(restore)


@contextmanager
def _console(tmp_path: Path):
    home = tmp_path / "corvin_home"
    (home / "tenants" / TENANT / "global" / "auth").mkdir(parents=True)
    (home / "tenants" / TENANT / "global" / "console" / "sessions").mkdir(parents=True)

    keys = ("CORVIN_HOME", "CORVIN_TENANT_ID", "VOICE_AUDIT_PATH")
    prev = {k: os.environ.get(k) for k in keys}
    os.environ["CORVIN_HOME"] = str(home)
    os.environ["CORVIN_TENANT_ID"] = TENANT
    os.environ["VOICE_AUDIT_PATH"] = str(home / "audit.jsonl")
    preloaded = {k: v for k, v in sys.modules.items() if k.startswith(_PURGED)}
    try:
        _reset_modules()
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from corvin_console import auth as _auth
        from corvin_console.app import router

        rec = _auth.create_session(tenant_id=TENANT, token_fingerprint="sub-test-fp")
        csrf = _auth.derive_csrf_token(rec.csrf_secret, rec.sid)

        app = FastAPI()
        app.include_router(router, prefix="/v1/console")
        client = TestClient(app, raise_server_exceptions=False)
        client.cookies.set("corvin_console_sid", rec.sid)
        yield client, csrf
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _reset_modules(restore=preloaded)


SUBSCRIBE = "/v1/console/api/learning/subscribe"


def test_cross_site_simple_post_without_csrf_is_refused(tmp_path):
    """The exact shape a browser can send cross-site: no token, simple content-type."""
    with _console(tmp_path) as (client, _csrf):
        res = client.post(
            SUBSCRIBE,
            headers={
                "Origin": "https://evil.example",
                "content-type": "text/plain;charset=UTF-8",
            },
        )
        assert res.status_code == 403, res.text
        assert "subscriber_id" not in res.text


def test_unsubscribe_also_requires_csrf(tmp_path):
    with _console(tmp_path) as (client, _csrf):
        res = client.post(
            f"/v1/console/api/learning/unsubscribe?subscriber_id=whatever",
            headers={"content-type": "text/plain;charset=UTF-8"},
        )
        assert res.status_code == 403, res.text


def test_subscribe_with_csrf_still_works(tmp_path):
    with _console(tmp_path) as (client, csrf):
        res = client.post(SUBSCRIBE, headers={"X-CSRF-Token": csrf})
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["subscriber_id"]
        assert body["tenant_id"] == TENANT

        # ...and the subscriber can be released again.
        gone = client.post(
            f"/v1/console/api/learning/unsubscribe?subscriber_id={body['subscriber_id']}",
            headers={"X-CSRF-Token": csrf},
        )
        assert gone.status_code == 200, gone.text


def test_subscriber_store_is_capped_and_refuses_with_503(tmp_path):
    """Server state must be bounded: the cap refuses, it does not silently grow."""
    with _console(tmp_path) as (client, csrf):
        from core.learning import dashboard as dash

        ok = 0
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(dash, "MAX_SUBSCRIBERS_PER_TENANT", 3)
            for _ in range(3):
                res = client.post(SUBSCRIBE, headers={"X-CSRF-Token": csrf})
                assert res.status_code == 200, res.text
                ok += 1
            over = client.post(SUBSCRIBE, headers={"X-CSRF-Token": csrf})

        assert ok == 3
        assert over.status_code == 503, over.text
        assert "subscriber limit reached" in over.json()["detail"]


# ── round-4 F11: the per-skill route served hard-coded nulls ────────────────


def test_skill_metrics_are_computed_from_real_events(tmp_path):
    """``/skills/{name}`` reports the tenant's REAL recorded events.

    Every field used to be a hard-coded ``None``/0 behind a docstring promising
    accuracy, latency, confidence, satisfaction and usage count.
    """
    with _console(tmp_path) as (client, csrf):
        from core.learning.event_store import EventStore
        from core.learning.learning_events import EventType, LearningEvent
        from core.paths.tenant import tenant_home

        store = EventStore(tenant_home(TENANT))
        skill = "os.delegation_router"

        def _write(event_type, signal):
            store.write_event(
                LearningEvent.create(
                    event_type=event_type, skill_id=skill, tenant_id=TENANT,
                    signal=signal, lom="core/console/tests/test_learning_dashboard_subscribe_guard.py",
                )
            )

        _write(EventType.SKILL_EXECUTED, {"latency_ms": 100.0})
        _write(EventType.SKILL_EXECUTED, {"latency_ms": 300.0})
        _write(EventType.OUTCOME, {"success": True})
        _write(EventType.OUTCOME, {"success": False})
        _write(EventType.CONFIDENCE, {"confidence": 0.8})
        _write(EventType.FEEDBACK, {"quality_rating": 4.0})

        res = client.get(f"/v1/console/api/learning/skills/{skill}")
        assert res.status_code == 200, res.text
        data = res.json()["data"]

        assert data["skill_name"] == skill
        assert data["usage_count"] == 2
        assert data["accuracy"] == pytest.approx(0.5)
        assert data["latency_ms"] == pytest.approx(200.0)
        assert data["confidence"] == pytest.approx(0.8)
        assert data["user_satisfaction"] == pytest.approx(4.0)
        assert data["last_updated"] is not None


def test_a_skill_with_no_events_reports_null_not_zero(tmp_path):
    with _console(tmp_path) as (client, csrf):
        res = client.get("/v1/console/api/learning/skills/os.delegation_router")
        assert res.status_code == 200, res.text
        data = res.json()["data"]
        assert data["accuracy"] is None
        assert data["latency_ms"] is None
        assert data["confidence"] is None
        assert data["user_satisfaction"] is None


def test_user_route_states_that_the_dimension_is_not_recorded(tmp_path):
    """Honest 'not recorded' beats a null that looks like a measured zero."""
    with _console(tmp_path) as (client, csrf):
        res = client.get("/v1/console/api/learning/user/someone")
        assert res.status_code == 200, res.text
        data = res.json()["data"]
        assert data["available"] is False
        assert "no user dimension" in data["reason"]


def test_summary_reports_the_real_cache_state(tmp_path):
    """``cached`` was hard-coded True regardless of whether the cache was hit."""
    with _console(tmp_path) as (client, csrf):
        first = client.get("/v1/console/api/learning/summary")
        assert first.status_code == 200, first.text
        assert first.json()["cached"] is False

        second = client.get("/v1/console/api/learning/summary")
        assert second.status_code == 200, second.text
        assert second.json()["cached"] is True
