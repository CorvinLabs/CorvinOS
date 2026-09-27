"""Regression tests for console modules that have NO production caller.

Adversarial review 2026-09-27, operator decision "defuse, keep": each module
below stays in the tree, marked ``NOT WIRED``, but must (1) import, and
(2) never report fabricated success/health — it fails closed instead. Each
test here failed (import error, or a fabricated value) before the defuse.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from corvin_console import deps


def _client(router, *, prefix: str = "") -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix=prefix)
    rec = SimpleNamespace(tenant_id="_default", sid="s", sid_fingerprint="fp", tier="owner")
    app.dependency_overrides[deps.require_session] = lambda: rec
    app.dependency_overrides[deps.require_csrf] = lambda: rec
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("modname", [
    "corvin_console.routes.skills_observability_api",
    "corvin_console.routes.skills_v2",
    "corvin_console.routes.context_engineering",
    "corvin_console.routes.learning_loops",
    "corvin_console.task_completion_orchestrator",
    "corvin_console.message_completeness_protocol",
    "corvin_console.services.voice_session_store",
    "corvin_console.services.performance_monitor",
    "corvin_console.models.voice_session",
    "core.console.routes.learning_dashboard",
])
def test_module_imports_and_is_marked_not_wired(modname):
    import importlib

    mod = importlib.import_module(modname)
    assert "NOT WIRED" in (mod.__doc__ or ""), modname


def test_skills_observability_fails_closed():
    from corvin_console.routes import skills_observability_api as m

    c = _client(m.router)
    for path in ("/metrics/latency", "/metrics/confidence", "/metrics/feedback",
                 "/metrics/ab-tests", "/export/csv"):
        r = c.get(f"/v1/skills-observability{path}")
        assert r.status_code == 501, (path, r.text)
    assert c.get("/v1/skills-observability/health").json()["status"] == "not_implemented"


def test_skills_observability_needs_a_session():
    from corvin_console.routes import skills_observability_api as m

    app = FastAPI()
    app.include_router(m.router)
    r = TestClient(app).get("/v1/skills-observability/metrics/latency")
    assert r.status_code == 401


def test_skills_v2_no_mock_install_or_status():
    from corvin_console.routes import skills_v2 as m

    c = _client(m.router)
    assert c.post("/skills/install", json={"skill_id": "os.x", "source": "marketplace"}).status_code == 501
    assert c.get("/skills/status", params={"task_id": "install-x-1"}).status_code == 501
    assert c.get("/skills/installed").status_code == 501


def test_learning_dashboard_routes_fail_closed():
    from core.console.routes import learning_dashboard as m

    c = TestClient(_app := FastAPI(), raise_server_exceptions=False)
    _app.include_router(m.router)
    r = c.post("/api/v1/console/learning/feedback",
               json={"skill_id": "os.x", "task_id": "t", "quality_rating": 5})
    assert r.status_code == 501  # used to answer "accepted" without storing anything
    assert c.get("/api/v1/console/learning/learning-loop/status").status_code == 501


def test_message_envelope_never_claims_unverified_context(monkeypatch):
    from corvin_console import message_completeness_protocol as m

    producer = SimpleNamespace(
        create_snapshot=lambda **k: SimpleNamespace(content_hash="h"),
        emit_bridge_event=lambda **k: (_ for _ in ()).throw(RuntimeError("chain down")),
    )
    gate = m.MessageCompletenessGate(producer=producer)
    env = gate.finalize_turn_with_context(
        assistant_response="a", user_message="u", turn_number=1,
        task_id="t", session_id="s", tenant_id="_default",
        last_message_hash="x", conversation_turn_count=1,
        worktree_path="/w", base_commit="c", phase_name="p",
    )
    audit = env.session_state["audit_trail"]
    assert audit["context_verified"] is False
    assert audit["bridge_event_id"] == ""
    with pytest.raises(NotImplementedError):
        m.TurnContextExtractor.extract_phase_name(None)


def test_voice_store_persistent_mode_is_refused_and_tenant_limit_is_correct():
    from corvin_console.services import voice_session_store as m

    with pytest.raises(NotImplementedError):
        m.SQLiteStore()
    store = m.InMemoryStore()
    for i in range(3):
        store.create_session(f"other-{i}", "acme")
    store.create_session("mine", "_default")
    assert [s["session_id"] for s in store.list_sessions("_default", limit=1)] == ["mine"]


def test_performance_sla_unmeasured_is_none_and_zero_latency_is_compliant():
    from corvin_console.services.performance_monitor import PerformanceMonitor

    pm = PerformanceMonitor()
    pm.record_latency("db", 0.0)
    sla = pm.get_sla_status()
    assert sla["db"]["p99_under_100ms"] is True
    assert sla["stt"]["p99_under_500ms"] is None


def test_orchestration_imports_and_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    from corvin_console.routes import orchestration as m

    assert "NOT WIRED" in m.__doc__
    c = _client(m.router, prefix="/v1/console")
    assert c.get("/v1/console/orchestration/status").status_code == 501
    assert c.post("/v1/console/orchestration/approve", json={"phase": "PHASE_1_TO_2A"}).status_code == 501
    r = c.get("/v1/console/orchestration/history")
    assert r.status_code == 200 and r.json() == {"history": []}


def test_stream_c_reports_no_data_and_unknown_applied():
    from corvin_console.routes.stream_c_dashboard import StreamCDashboard

    d = StreamCDashboard(tenant_id="_default")
    assert d.panel_trend_gauge()["data"]["status"] == "no_data"  # was "stable"
    d.ingest_optimizer_config(SimpleNamespace(tenant_id="_default", skill_id="os.x", config_delta=0.1))
    assert d.panel_config_delta_chart()["data"] == [
        {"skill_id": "os.x", "config_delta": 0.1, "applied": None}]  # was True


def test_learning_stream_refuses_unauthenticated_and_is_not_implemented(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    from starlette.websockets import WebSocketDisconnect
    from corvin_console.routes import learning_stream as m

    assert "NOT WIRED" in m.__doc__
    app = FastAPI()
    app.include_router(m.router)
    c = TestClient(app)
    with c.websocket_connect("/v1/console/learning/stream") as ws:
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
    assert exc.value.code == 4401
