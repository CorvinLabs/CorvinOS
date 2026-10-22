"""The design chat can no longer reach `ready` by a keyword (ADR-2275 / PLAN-0962 S3).

Real design WebSocket + HTTP run route against the console router in a sandboxed CORVIN_HOME;
only the `claude -p` spawn is replaced (nothing here reaches a model).
"""
from __future__ import annotations

import json
import time

import pytest

from test_workflow_composer import _TWO_STEP, audit_records, make_workflow, sandbox  # noqa: E402

BASE = "/v1/console/workflows"
ACCEPTANCE = {"expect": {"state": "complete", "nodes_reached": ["fetch", "summarise"]}}
INVALID_YAML = _TWO_STEP.replace("depends_on: [fetch]", "depends_on: [ghost]")


def _meta(tid, wid="wf1"):
    from forge import paths as _fp
    return json.loads((_fp.tenant_home(tid) / "workflows" / f"{wid}.meta.json").read_text())


def _set_meta(tid, wid="wf1", **kv):
    from forge import paths as _fp
    p = _fp.tenant_home(tid) / "workflows" / f"{wid}.meta.json"
    m = json.loads(p.read_text())
    m.update(kv)
    p.write_text(json.dumps(m))


def _fake_designer(monkeypatch, yaml_text):
    import corvin_console.routes.workflows as wfroutes

    def spawn(prompt):
        return yaml_text[0] if "Based on this conversation" in prompt else "Looks good to me."
    monkeypatch.setattr(wfroutes, "_run_claude_for_design", spawn)
    monkeypatch.setattr(wfroutes._spawn_gates, "check_console_spawn_or_refusal", lambda *a, **k: None)
    return wfroutes


def _say(client, text, wid="wf1"):
    with client.websocket_connect(f"{BASE}/{wid}/chat") as ws:
        ws.send_json({"type": "user", "text": text})
        seen_user = False
        while True:
            f = ws.receive_json()
            if f["type"] != "message":
                continue
            if f["role"] == "user":
                seen_user = True
            elif seen_user:
                return f


def test_confirmation_word_without_an_acceptance_case_stays_detailing_and_names_the_line(tmp_path, monkeypatch):
    with sandbox(tmp_path) as (app, home, tid, new_client):
        make_workflow(tid, "wf1", _TWO_STEP, phase="detailing")
        _fake_designer(monkeypatch, [_TWO_STEP])
        frame = _say(new_client(), "yes, that is fine")
        assert "phase_update" not in frame
        assert "Not marked ready yet" in frame["content"] and "acceptance case" in frame["content"]
        assert _meta(tid)["phase"] == "detailing"
        checked = [r for r in audit_records(tid) if r["event_type"] == "workflow.readiness_checked"]
        assert checked and checked[-1]["details"]["unmeasured"] == 1 and checked[-1]["details"]["ready"] is False


def test_with_a_holding_acceptance_case_the_phase_moves_to_ready(tmp_path, monkeypatch):
    with sandbox(tmp_path) as (app, home, tid, new_client):
        make_workflow(tid, "wf1", _TWO_STEP, phase="detailing")
        _set_meta(tid, acceptance=ACCEPTANCE)
        _fake_designer(monkeypatch, [_TWO_STEP])
        frame = _say(new_client(), "yes, that is fine")
        assert frame["phase_update"] == "ready" and "Readiness check passed" in frame["content"]
        assert _meta(tid)["phase"] == "ready"


def test_a_failing_acceptance_case_blocks_and_says_which_line(tmp_path, monkeypatch):
    with sandbox(tmp_path) as (app, home, tid, new_client):
        make_workflow(tid, "wf1", _TWO_STEP, phase="detailing")
        _set_meta(tid, acceptance={"expect": {"state": "complete", "nodes_reached": ["nope"]}})
        _fake_designer(monkeypatch, [_TWO_STEP])
        frame = _say(new_client(), "ok")
        assert "phase_update" not in frame and "acceptance case" in frame["content"]
        assert _meta(tid)["phase"] == "detailing"


def test_designer_repairs_are_counted_audited_and_above_the_ceiling_block_ready(tmp_path, monkeypatch):
    with sandbox(tmp_path) as (app, home, tid, new_client):
        make_workflow(tid, "wf1", _TWO_STEP, phase="detailing")
        _set_meta(tid, acceptance=ACCEPTANCE)
        draft = [INVALID_YAML]
        _fake_designer(monkeypatch, draft)
        c = new_client()
        for _ in range(4):
            _say(c, "tweak step two please")
        assert _meta(tid)["repairs"] == 4
        recs = [r["details"] for r in audit_records(tid) if r["event_type"] == "workflow.design_repaired"]
        assert [d["count"] for d in recs] == [1, 2, 3, 4] and {d["code"] for d in recs} == {"yaml_regenerated"}
        assert "_dropped_fields" not in recs[0]
        draft[0] = _TWO_STEP  # the designer finally produces a valid draft
        frame = _say(c, "yes, that is fine")
        assert "phase_update" not in frame and "designer repairs" in frame["content"]
        assert _meta(tid)["phase"] == "detailing"


def _events(resp):
    return [json.loads(l[6:]) for l in resp.text.splitlines() if l.startswith("data: ")]


def test_dry_run_is_a_synthetic_check_that_reports_a_failing_node(tmp_path):
    failing = _TWO_STEP + '''    - id: boom
      type: code
      language: python3
      depends_on: [summarise]
      source: "def main():\\n    raise RuntimeError('x')\\n"
      outputs: [o]
'''
    with sandbox(tmp_path) as (app, home, tid, new_client):
        make_workflow(tid, "bad", failing, phase="ready")
        make_workflow(tid, "good", _TWO_STEP, phase="ready")
        c = new_client()
        bad = _events(c.post(f"{BASE}/bad/runs", json={"inputs": {}, "dry_run": True}))
        assert any(e["type"] == "node_failed" and e["node_id"] == "boom" for e in bad)
        done = bad[-1]
        assert done["type"] == "run_completed" and done["synthetic"] is True and done["ok"] is False
        good = _events(c.post(f"{BASE}/good/runs", json={"inputs": {}, "dry_run": True}))
        assert [e["type"] for e in good if e["type"].startswith("node_")] == [
            "node_started", "node_completed", "node_started", "node_completed"]
        assert good[-1]["ok"] is True and good[-1]["synthetic"] is True and good[-1]["dry_run"] is True
        assert good[-1]["ready"] is False  # no acceptance case: ok to run, not ready
        # the run record keeps its dry_run flag
        from forge import paths as _fp
        metas = list((_fp.tenant_home(tid) / "workflows" / "good" / "runs").glob("*.meta.json"))
        assert metas and json.loads(metas[0].read_text())["dry_run"] is True


def test_dry_run_no_longer_sleeps_per_node():
    import inspect

    import corvin_console.routes.workflows as wf
    src = inspect.getsource(wf._stream_run)
    assert "asyncio.sleep" not in src
