"""Regression tests for REVIEW-0051 round 1 console findings (B-01 .. B-10) and the run
lifecycle routes added with them (cancel, stream re-attach)."""
from __future__ import annotations

import json
import threading
import time

import pytest
import yaml

from test_workflow_dag_console_e2e import (  # noqa: F401
    _make, _paused_at, _rid, _stream_in_thread, _stub_factory, _wait,
)
from test_workflow_resume_hardening import _sandbox  # noqa: F401

APPROVAL = yaml.safe_dump({
    "awp": "1.1.0", "workflow": {"name": "appr", "description": "d"},
    "orchestration": {"engine": "dag", "graph": [
        {"id": "gate", "type": "approval", "depends_on": [], "message": "go?"},
        {"id": "after", "type": "merge", "depends_on": ["gate"], "strategy": "concat_list",
         "inputs": ["gate.output"], "output": "o"},
    ]}}, sort_keys=False)


@pytest.fixture
def env(tmp_path):
    with _sandbox(tmp_path) as (client, home, tid):
        from corvin_console.routes import workflow_dag_run as d
        old = d.ENGINE_FACTORY
        d.ENGINE_FACTORY = _stub_factory()
        _make(home, tid, "appr", APPROVAL)
        try:
            yield client, home, tid
        finally:
            d.ENGINE_FACTORY = old


def _paused_run(client):
    events: list = []
    t = _stream_in_thread(client, "appr", events)
    rid = _rid(events)
    _paused_at(client, "appr", rid, "gate")
    return rid, events, t


def _ckpt_exists(rid, tid):
    from corvin_workflows import checkpoint as c
    return c.load(rid, tenant_id=tid) is not None


def test_b01_node_routes_enforce_the_yaml_size_limit(env):
    client, _, _ = env
    node = {"id": "big", "type": "code", "language": "python3", "depends_on": [],
            "source": "def main():\n    return {'a': 1}\n# " + "x" * 300_000, "outputs": ["a"]}
    assert client.post("/v1/console/workflows/appr/nodes", json={"node": node}).status_code == 413


def test_b02_concurrent_node_adds_are_not_lost(env):
    client, _, _ = env
    codes: list[int] = []

    def add(i):
        node = {"id": f"m{i}", "type": "merge", "depends_on": [], "strategy": "concat_list",
                "inputs": ["input.x"], "output": "o"}
        codes.append(client.post("/v1/console/workflows/appr/nodes", json={"node": node}).status_code)

    ts = [threading.Thread(target=add, args=(i,)) for i in range(12)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert codes.count(200) == 12
    got = {n["id"] for n in client.get("/v1/console/workflows/appr").json()["graph"]}
    assert {f"m{i}" for i in range(12)} <= got


def test_b10_yaml_aliases_are_rejected(env):
    client, _, _ = env
    text = APPROVAL.replace("message: go?", "message: &m go?\n        ") + ""
    bomb = ("awp: '1.1.0'\nworkflow: {name: a, description: d}\norchestration:\n  engine: dag\n  graph:\n"
            "    - &n {id: x, type: approval, depends_on: []}\n    - *n\n")
    r = client.put("/v1/console/workflows/appr/yaml", json={"yaml": bomb})
    assert r.status_code == 400 and "alias" in r.text.lower()


def test_b05_deleting_a_workflow_removes_its_paused_checkpoints(env):
    client, _, tid = env
    rid, events, t = _paused_run(client)
    assert _ckpt_exists(rid, tid)
    assert client.delete("/v1/console/workflows/appr").status_code == 200
    assert not _ckpt_exists(rid, tid)


def test_b06_typed_reply_to_an_approval_is_refused(env):
    client, _, _ = env
    rid, events, t = _paused_run(client)
    r = client.post(f"/v1/console/workflows/appr/runs/{rid}/resume", json={"reply": "approve"})
    assert r.status_code == 409
    assert client.post(f"/v1/console/workflows/appr/runs/{rid}/approve", json={"comment": ""}).status_code == 200
    t.join(30)


def test_b04_delete_unknown_run_is_404_and_deleting_a_paused_run_drops_its_checkpoint(env):
    client, _, tid = env
    assert client.delete("/v1/console/workflows/appr/runs/deadbeefdeadbeef").status_code == 404
    assert client.delete("/v1/console/workflows/appr/runs/not-a-rid").status_code == 404
    rid, events, t = _paused_run(client)
    assert client.delete(f"/v1/console/workflows/appr/runs/{rid}").status_code == 200
    assert not _ckpt_exists(rid, tid)
    assert client.get(f"/v1/console/workflows/appr/runs/{rid}").status_code == 404


def test_b07_stale_running_meta_holds_no_concurrency_slot(env):
    client, home, tid = env
    from corvin_console.routes import workflows as w
    meta = w._run_meta_path(tid, "appr", "aaaaaaaaaaaaaaaa")
    w._write_atomic(meta, {"rid": "aaaaaaaaaaaaaaaa", "wid": "appr", "status": "running",
                           "started_at": time.time() - 3 * 3600, "dry_run": False})
    assert w._count_running_workflows(tid) == 0
    w._write_atomic(meta, {"rid": "aaaaaaaaaaaaaaaa", "wid": "appr", "status": "running",
                           "started_at": time.time() - 60, "dry_run": False})
    assert w._count_running_workflows(tid) == 1


def test_b07_expired_pause_is_ended_when_the_run_is_read(env):
    client, _, tid = env
    rid, events, t = _paused_run(client)
    from corvin_console.routes import workflows as w
    mp = w._run_meta_path(tid, "appr", rid)
    meta = json.loads(mp.read_text())
    meta["awaiting"]["expires_at_ms"] = int(time.time() * 1000) - 1
    mp.write_text(json.dumps(meta))
    got = client.get(f"/v1/console/workflows/appr/runs/{rid}").json()["run"]
    assert got["status"] == "failed" and got["error"] == "pause expired"
    t.join(10)


def test_cancel_a_paused_run_ends_it_and_removes_the_checkpoint(env):
    client, _, tid = env
    rid, events, t = _paused_run(client)
    r = client.post(f"/v1/console/workflows/appr/runs/{rid}/cancel")
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    t.join(10)
    assert events[-1]["type"] == "run_completed" and events[-1]["error"] == "cancelled"
    assert not _ckpt_exists(rid, tid)
    assert client.post(f"/v1/console/workflows/appr/runs/{rid}/cancel").status_code == 409
    assert client.post("/v1/console/workflows/appr/runs/zz/cancel").status_code == 404


def test_stream_reattach_replays_a_paused_run_and_follows_to_the_end(env):
    client, _, _ = env
    rid, events, t = _paused_run(client)
    t.join(0.1)  # first stream may still be open; a second one must work independently
    got: list = []

    def attach():
        with client.stream("GET", f"/v1/console/workflows/appr/runs/{rid}/stream") as r:
            for line in r.iter_lines():
                if line.startswith("data: "):
                    got.append(json.loads(line[6:]))

    a = threading.Thread(target=attach, daemon=True)
    a.start()
    _wait(lambda: any(e.get("type") == "node_awaiting_approval" for e in got))
    client.post(f"/v1/console/workflows/appr/runs/{rid}/approve", json={"comment": ""})
    a.join(30)
    assert not a.is_alive() and got[-1]["type"] == "run_completed" and got[-1]["ok"] is True


def test_b09_media_zip_skips_symlinks(env, tmp_path):
    client, _, tid = env
    from corvin_console.routes import workflows as w
    secret = tmp_path / "secret.png"
    secret.write_bytes(b"SECRET")
    art = w._run_artifacts_dir(tid, "appr", "bbbbbbbbbbbbbbbb")
    art.mkdir(parents=True, exist_ok=True)
    (art / "real.png").write_bytes(b"REAL")
    (art / "evil.png").symlink_to(secret)
    r = client.get("/v1/console/workflows/appr/runs/bbbbbbbbbbbbbbbb/media.zip")
    import io, zipfile
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert names == ["real.png"]
