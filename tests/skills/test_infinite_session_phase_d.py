"""Phase D — dashboard API over the REAL router (ADR-0545).

Every test goes through FastAPI's TestClient against
``corvin_console.routes.infinite_session_api.router``; only the auth
dependencies (``require_session`` / ``require_csrf``) are overridden. The
data under test is written through the real :class:`EventStore` into a
temporary ``CORVIN_HOME`` — there is no second layout to seed.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "core" / "console"))

from corvin_console import auth as session_auth  # noqa: E402
from corvin_console import deps as console_deps  # noqa: E402
from corvin_console.routes import infinite_session_api as api  # noqa: E402

from core.infinite_session import EventStore, Snapshot, SnapshotType, snapshot_task_state  # noqa: E402

TENANT = "_default"
OTHER = "tenant_b"


def _record(tenant_id: str = TENANT) -> session_auth.SessionRecord:
    import dataclasses
    values: dict[str, object] = {}
    for f in dataclasses.fields(session_auth.SessionRecord):
        if f.default is not dataclasses.MISSING:
            continue
        ann = str(f.type)
        if "float" in ann:
            values[f.name] = 2_000_000.0
        elif "bool" in ann:
            values[f.name] = False
        elif f.name == "tier":
            values[f.name] = "owner"
        elif f.name == "tenant_id":
            values[f.name] = tenant_id
        else:
            values[f.name] = f"test-{f.name}"
    return session_auth.SessionRecord(**values)  # type: ignore[arg-type]


def _recording_audit(sink: list):
    def _emit(event_type, *, tenant_id, details):
        sink.append((event_type, tenant_id, dict(details)))
        return f"ref-{len(sink)}"
    return _emit


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    monkeypatch.setenv("CORVIN_TENANT_ID", TENANT)
    return tmp_path


@pytest.fixture
def console_audit():
    with patch.object(api, "console_audit") as mock:
        yield mock


def _client(rec: session_auth.SessionRecord, *, csrf_ok: bool = True) -> TestClient:
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[console_deps.require_session] = lambda: rec
    if csrf_ok:
        app.dependency_overrides[console_deps.require_csrf] = lambda: rec
    return TestClient(app)


def _seed(task_id: str, states: list[dict], tenant_id: str = TENANT) -> list[Snapshot]:
    """Seed a chain through the real store (audit recorded, not core chain)."""
    store = EventStore(tenant_id, audit=_recording_audit([]))
    out = []
    for i, state in enumerate(states):
        snap, err = snapshot_task_state(
            tenant_id, task_id, state, phase_id=f"phase_{i}", store=store
        )
        assert err == "", err
        out.append(snap)
    return out


# ── GET /tasks ────────────────────────────────────────────────────────────


def test_list_tasks_empty(home, console_audit):
    res = _client(_record()).get("/api/infinite-session/tasks")
    assert res.status_code == 200
    body = res.json()
    assert body["tasks"] == [] and body["total_count"] == 0 and body["tenant_id"] == TENANT


def test_list_tasks_reads_event_store_layout(home, console_audit):
    _seed("task_a", [{"x": 0.5}, {"x": 0.52}])
    _seed("task_b", [{"y": 1}])
    body = _client(_record()).get("/api/infinite-session/tasks").json()
    assert body["total_count"] == 2
    by_id = {t["task_id"]: t for t in body["tasks"]}
    assert by_id["task_a"]["checkpoint_count"] == 2
    assert by_id["task_a"]["phase_id"] == "phase_1"
    assert by_id["task_a"]["status"] == "active"
    assert by_id["task_a"]["current_drift_level"] in {"NORMAL", "WARNING", "CRITICAL"}
    # no parallel layout was created
    assert not (home / "tenants" / TENANT / "infinite_session" / "snapshots" / "tasks").exists()


def test_list_tasks_is_tenant_isolated(home, console_audit):
    _seed("secret_task", [{"x": 1}], tenant_id=OTHER)
    body = _client(_record(TENANT)).get("/api/infinite-session/tasks").json()
    assert body["total_count"] == 0
    body = _client(_record(OTHER)).get("/api/infinite-session/tasks").json()
    assert [t["task_id"] for t in body["tasks"]] == ["secret_task"]


# ── GET /task/{id}/history ────────────────────────────────────────────────


def test_history_returns_full_chain(home, console_audit):
    snaps = _seed("task_h", [{"n": 1}, {"n": 2}, {"n": 3}])
    res = _client(_record()).get("/api/infinite-session/task/task_h/history")
    assert res.status_code == 200
    body = res.json()
    assert body["checkpoint_count"] == 3 and body["chain_valid"] is True
    assert [c["checkpoint_id"] for c in body["checkpoints"]] == [s.snapshot_id for s in snaps]
    assert body["checkpoints"][1]["prev_snapshot_hash"] == snaps[0].content_hash
    assert body["current_config"] == {"n": 3}
    assert body["checkpoints"][0]["config_state"] == {"n": 1}


def test_history_404_for_unknown_task(home, console_audit):
    assert _client(_record()).get("/api/infinite-session/task/nope/history").status_code == 404


def test_history_cross_tenant_is_404(home, console_audit):
    _seed("task_x", [{"a": 1}], tenant_id=OTHER)
    assert _client(_record(TENANT)).get("/api/infinite-session/task/task_x/history").status_code == 404


@pytest.mark.parametrize("bad", ["..", "a..b", "a%2Fb", "a%2F..%2Fb", "x y", "a;b"])
def test_history_rejects_traversal_ids(home, console_audit, bad):
    res = _client(_record()).get(f"/api/infinite-session/task/{bad}/history")
    # 422 = Pydantic pattern; 400 = store id validator ('..' inside an otherwise valid id)
    assert res.status_code in (400, 404, 422), (bad, res.status_code)
    assert not (home / "tenants").exists() or not list((home / "tenants").rglob("*.json"))


def test_history_detects_tampered_chain(home, console_audit):
    snaps = _seed("task_t", [{"v": 1}, {"v": 2}])
    path = home / "tenants" / TENANT / "infinite_session" / "snapshots" / "task_t" / f"{snaps[1].snapshot_id}.json"
    data = json.loads(path.read_text())
    data["state_dict"]["v"] = 99
    path.write_text(json.dumps(data))
    res = _client(_record()).get("/api/infinite-session/task/task_t/history")
    # A hash-mismatching snapshot cannot be loaded at all (fail-closed)
    assert res.status_code == 500


# ── GET /task/{id}/context-diff ───────────────────────────────────────────


def test_context_diff(home, console_audit):
    a, b = _seed("task_d", [{"keep": 1, "mod": "old", "gone": True}, {"keep": 1, "mod": "new", "added": 5}])
    res = _client(_record()).get(
        "/api/infinite-session/task/task_d/context-diff",
        params={"from_checkpoint": a.snapshot_id, "to_checkpoint": b.snapshot_id},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["additions"] == {"added": 5}
    assert body["removals"] == {"gone": True}
    assert body["modifications"] == {"mod": {"before": "old", "after": "new"}}
    assert body["timestamp"] == b.timestamp


def test_context_diff_rejects_traversal_and_unknown(home, console_audit):
    a, = _seed("task_e", [{"k": 1}])
    c = _client(_record())
    res = c.get("/api/infinite-session/task/task_e/context-diff",
                params={"from_checkpoint": "../../etc/passwd", "to_checkpoint": a.snapshot_id})
    assert res.status_code == 422
    res = c.get("/api/infinite-session/task/task_e/context-diff",
                params={"from_checkpoint": "unknown", "to_checkpoint": a.snapshot_id})
    assert res.status_code == 404


# ── POST /task/{id}/revert ────────────────────────────────────────────────


def _revert(client: TestClient, task_id: str, target: str, body_task: str | None = None):
    return client.post(
        f"/api/infinite-session/task/{task_id}/revert",
        json={"task_id": body_task or task_id, "target_checkpoint_id": target, "reason": "operator decision"},
    )


def test_revert_requires_csrf(home, console_audit):
    a, b = _seed("task_r", [{"v": 1}, {"v": 2}])
    # no require_csrf override → the real dependency runs → 401 without cookie
    res = _revert(_client(_record(), csrf_ok=False), "task_r", a.snapshot_id)
    assert res.status_code == 401
    # nothing written
    assert EventStore(TENANT, audit=_recording_audit([])).list_snapshots(TENANT, "task_r")[0].__len__() == 2


def test_revert_appends_chained_recovery_snapshot(home, console_audit):
    a, b = _seed("task_r2", [{"v": 1}, {"v": 2}])
    res = _revert(_client(_record()), "task_r2", a.snapshot_id)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["success"] is True and body["reverted_to_checkpoint"] == a.snapshot_id
    new_id = body["new_checkpoint_id"]
    assert new_id and new_id != a.snapshot_id and body["transaction_id"]

    store = EventStore(TENANT, audit=_recording_audit([]))
    index, _ = store.list_snapshots(TENANT, "task_r2")
    assert [m.snapshot_id for m in index][-1] == new_id and len(index) == 3
    head, _ = store.get_latest_snapshot(TENANT, "task_r2")
    assert head.state_dict == {"v": 1}
    assert head.prev_snapshot_hash == b.content_hash
    assert head.snapshot_type == SnapshotType.ROLLBACK_RECOVERY
    assert store.verify_snapshot_chain(TENANT, "task_r2") == (True, "")

    # core chain: audit-first snapshot_created for the recovery snapshot
    from core.learning.event_persistence import _resolve_core_audit
    chain_path = Path(_resolve_core_audit().audit_path())
    assert chain_path.is_relative_to(home)  # never the live root
    chain = chain_path.read_text()
    assert "infinite_session.snapshot_created" in chain and new_id in chain
    assert '"v": 1' not in chain and '"state_dict"' not in chain  # content-free

    # console audit: action_performed, fingerprint not user id, no reason
    kwargs = console_audit.action_performed.call_args.kwargs
    assert kwargs["action"] == "infinite_session.revert" and kwargs["target_id"] == "task_r2"
    assert kwargs["sid_fingerprint"] == "test-sid_fingerprint"
    assert "reason" not in kwargs

    # rollback transaction log is chained + verifiable
    from core.infinite_session import RollbackManager
    rm = RollbackManager(TENANT)
    assert rm.verify_chain_integrity(TENANT) == (True, None)
    assert rm.get_transaction_history(TENANT)[0]["status"] == "committed"

    # dashboard now reports the task as reverted
    tasks = _client(_record()).get("/api/infinite-session/tasks").json()["tasks"]
    assert tasks[0]["status"] == "reverted" and tasks[0]["checkpoint_count"] == 3


def test_revert_unknown_target_404_and_audited(home, console_audit):
    _seed("task_r3", [{"v": 1}])
    res = _revert(_client(_record()), "task_r3", "does-not-exist")
    assert res.status_code == 404
    assert console_audit.action_failed.call_args.kwargs["reason"] == "target_checkpoint_not_found"
    console_audit.action_performed.assert_not_called()


def test_revert_already_at_head_409(home, console_audit):
    a, = _seed("task_r4", [{"v": 1}])
    assert _revert(_client(_record()), "task_r4", a.snapshot_id).status_code == 409


def test_revert_body_path_mismatch_400(home, console_audit):
    a, b = _seed("task_r5", [{"v": 1}, {"v": 2}])
    assert _revert(_client(_record()), "task_r5", a.snapshot_id, body_task="other").status_code == 400


@pytest.mark.parametrize("bad", ["../../x", "a/b", "", "x" * 129])
def test_revert_rejects_bad_ids(home, console_audit, bad):
    _seed("task_r6", [{"v": 1}, {"v": 2}])
    res = _revert(_client(_record()), "task_r6", bad)
    assert res.status_code == 422


def test_revert_cross_tenant_refused(home, console_audit, monkeypatch):
    a, b = _seed("task_r7", [{"v": 1}, {"v": 2}], tenant_id=OTHER)
    # tenant_b's session can see the target; _default's cannot (404, nothing written)
    res = _revert(_client(_record(TENANT)), "task_r7", a.snapshot_id)
    assert res.status_code == 404
    store = EventStore(OTHER, audit=_recording_audit([]))
    assert len(store.list_snapshots(OTHER, "task_r7")[0]) == 2


# ── GET /health ───────────────────────────────────────────────────────────


def test_health_counts_and_verifies(home, console_audit):
    _seed("task_hc", [{"v": 1}, {"v": 2}])
    body = _client(_record()).get("/api/infinite-session/health").json()
    assert body["status"] == "healthy"
    assert body["total_tasks"] == 1 and body["total_checkpoints"] == 2
    assert body["errors"] == []


def test_health_degraded_on_broken_chain(home, console_audit):
    snaps = _seed("task_hb", [{"v": 1}, {"v": 2}])
    index = home / "tenants" / TENANT / "infinite_session" / "snapshots" / "task_hb" / "index.json"
    data = json.loads(index.read_text())
    data[1]["content_hash"] = "0" * 64
    index.write_text(json.dumps(data))
    body = _client(_record()).get("/api/infinite-session/health").json()
    assert body["status"] == "degraded" and body["event_store"] == "degraded"
    assert any(e.startswith("chain:task_hb") for e in body["errors"])


def test_all_routes_require_session(home):
    app = FastAPI()
    app.include_router(api.router)
    c = TestClient(app)
    for path in ("/api/infinite-session/tasks", "/api/infinite-session/task/t/history",
                 "/api/infinite-session/health"):
        assert c.get(path).status_code == 401, path
    assert c.post("/api/infinite-session/task/t/revert",
                  json={"task_id": "t", "target_checkpoint_id": "x", "reason": "r"}).status_code == 401
