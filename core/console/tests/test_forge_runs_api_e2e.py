"""The run API (ADR-2253 D4; T-0148): list, detail, events - tenant-scoped, allowlisted, audited.

Real router, real runs from the scripted engine, a second real tenant session for isolation."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_forge_creator_e2e import ScriptedEngine, chain_actions, console, poll, start  # noqa: E402

from corvin_console import auth as _auth  # noqa: E402
from corvin_console import forge_runs  # noqa: E402

B = "/v1/console/forge"
REQ_TOOL = "count the words in a text"


def _second_tenant_client(client, tenant: str = "other_tenant"):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from corvin_console.app import router
    (client.home / "tenants" / tenant / "global" / "auth").mkdir(parents=True, exist_ok=True)
    (client.home / "tenants" / tenant / "global" / "console" / "sessions").mkdir(parents=True, exist_ok=True)
    app = FastAPI()
    app.include_router(router, prefix="/v1/console")
    rec = _auth.create_session(tenant_id=tenant, token_fingerprint="other-fp")
    other = TestClient(app, raise_server_exceptions=False)
    other.cookies.set("corvin_console_sid", rec.sid)
    return other


def test_list_detail_and_events_for_a_finished_tool_run(tmp_path):
    with console(tmp_path, ScriptedEngine()) as client:
        run_id = start(client, "tool", REQ_TOOL)
        assert poll(client, run_id)["status"] == "success"
        lst = client.get(f"{B}/runs").json()
        assert lst["count"] == 1 and lst["runs"][0]["run_id"] == run_id
        row = lst["runs"][0]
        assert row["kind"] == "tool" and row["status"] == "success" and row["model"] == "scripted-model"
        assert row["artifact_ref"]["kind"] == "tool" and row["live"] is False
        det = client.get(f"{B}/runs/{run_id}").json()
        assert det["run_id"] == run_id and det["progress"] == 100
        ev = client.get(f"{B}/runs/{run_id}/events").json()
        assert ev["events"] and ev["events"][0]["seq"] == 1 and ev["next_after"] == ev["events"][-1]["seq"]
        # `after` pages: nothing after the last seq, the tail after an earlier one
        assert client.get(f"{B}/runs/{run_id}/events", params={"after": ev["next_after"]}).json()["events"] == []
        tail = client.get(f"{B}/runs/{run_id}/events", params={"after": 1}).json()["events"]
        assert tail and tail[0]["seq"] == 2


def test_the_public_view_never_leaks_the_request_the_draft_or_the_owner(tmp_path):
    with console(tmp_path, ScriptedEngine()) as client:
        run_id = start(client, "tool", REQ_TOOL + " secret-marker-1234")
        poll(client, run_id)
        for body in (client.get(f"{B}/runs").json()["runs"][0], client.get(f"{B}/runs/{run_id}").json()):
            raw = json.dumps(body)
            assert "secret-marker-1234" not in raw
            for private in ("resume", "sid_fingerprint", "boot_id", "owner", "heartbeat_at", "draft"):
                assert private not in body, private


def test_filters_kind_status_and_limit_work_and_bad_values_are_422(tmp_path):
    with console(tmp_path, ScriptedEngine()) as client:
        t = start(client, "tool", REQ_TOOL)
        poll(client, t)
        p = start(client, "plugin", "a weather digest plugin")
        poll(client, p)
        assert {r["kind"] for r in client.get(f"{B}/runs").json()["runs"]} == {"tool", "plugin"}
        assert [r["run_id"] for r in client.get(f"{B}/runs", params={"kind": "plugin"}).json()["runs"]] == [p]
        assert client.get(f"{B}/runs", params={"status": "running"}).json()["runs"] == []
        both = client.get(f"{B}/runs", params={"kind": "tool", "status": "success"}).json()["runs"]
        assert [r["run_id"] for r in both] == [t]
        assert len(client.get(f"{B}/runs", params={"limit": 1}).json()["runs"]) == 1
        assert client.get(f"{B}/runs", params={"kind": "banana"}).status_code == 422
        assert client.get(f"{B}/runs", params={"status": "banana"}).status_code == 422
        assert client.get(f"{B}/runs", params={"limit": 0}).status_code == 422


def test_a_second_tenant_sees_exactly_its_own_runs_and_foreign_ids_are_404_everywhere(tmp_path):
    with console(tmp_path, ScriptedEngine()) as client:
        mine = start(client, "tool", REQ_TOOL)
        poll(client, mine)
        other = _second_tenant_client(client)
        # POSITIVE CONTROL: the other tenant has a run of its own and sees it (and only it)
        theirs = forge_runs.new_run(tenant_id="other_tenant", kind="tool", phases=("planning",), sid_fingerprint=None,
                                    resume={"request": "another tenants request"})
        listing = other.get(f"{B}/runs").json()
        assert [r["run_id"] for r in listing["runs"]] == [theirs]
        assert [r["run_id"] for r in client.get(f"{B}/runs").json()["runs"]] == [mine]
        for path in (f"{B}/runs/{mine}", f"{B}/runs/{mine}/events"):
            assert other.get(path).status_code == 404, path
        assert other.get(f"{B}/runs/{theirs}").status_code == 200
        assert client.get(f"{B}/runs/{theirs}").status_code == 404


def test_anonymous_callers_are_refused_and_unknown_or_malformed_ids_are_404(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from corvin_console.app import router
    with console(tmp_path, ScriptedEngine()) as client:
        app = FastAPI()
        app.include_router(router, prefix="/v1/console")
        anon = TestClient(app, raise_server_exceptions=False)
        for path in (f"{B}/runs", f"{B}/runs/run-aaaaaaaaaaaa", f"{B}/runs/run-aaaaaaaaaaaa/events"):
            assert anon.get(path).status_code == 401, path
        for rid in ("run-aaaaaaaaaaaa", "nope", "..%2F..%2Fetc", "a" * 300):
            assert client.get(f"{B}/runs/{rid}").status_code in (404, 422), rid
        assert client.get(f"{B}/runs/run-aaaaaaaaaaaa/events").status_code == 404


def test_a_live_peers_run_is_listed_live_and_an_orphan_is_listed_not_resumed(tmp_path, monkeypatch):
    spawned = []
    monkeypatch.setattr(forge_runs, "spawn", lambda **kw: spawned.append(kw["run_id"]))
    with console(tmp_path, ScriptedEngine()) as client:
        d = client.home / "tenants/_default/global/forge_runs"
        d.mkdir(parents=True, exist_ok=True)
        base = {"tenant_id": "_default", "kind": "tool", "status": "running", "phase": "planning", "phases": ["planning"],
                "progress": 5, "message": "m", "engine": "e", "boot_id": "another", "resume": {"request": "x" * 12}}
        (d / "run-111111111111.json").write_text(json.dumps({**base, "heartbeat_at": time.time(), "started_at": "2026-10-11T10:00:00+00:00"}))
        (d / "run-222222222222.json").write_text(json.dumps({**base, "heartbeat_at": 0, "started_at": "2026-10-11T09:00:00+00:00"}))
        rows = {r["run_id"]: r for r in client.get(f"{B}/runs").json()["runs"]}
        assert rows["run-111111111111"]["live"] is True and rows["run-222222222222"]["live"] is False
        assert spawned == [], "a viewer never resumes anything"
        assert json.loads((d / "run-222222222222.json").read_text())["status"] == "running", "and never rewrites a record"
        order = [r["run_id"] for r in client.get(f"{B}/runs").json()["runs"]]
        assert order == ["run-111111111111", "run-222222222222"], "newest first"


def test_listing_prunes_a_quiet_tenant(tmp_path):
    import os
    with console(tmp_path, ScriptedEngine()) as client:
        d = client.home / "tenants/_default/global/forge_runs"
        d.mkdir(parents=True, exist_ok=True)
        old = d / "run-333333333333.json"
        old.write_text(json.dumps({"tenant_id": "_default", "kind": "tool", "status": "success", "boot_id": "x"}))
        t = time.time() - 30 * 86400
        os.utime(old, (t, t))
        assert client.get(f"{B}/runs").json()["runs"] == []
        assert not old.exists()


def test_run_start_and_finish_are_on_the_audit_chain_with_ids_and_counts_only(tmp_path):
    with console(tmp_path, ScriptedEngine()) as client:
        run_id = start(client, "tool", REQ_TOOL + " secret-marker-9876")
        poll(client, run_id)
        chain = client.home / "tenants/_default/global/forge/audit.jsonl"
        recs = [json.loads(line) for line in chain.read_text().splitlines() if line.strip()]
        started = [r for r in recs if r.get("event_type") == "forge_run.started"]
        finished = [r for r in recs if r.get("event_type") == "forge_run.finished"]
        assert len(started) == 1 and len(finished) == 1
        assert started[0]["details"]["run_id"] == run_id == finished[0]["details"]["run_id"]
        d = finished[0]["details"]
        assert d["status"] == "success" and d["artifact_kind"] == "tool" and d["artifact_id"] and d["event_count"] >= 2
        assert d["truncated"] is False
        assert "secret-marker-9876" not in chain.read_text()
        assert all("_dropped_fields" not in r.get("details", {}) for r in started + finished), "every field is allowlisted"


def test_retryable_is_true_only_for_a_failed_tool_run_that_kept_its_draft(tmp_path):
    from test_forge_creator_e2e import BROKEN_IMPL, tool_plan
    engine = ScriptedEngine(tool_plan=tool_plan(BROKEN_IMPL), tool_fix=tool_plan(BROKEN_IMPL))
    with console(tmp_path, engine) as client:
        failed = start(client, "tool", REQ_TOOL)
        assert poll(client, failed)["status"] == "failed"
        row = client.get(f"{B}/runs/{failed}").json()
        assert row["retryable"] is True
        assert "draft" not in json.dumps(row), "the fact that a draft exists is shown, never the draft"
    with console(tmp_path / "ok", ScriptedEngine()) as client:
        ok = start(client, "tool", REQ_TOOL)
        assert poll(client, ok)["status"] == "success"
        assert client.get(f"{B}/runs/{ok}").json()["retryable"] is False          # finished fine: nothing to retry
        plug = start(client, "plugin", "a weather digest plugin")
        poll(client, plug)
        assert client.get(f"{B}/runs/{plug}").json()["retryable"] is False         # only tool runs have a retry route
