"""ADR-2308 adversarial review, round 1 (2026-10-29) — one test per finding.

Real DAGRunner and real ClaudeCliEngine (the pre-spawn gates run) against a `claude` on PATH that
records its argv; sandboxed CORVIN_HOME (repo-root conftest). No network, no model.
"""
from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest
import yaml

from corvin_workflows import DAGRunner, WorkflowDoc
from corvin_workflows import model_resolution as _mr
from corvin_workflows.validator import validate

_mr._ensure_shared_on_path()
_FORGE = str(Path(__file__).resolve().parents[3] / "corvin_operator" / "forge")
_SHARED = str(Path(__file__).resolve().parents[3] / "corvin_operator" / "bridges" / "shared")

FAKE_CLAUDE = """#!/usr/bin/env python3
import json, os, sys
with open(os.environ["FAKE_CLAUDE_LOG"], "a") as fh:
    fh.write(json.dumps(sys.argv[1:]) + "\\n")
classifier = "--max-turns" in sys.argv
reply = ({"violated_rule_id": "", "confidence": 0.0, "reason": "ok"} if classifier else {"ok": True})
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": json.dumps(reply),
                  "usage": {"input_tokens": 1, "output_tokens": 1},
                  "modelUsage": {"reported-model-1": {"outputTokens": 1}}}))
"""


@pytest.fixture(autouse=True)
def _gates_and_forge_only_here(monkeypatch):
    before = set(sys.modules)
    monkeypatch.syspath_prepend(_FORGE)
    yield
    for name in set(sys.modules) - before:
        origin = getattr(sys.modules.get(name), "__file__", None) or ""
        if name == "forge" or name.startswith("forge.") or origin.startswith((_FORGE, _SHARED)):
            sys.modules.pop(name, None)


@pytest.fixture
def cli_log(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    claude = bindir / "claude"
    claude.write_text(FAKE_CLAUDE)
    claude.chmod(claude.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "argv.jsonl"
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    monkeypatch.delenv("CORVIN_HELPER_MODEL_WORKFLOW_NODE", raising=False)
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    return log


def _doc(graph: list[dict]) -> WorkflowDoc:
    return WorkflowDoc.from_dict({
        "awp": "1.0.0",
        "workflow": {"name": "review_r1", "description": "d"},
        "orchestration": {"engine": "dag", "graph": graph},
    })


def _node_calls(log: Path) -> list[list[str]]:
    if not log.exists():
        return []
    rows = [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
    return [r for r in rows if "--max-turns" not in r]


def _chain(tenant: str = "_default") -> list[dict]:
    from forge import paths as fp
    p = Path(fp.tenant_audit_chain(tenant))
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.exists() else []


def _run(doc: WorkflowDoc, sink: list | None = None):
    from corvin_workflows.engines_claude import ClaudeCliEngine
    return DAGRunner(doc, engine=ClaudeCliEngine(tenant_id="_default"), tenant_id="_default",
                     workflow_id="wf-r1", audit_sink=(sink.append if sink is not None else None)).run({})


# ── finding: model accepted and ignored on types that never take one ───────────────────────────

@pytest.mark.parametrize("node", [
    {"id": "x", "type": "delegation_loop", "agent": "a", "model": "opus",
     "config": {"manager": "m", "budget": {"max_loops": 1, "max_total_workers": 1}}},
    {"id": "x", "type": "deliver", "model": "haiku", "config": {"channel": "discord", "chat_id": "auto"}},
    {"id": "x", "type": "merge", "model": "haiku", "strategy": "concat_list", "inputs": ["a.b"], "output": "o"},
])
def test_a_model_on_a_type_that_takes_none_fails_validation(node):
    with pytest.raises(Exception) as ei:
        validate(_doc([node]))
    assert "take no 'model'" in str(ei.value)


def test_a_model_on_a_plugin_registered_type_is_left_to_the_plugin():
    from corvin_workflows import register_node_type
    from corvin_workflows.node_types import NODE_TYPES
    try:
        register_node_type("review_r1_custom", validate=lambda n: None,
                           execute=lambda **kw: {"ok": True})
        validate(_doc([{"id": "c", "type": "review_r1_custom", "model": "whatever-the-plugin-means"}]))
    finally:
        NODE_TYPES.pop("review_r1_custom", None)


# ── finding: the raw invalid value reached node.failed, and the node was retried ────────────────

def test_an_unknown_node_model_at_run_time_fails_once_with_a_fixed_message(cli_log):
    # validation is skipped on purpose: this is the "registry changed after save" case
    doc = _doc([{"id": "n", "type": "agent", "agent": "a", "instructions": "x",
                 "model": "retired-model-secret-9", "retry": {"max_retries": 2}}])
    before = len(_chain())
    sink: list[dict] = []
    res = _run(doc, sink)
    assert res.state == "failed"
    assert res.nodes["n"].attempts == 1                       # final, never retried
    assert _node_calls(cli_log) == []                          # nothing spawned
    assert "retired-model-secret-9" not in json.dumps(sink)   # the value is never in a record
    assert "model_unknown" in json.dumps(sink) or "unknown node model" in json.dumps(sink)
    resolved = [r for r in _chain()[before:] if r.get("event_type") == "workflow.node_model_resolved"]
    assert len(resolved) == 1 and resolved[0]["details"]["model_id"] == ""


# ── finding: a reader fault silently dropped an explicit node model ─────────────────────────────

def test_a_reader_fault_fails_a_node_that_names_a_model_and_spares_one_that_does_not(cli_log, monkeypatch):
    def broken():
        raise RuntimeError("reader down")
    monkeypatch.setattr(_mr, "_default_deps", broken)
    named = _run(_doc([{"id": "named", "type": "agent", "agent": "a", "instructions": "MARK-NAMED",
                        "model": "opus"}]))
    assert named.nodes["named"].status == "failed" and "model_unresolved" in (named.nodes["named"].error or "")
    assert _node_calls(cli_log) == []                           # never run on the path default instead
    unset = _run(_doc([{"id": "unset", "type": "agent", "agent": "b", "instructions": "MARK-UNSET"}]))
    assert unset.nodes["unset"].status == "success"
    calls = _node_calls(cli_log)
    assert len(calls) == 1 and "MARK-UNSET" in calls[0][calls[0].index("-p") + 1]
    assert calls[0][calls[0].index("--model") + 1] == "haiku"   # the path default, as before


# ── finding: a refused payload left an open span and a resolution event naming it ───────────────

def test_a_payload_the_guard_refuses_opens_no_span_and_writes_no_resolution(cli_log, monkeypatch):
    from corvin_workflows import engines_claude as ec

    def refuse(_prompt):
        raise RuntimeError("guard refused")
    monkeypatch.setattr(ec, "_guard_prompt_head", refuse)
    before = len(_chain())
    res = _run(_doc([{"id": "n", "type": "agent", "agent": "a", "instructions": "x", "model": "haiku"}]))
    assert res.state == "failed"
    new = [r.get("event_type") for r in _chain()[before:]]
    assert "engine.span.start" not in new and "workflow.node_model_resolved" not in new, new


def test_a_missing_cli_closes_the_span_it_opened(cli_log, monkeypatch):
    from corvin_workflows import engines_claude as ec
    engine = ec.ClaudeCliEngine(tenant_id="_default")       # constructed while `claude` is on PATH
    real_run = ec.subprocess.run

    def gone(cmd, *a, **kw):
        if cmd and cmd[0] == "claude" and "--max-turns" not in cmd:
            raise FileNotFoundError("claude")
        return real_run(cmd, *a, **kw)
    monkeypatch.setattr(ec.subprocess, "run", gone)
    before = len(_chain())
    res = DAGRunner(_doc([{"id": "n", "type": "agent", "agent": "a", "instructions": "x"}]),
                    engine=engine, tenant_id="_default", workflow_id="wf-r1").run({})
    assert res.state == "failed"
    new = _chain()[before:]
    starts = [r for r in new if r.get("event_type") == "engine.span.start"]
    ends = [r for r in new if r.get("event_type") == "engine.span.end"]
    assert len(starts) == len(ends) >= 1 and all(e["details"]["status"] == "error" for e in ends)


# ── finding: the readiness hint ignored an operator pin ─────────────────────────────────────────

def test_readiness_names_the_operator_pin_when_one_is_set(monkeypatch):
    from corvin_workflows.readiness import check_readiness
    text = yaml.safe_dump({"awp": "1.0.0", "workflow": {"name": "wf", "description": "d"},
                           "orchestration": {"engine": "dag", "graph": [
                               {"id": "a", "type": "agent", "agent": "w", "depends_on": []}]}})
    monkeypatch.setattr(_mr, "operator_pin", lambda tenant: "pinned-model-1" if tenant == "_default" else "")
    pinned = next(s for s in check_readiness(text, acceptance=None, repairs=0, tenant_id="_default").signals
                  if s.name == "node_model")
    assert "operator pin" in pinned.detail and pinned.value == 0 and "pinned-model-1" not in pinned.detail
    plain = next(s for s in check_readiness(text, acceptance=None, repairs=0, tenant_id="other").signals
                 if s.name == "node_model")
    assert plain.value == 1 and "path default" in plain.detail
