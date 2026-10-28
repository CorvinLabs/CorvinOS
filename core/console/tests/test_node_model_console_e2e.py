"""ADR-2308 / T-0328 (console slice) — HTTP E2E: a node's model reaches the real `claude` argv on BOTH
console paths, with the span, the audit event and a non-zero token count in the run stream.

Real FastAPI app under uvicorn (an httpx client over a socket, as the browser reads it), the console's
own legacy executor AND the DAG engine, a real subprocess: `claude` on PATH is a small script that
records argv + stdin and answers with the CLI's `--output-format json` envelope. The pre-spawn gates
run for real. Sandboxed CORVIN_HOME; no network, no model.
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from test_workflow_resume_hardening import _live_sandbox as _sandbox  # noqa: E402  (same sandbox)

REPORTED = "reported-model-1"

FAKE_CLAUDE = """#!/usr/bin/env python3
import json, os, sys
argv = sys.argv[1:]
positional = [a for i, a in enumerate(argv) if i and argv[i - 1] == "-p" and not a.startswith("--")]
prompt = positional[0] if positional else sys.stdin.read()
with open(os.environ["FAKE_CLAUDE_LOG"], "a") as fh:
    fh.write(json.dumps({"argv": argv, "prompt": prompt}) + "\\n")
if "--max-turns" in argv:                       # the L44 gate's own classifier call
    reply = json.dumps({"violated_rule_id": "", "confidence": 0.0, "reason": "ok"})
elif positional:                                # the DAG engine asks for ONE JSON object
    reply = json.dumps({"ok": True})
elif "COMPLETE" in prompt and "DELEGATE" in prompt:   # the legacy delegation manager
    reply = json.dumps({"decision": "COMPLETE", "result": "done"})
else:                                           # the legacy executor takes plain text lines
    reply = "alpha\\nbeta"
print(json.dumps({
    "type": "result", "subtype": "success", "is_error": False, "result": reply,
    "total_cost_usd": 0.0123,
    "usage": {"input_tokens": 11, "output_tokens": 7,
              "cache_read_input_tokens": 100, "cache_creation_input_tokens": 20},
    "modelUsage": {"%s": {"inputTokens": 11, "outputTokens": 7}},
}))
""" % REPORTED

LEGACY = """awp: "1.0.0"
workflow:
  name: legacy_e2e
  description: Agent and fan_out nodes on the console's own executor.
orchestration:
  engine: dag
  graph:
    - id: with_model
      type: agent
      agent: a
      instructions: "MARK-WITH-MODEL"
      model: opus
    - id: plain
      type: agent
      agent: b
      instructions: "MARK-PLAIN"
    - id: fan
      type: fan_out
      agent: c
      instructions: "MARK-FAN {item}"
      items_from: plain.output
      depends_on: [plain]
    - id: loop
      type: delegation_loop
      agent: d
      instructions: "MARK-LOOP"
      config: {manager: assistant, budget: {max_loops: 1, max_total_workers: 1}}
"""

DAG = """awp: "1.0.0"
workflow:
  name: dag_e2e
  description: Agent nodes next to a deterministic merge on the DAG engine.
orchestration:
  engine: dag
  graph:
    - id: with_model
      type: agent
      agent: a
      instructions: "MARK-WITH-MODEL"
      model: opus
    - id: plain
      type: agent
      agent: b
      instructions: "MARK-PLAIN"
    - id: join
      type: merge
      depends_on: [with_model, plain]
      strategy: concat_list
      inputs: [with_model.ok, plain.ok]
      output: report
"""


@pytest.fixture
def fake_cli(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    claude = bindir / "claude"
    claude.write_text(FAKE_CLAUDE)
    claude.chmod(claude.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    monkeypatch.delenv("CORVIN_HELPER_MODEL_WORKFLOW_NODE", raising=False)
    return log


def _make(tid: str, wid: str, text: str) -> None:
    from forge import paths as fp
    d = fp.tenant_home(tid) / "workflows"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{wid}.meta.json").write_text(json.dumps({"id": wid, "name": wid, "phase": "ready"}))
    (d / f"{wid}.awp.yaml").write_text(text)


def _run(client, wid: str) -> list[dict]:
    events: list[dict] = []
    with client.stream("POST", f"/v1/console/workflows/{wid}/runs",
                       json={"inputs": {}, "dry_run": False}) as r:
        assert r.status_code == 200, r.read()
        for line in r.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
                if events[-1].get("type") in ("run_completed", "error"):
                    break
    return events


def _calls(log: Path) -> list[dict]:
    """Node spawns only; the gates' classifier calls (``--max-turns``) are not nodes."""
    rows = [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
    return [r for r in rows if "--max-turns" not in r["argv"]]


def _call_for(calls: list[dict], marker: str) -> list[str]:
    hits = [c["argv"] for c in calls if marker in c["prompt"]]
    assert hits, marker
    return hits[0]


def _model_of(argv: list[str]):
    return argv[argv.index("--model") + 1] if "--model" in argv else None


def _chain_events(tid: str, name: str) -> list[dict]:
    from forge import paths as fp
    path = Path(fp.tenant_audit_chain(tid))
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return [r["details"] for r in rows if r.get("event_type") == name]


# ── legacy executor ──────────────────────────────────────────────────────────

def test_legacy_path_node_value_reaches_the_argv_and_the_stream_reports_real_tokens(tmp_path, fake_cli):
    from model_selector import tier_model
    with _sandbox(tmp_path) as (client, home, tid):
        _make(tid, "legacy", LEGACY)
        events = _run(client, "legacy")
        assert events[-1]["type"] == "run_completed" and events[-1]["ok"] is True, events[-3:]
        calls = _calls(fake_cli)
        assert _model_of(_call_for(calls, "MARK-WITH-MODEL")) == tier_model("opus")
        assert _model_of(_call_for(calls, "MARK-PLAIN")) is None       # the argv this executor always had
        assert _model_of(_call_for(calls, "MARK-FAN")) is None
        for c in calls:
            assert c["argv"][c["argv"].index("--output-format") + 1] == "json"
        done = {e["node_id"]: e for e in events if e.get("type") == "node_completed"}
        for node in ("with_model", "plain", "fan"):
            assert done[node]["tokens"] > 0 and done[node]["model_id"] == REPORTED, node
        assert done["with_model"]["tokens"] == 138          # 11 + 7 + 100 + 20, the four billed parts


def test_legacy_path_writes_spans_and_resolution_events_only_for_in_scope_nodes(tmp_path, fake_cli):
    with _sandbox(tmp_path) as (client, home, tid):
        _make(tid, "legacy", LEGACY)
        before_spans = len(_chain_events(tid, "engine.span.end"))
        _run(client, "legacy")
        ends = _chain_events(tid, "engine.span.end")[before_spans:]
        assert ends and all(d["model_id"] == REPORTED for d in ends)
        assert all((d["input_tokens"], d["output_tokens"], d["cache_read_tokens"], d["cache_write_tokens"])
                   == (11, 7, 100, 20) for d in ends)
        resolved = _chain_events(tid, "workflow.node_model_resolved")
        by_node: dict[str, list[dict]] = {}
        for d in resolved:
            by_node.setdefault(d["node_id"], []).append(d)
        assert set(by_node) == {"with_model", "plain", "fan"}, set(by_node)   # delegation_loop is out of scope
        assert by_node["with_model"][0]["source"] == "node"
        assert by_node["plain"][0]["source"] == "site_default" and by_node["plain"][0]["model_id"] == ""
        assert sorted(d["item_index"] for d in by_node["fan"]) == [0, 1]
        span_ids = {d["span_id"] for d in ends}
        assert all(d["span_id"] in span_ids and d["wid"] == "legacy" and d["rid"] for d in resolved)
        # the excluded type still spawned, on the unchanged argv, with a span of its own
        loop_call = _call_for(_calls(fake_cli), "MARK-LOOP")
        assert _model_of(loop_call) is None and "--output-format" in loop_call


def test_an_operator_pin_applies_to_every_in_scope_node_on_the_legacy_path(tmp_path, fake_cli, monkeypatch):
    import engine_models
    monkeypatch.setattr(engine_models, "get_tenant_engine_model",
                        lambda tenant_id, engine_id, role: "anthropic/pinned-model-9" if role == "worker_model" else None)
    with _sandbox(tmp_path) as (client, home, tid):
        _make(tid, "legacy", LEGACY)
        events = _run(client, "legacy")
        assert events[-1]["ok"] is True
        calls = _calls(fake_cli)
        for marker in ("MARK-WITH-MODEL", "MARK-PLAIN", "MARK-FAN"):
            assert _model_of(_call_for(calls, marker)) == "pinned-model-9", marker   # provider prefix stripped
        assert _model_of(_call_for(calls, "MARK-LOOP")) is None        # excluded type: the pin does not reach it
        assert {d["source"] for d in _chain_events(tid, "workflow.node_model_resolved")} == {"pin"}


def test_the_level4_variable_sets_an_unset_node_on_the_legacy_path(tmp_path, fake_cli, monkeypatch):
    monkeypatch.setenv("CORVIN_HELPER_MODEL_WORKFLOW_NODE", "site-model-3")
    with _sandbox(tmp_path) as (client, home, tid):
        _make(tid, "legacy", LEGACY)
        _run(client, "legacy")
        calls = _calls(fake_cli)
        assert _model_of(_call_for(calls, "MARK-PLAIN")) == "site-model-3"
        assert _model_of(_call_for(calls, "MARK-WITH-MODEL")) != "site-model-3"   # the node value outranks level 4


# ── DAG engine ───────────────────────────────────────────────────────────────

def test_dag_route_node_value_reaches_the_argv_and_the_tokens_are_in_the_stream(tmp_path, fake_cli):
    from model_selector import tier_model
    with _sandbox(tmp_path) as (client, home, tid):
        _make(tid, "dag", DAG)
        before = len(_chain_events(tid, "engine.span.end"))
        events = _run(client, "dag")
        assert events[-1]["type"] == "run_completed" and events[-1]["ok"] is True, events[-3:]
        calls = _calls(fake_cli)
        assert _model_of(_call_for(calls, "MARK-WITH-MODEL")) == tier_model("opus")
        assert _model_of(_call_for(calls, "MARK-PLAIN")) == "haiku"      # the alias this engine always used
        for c in calls:
            assert c["argv"][c["argv"].index("--output-format") + 1] == "json"
        ends = _chain_events(tid, "engine.span.end")[before:]
        assert len(ends) == 2 and all(d["model_id"] == REPORTED and d["tokens_used"] == 138 for d in ends)
        resolved = {d["node_id"]: d for d in _chain_events(tid, "workflow.node_model_resolved")}
        assert resolved["with_model"]["source"] == "node" and resolved["plain"]["source"] == "site_default"
        assert all(d["wid"] == "dag" and d["rid"] for d in resolved.values())
