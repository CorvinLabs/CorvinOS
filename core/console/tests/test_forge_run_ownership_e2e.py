"""Run ownership, resume safety and the concurrency cap (ADR-2253 D6, D9, D10; T-0159/T-0162/T-0166).

Through the REAL console router and the real ``forge_runs`` store. A "restart" is simulated the way it really
happens: the persisted record stays on disk under another boot id and this process's in-memory map is empty.
A "second host" is a record whose owner (boot id) is not this process but whose heartbeat is fresh.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_forge_creator_e2e import ScriptedEngine, console  # noqa: E402

from corvin_console import forge_runs  # noqa: E402


def _write_record(client, run_id: str, **fields) -> None:
    rec = {"tenant_id": "_default", "kind": "skill", "status": "running", "phase": "planning",
           "phases": ["planning"], "progress": 5, "message": "working", "engine": "claude_code", "error": None,
           "created_at": "2026-10-11T00:00:00+00:00", "sid_fingerprint": "fp", "boot_id": "another-process",
           "resume": {"user_request": "make a skill that validates json", "base": None}, "resumes": 0}
    rec.update(fields)
    d = client.home / "tenants" / "_default" / "global" / "forge_runs"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{run_id}.json").write_text(json.dumps(rec))


def _read(client, run_id: str) -> dict:
    return json.loads((client.home / "tenants" / "_default" / "global" / "forge_runs" / f"{run_id}.json").read_text())


@pytest.fixture
def spawned(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(forge_runs, "spawn", lambda **kw: calls.append(kw["run_id"]))
    return calls


def test_polling_a_skill_run_through_the_tool_status_route_resumes_nothing(tmp_path, spawned):
    with console(tmp_path, ScriptedEngine()) as client:
        _write_record(client, "run-aaaaaaaaaaaa", kind="skill", heartbeat_at=0)  # orphaned: stale heartbeat
        forge_runs.runs.clear()
        r = client.get("/v1/console/forge-creator/status/run-aaaaaaaaaaaa")
        assert r.status_code == 404
        assert spawned == [], "a caller who may not see the run must not resume it"
        assert _read(client, "run-aaaaaaaaaaaa")["resumes"] == 0 and _read(client, "run-aaaaaaaaaaaa")["status"] == "running"
        # positive control: the route that DOES serve skill runs resumes the same record
        ok = client.get("/v1/console/skill-creator/status/run-aaaaaaaaaaaa")
        assert ok.status_code == 200 and spawned == ["run-aaaaaaaaaaaa"]


def test_retry_tool_on_a_skill_run_is_404_and_resumes_nothing(tmp_path, spawned):
    with console(tmp_path, ScriptedEngine()) as client:
        _write_record(client, "run-bbbbbbbbbbbb", kind="skill", heartbeat_at=0)
        forge_runs.runs.clear()
        assert client.post("/v1/console/forge-creator/tool/retry/run-bbbbbbbbbbbb").status_code == 404
        assert spawned == [] and _read(client, "run-bbbbbbbbbbbb")["resumes"] == 0


def test_a_resumer_that_raises_leaves_the_record_failed_not_running(tmp_path, spawned):
    with console(tmp_path, ScriptedEngine()) as client:
        # a tool record whose (non-empty) resume payload has no 'request': the tool resumer raises KeyError
        _write_record(client, "run-cccccccccccc", kind="tool", heartbeat_at=0, resume={"panel_request": ""})
        forge_runs.runs.clear()
        r = client.get("/v1/console/forge-creator/status/run-cccccccccccc")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "failed" and body["failure_code"] == "resume_failed"
        assert _read(client, "run-cccccccccccc")["status"] == "failed"
        assert spawned == []
        # it no longer counts against the cap
        assert forge_runs.running_count("_default") == 0


def test_a_live_peer_hosts_run_is_not_respawned_nor_failed(tmp_path, spawned):
    with console(tmp_path, ScriptedEngine()) as client:
        _write_record(client, "run-dddddddddddd", kind="tool", heartbeat_at=time.time())  # owner alive
        forge_runs.runs.clear()
        for _ in range(3):
            body = client.get("/v1/console/forge-creator/status/run-dddddddddddd").json()
            assert body["status"] == "running"
        assert spawned == []
        rec = _read(client, "run-dddddddddddd")
        assert rec["status"] == "running" and rec["resumes"] == 0
        # the peer finishes: the next poll shows it (no stale in-memory copy)
        rec.update(status="success", progress=100, message="done by the peer")
        (client.home / "tenants/_default/global/forge_runs/run-dddddddddddd.json").write_text(json.dumps(rec))
        assert client.get("/v1/console/forge-creator/status/run-dddddddddddd").json()["status"] == "success"


def test_a_dead_owner_is_still_recovered(tmp_path, spawned):
    with console(tmp_path, ScriptedEngine()) as client:
        _write_record(client, "run-eeeeeeeeeeee", kind="tool", heartbeat_at=time.time() - 10_000,
                      resume={"request": "count the words in a text", "panel_request": ""})
        forge_runs.runs.clear()
        body = client.get("/v1/console/forge-creator/status/run-eeeeeeeeeeee").json()
        assert spawned == ["run-eeeeeeeeeeee"], "a stale heartbeat means the owner is dead: resume once"
        assert body["status"] == "running"
        assert _read(client, "run-eeeeeeeeeeee")["resumes"] == 1


def test_the_cap_counts_a_live_peers_runs_and_a_resume_obeys_it(tmp_path, spawned):
    with console(tmp_path, ScriptedEngine()) as client:
        for i in range(forge_runs.MAX_RUNNING_PER_TENANT):
            _write_record(client, f"run-{i:012d}", kind="tool", heartbeat_at=time.time())
        forge_runs.runs.clear()
        # starting a new run is refused: the peer's live runs fill the cap
        r = client.post("/v1/console/forge-creator/tool/generate", json={"user_request": "count the words in a text"})
        assert r.status_code == 429, r.text
        # an orphan (dead owner) whose resume would exceed the cap is failed with resume_over_cap; the poll is NOT 429
        _write_record(client, "run-ffffffffffff", kind="tool", heartbeat_at=0,
                      resume={"request": "count the words in a text", "panel_request": ""})
        r = client.get("/v1/console/forge-creator/status/run-ffffffffffff")
        assert r.status_code == 200 and r.json()["status"] == "failed"
        assert r.json()["failure_code"] == "resume_over_cap"
        assert spawned == []
