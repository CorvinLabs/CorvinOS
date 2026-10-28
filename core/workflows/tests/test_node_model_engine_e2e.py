"""ADR-2308 / T-0328 (engine slice) — a node's model reaches the real `claude` argv, the span
and the audit chain.

Real DAGRunner, real ClaudeCliEngine (so the four pre-spawn gates run), a real subprocess: the
`claude` on PATH is a small script that records its argv and answers with the CLI's
`--output-format json` envelope. Sandboxed CORVIN_HOME (repo-root conftest). No network, no model.
"""
from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

from corvin_workflows import DAGRunner
from corvin_workflows import model_resolution as _mr
from corvin_workflows.storage import load_workflow
from corvin_workflows.validator import validate

_mr._ensure_shared_on_path()  # the same bootstrap production code uses for bridges/shared
_FORGE = str(Path(__file__).resolve().parents[3] / "corvin_operator" / "forge")


_SHARED = str(Path(__file__).resolve().parents[3] / "corvin_operator" / "bridges" / "shared")


@pytest.fixture(autouse=True)
def _gates_and_forge_only_here(monkeypatch):
    """These tests run the REAL pre-spawn gates and write to the real audit writer, so they need
    the top-level ``forge`` package (on PYTHONPATH in production) and they leave state behind in
    the gate modules (house-rules counters, audit-writer fallbacks). Neither may leak: another
    test (the peer gate) takes a different branch once ``forge`` is importable and reads that
    state. Everything newly imported from ``forge`` or ``bridges/shared`` during a test is
    dropped again afterwards, and the extra path entry is undone."""
    before = set(sys.modules)
    monkeypatch.syspath_prepend(_FORGE)
    yield
    for name in set(sys.modules) - before:
        origin = getattr(sys.modules.get(name), "__file__", None) or ""
        if name == "forge" or name.startswith("forge.") or origin.startswith((_FORGE, _SHARED)):
            sys.modules.pop(name, None)

REPORTED = "reported-model-1"

FAKE_CLAUDE = """#!/usr/bin/env python3
import json, os, sys
with open(os.environ["FAKE_CLAUDE_LOG"], "a") as fh:
    fh.write(json.dumps(sys.argv[1:]) + "\\n")
# The L44 house-rules gate runs its own classifier through this same `claude` (--max-turns 1):
# answer it like a healthy model (no rule violated) so the gate really runs and really passes.
classifier = "--max-turns" in sys.argv
if not classifier and "-p" in sys.argv and "MARK-FAIL" in sys.argv[sys.argv.index("-p") + 1]:
    sys.stderr.write("simulated engine failure\\n")
    sys.exit(1)                                   # a node whose spawn fails
reply = ({"violated_rule_id": "", "confidence": 0.0, "reason": "ok"} if classifier
         else {"items": ["x", "y"], "ok": True})
envelope = {
    "type": "result", "subtype": "success", "is_error": False,
    "result": json.dumps(reply),
    "total_cost_usd": 0.0123,
    "usage": {"input_tokens": 11, "output_tokens": 7,
              "cache_read_input_tokens": 100, "cache_creation_input_tokens": 20},
    "modelUsage": {"%s": {"inputTokens": 11, "outputTokens": 7}},
}
print(json.dumps(envelope))
""" % REPORTED

WORKFLOW = """awp: "1.0.0"
workflow:
  name: node_model_e2e
  description: Three nodes that show which model each one runs on.
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
      instructions: "MARK-FAN"
      items_from: plain.items
      depends_on: [plain]
"""


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
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


def _doc(tmp_path: Path, text: str = WORKFLOW):
    p = tmp_path / "wf.awp.yaml"
    p.write_text(text)
    return load_workflow(p)


def _chain(tenant: str = "_default") -> list[dict]:
    from forge import paths as fp
    path = fp.tenant_audit_chain(tenant)
    if not Path(path).exists():
        return []
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def _events(name: str, since: int = 0) -> list[dict]:
    return [r["details"] for r in _chain()[since:] if r.get("event_type") == name]


def _argv(log: Path) -> list[list[str]]:
    """The NODE spawns only: the gates' own classifier calls (``--max-turns``) are not nodes."""
    calls = [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
    return [c for c in calls if "--max-turns" not in c]


def _call_for(calls: list[list[str]], marker: str) -> list[str]:
    """The spawn whose prompt carries *marker* (order inside a topological level is not promised)."""
    hits = [c for c in calls if marker in c[c.index("-p") + 1]]
    assert hits, marker
    return hits[0]


def _model_of(argv: list[str]) -> str:
    return argv[argv.index("--model") + 1]


def _run(tmp_path, doc=None):
    from corvin_workflows.engines_claude import ClaudeCliEngine
    engine = ClaudeCliEngine(tenant_id="_default")
    return DAGRunner(doc or _doc(tmp_path), engine=engine, tenant_id="_default",
                     workflow_id="wf-e2e").run({})


def test_node_value_reaches_the_argv_the_plain_node_keeps_the_engine_alias(cli_env, tmp_path):
    from model_selector import tier_model
    res = _run(tmp_path)
    assert res.state == "complete", getattr(res, "error", res)
    calls = _argv(cli_env)
    assert len(calls) == 4  # with_model, plain, fan x 2
    # Order within a topological level is not promised: key the calls by the prompt they carry.
    assert _model_of(_call_for(calls, "MARK-WITH-MODEL")) == tier_model("opus")
    assert _model_of(_call_for(calls, "MARK-PLAIN")) == "haiku"  # the alias this engine always used
    assert _model_of(_call_for(calls, "MARK-FAN")) == "haiku"
    for c in calls:
        assert c[c.index("--output-format") + 1] == "json"


def test_each_spawn_writes_a_span_on_the_reported_model_with_the_four_way_split(cli_env, tmp_path):
    before = len(_chain())
    _run(tmp_path)
    ends = _events("engine.span.end", before)
    assert len(ends) == 4
    for d in ends:
        assert d["model_id"] == REPORTED            # the model the CLI REPORTED
        assert (d["input_tokens"], d["output_tokens"], d["cache_read_tokens"], d["cache_write_tokens"]) == (
            11, 7, 100, 20)
        assert d["status"] == "ok" and d["role"] == "worker"
        assert d["tokens_used"] == 138


def test_resolution_event_joins_the_span_and_carries_ids_and_enums_only(cli_env, tmp_path):
    before = len(_chain())
    _run(tmp_path)
    resolved = _events("workflow.node_model_resolved", before)
    span_ids = {d["span_id"] for d in _events("engine.span.end", before)}
    by_node = {}
    for d in resolved:
        by_node.setdefault(d["node_id"], []).append(d)
    assert set(by_node) == {"with_model", "plain", "fan"}
    assert by_node["with_model"][0]["source"] == "node" and by_node["with_model"][0]["model_id"]
    assert by_node["plain"][0]["source"] == "site_default" and by_node["plain"][0]["model_id"] == ""
    assert sorted(d["item_index"] for d in by_node["fan"]) == [0, 1]
    for d in resolved:
        assert d["span_id"] in span_ids
        assert d["wid"] == "wf-e2e" and d["rid"] and d["tenant_id"] == "_default"
        assert d["node_class"].split("|")[0] in ("agent", "fan_out") and d["node_class"].endswith("|json")
        assert d["gate_status"] == "none" and d["escalated"] is False
        assert "instructions" not in json.dumps(d)  # never a prompt, never free text


def test_the_level4_variable_sets_the_model_of_an_unset_node(cli_env, tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HELPER_MODEL_WORKFLOW_NODE", "sonnet-from-env-1")
    before = len(_chain())
    _run(tmp_path)
    assert _model_of(_call_for(_argv(cli_env), "MARK-PLAIN")) == "sonnet-from-env-1"
    plain = [d for d in _events("workflow.node_model_resolved", before) if d["node_id"] == "plain"][0]
    assert (plain["source"], plain["model_id"]) == ("site_default", "sonnet-from-env-1")


def test_an_invalid_node_value_fails_validation_and_never_reaches_a_spawn(cli_env, tmp_path):
    bad = WORKFLOW.replace("model: opus", "model: not-a-real-model-9")
    with pytest.raises(Exception) as ei:
        validate(_doc(tmp_path, bad))
    assert "model_unknown" in str(ei.value)
    assert not cli_env.exists() or _argv(cli_env) == []


@pytest.mark.parametrize("stdout,text,model", [
    ('{"ok": true}', '{"ok": true}', ""),                       # not an envelope: the plain reply, no model claimed
    ("plain words", "plain words", ""),                          # not JSON at all
    ('{"result": 5}', "", ""),                                  # an envelope whose result is not text
    ('{"result": "r", "modelUsage": {"a": {"outputTokens": 1}, "b": {"outputTokens": 9}}}', "r", "b"),
])
def test_the_envelope_reader_is_fail_soft_and_claims_only_a_model_it_was_told(stdout, text, model):
    from corvin_workflows.engines_claude import split_envelope
    got_text, usage, reported, cost, is_error = split_envelope(stdout)
    assert (got_text, reported, is_error) == (text, model, False)
    assert isinstance(usage, dict)


def test_an_envelope_that_reports_an_error_is_flagged():
    from corvin_workflows.engines_claude import split_envelope
    assert split_envelope('{"result": "boom", "is_error": true}')[4] is True


def test_profile_entries_join_the_span_by_span_id_with_equal_tokens_and_no_text(cli_env, tmp_path):
    from corvin_workflows import node_profile
    before = len(_chain())
    _run(tmp_path)
    ends = {d["span_id"]: d for d in _events("engine.span.end", before)}
    mine = [r for r in node_profile.read_entries("_default") if r["span_id"] in ends]
    assert len(mine) == 4                                        # with_model, plain, fan x 2
    for r in mine:
        d = ends[r["span_id"]]
        assert (r["input_tokens"], r["output_tokens"], r["cache_read_tokens"], r["cache_write_tokens"]) == (
            d["input_tokens"], d["output_tokens"], d["cache_read_tokens"], d["cache_write_tokens"])
        assert r["model_id"] == d["model_id"] == REPORTED
        assert r["signal"] == "proxy" and r["status"] == "ok" and r["cost_usd"] == 0.0123 and r["rid"]
    assert sorted(r["item_index"] for r in mine if r["node_class"].startswith("fan_out")) == [0, 1]
    assert "MARK-" not in json.dumps(mine)                       # never a prompt, never an instruction
    assert node_profile.gate_eligible(mine) == []                # a proxy entry never reaches a gate


def test_a_failed_node_is_spawned_exactly_once_and_the_span_ends_in_error(cli_env, tmp_path):
    failing = WORKFLOW.replace("MARK-PLAIN", "MARK-FAIL")
    before = len(_chain())
    res = _run(tmp_path, _doc(tmp_path, failing))
    assert res.state == "failed"
    spawns = [c for c in _argv(cli_env) if "MARK-FAIL" in c[c.index("-p") + 1]]
    assert len(spawns) == 1                          # no hidden retry, no escalation after a failure
    ends = [d for d in _events("engine.span.end", before) if d["status"] == "error"]
    assert len(ends) == 1 and ends[0]["model_id"] == ""   # no envelope arrived: no model is claimed


def test_a_stub_engine_run_is_unchanged_and_writes_nothing(tmp_path):
    from corvin_workflows import StubEngine
    before = len(_chain())
    res = DAGRunner(_doc(tmp_path), engine=StubEngine(default=lambda call: {"items": ["x"], "ok": True}),
                    tenant_id="_default", workflow_id="wf-stub").run({})
    assert res.state == "complete"
    assert _events("workflow.node_model_resolved", before) == []  # a stub never spawns a model
