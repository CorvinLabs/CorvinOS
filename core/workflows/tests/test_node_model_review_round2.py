"""ADR-2308 adversarial review, round 2 (2026-10-29) — one test per finding.

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
from corvin_workflows import node_profile

_mr._ensure_shared_on_path()
_FORGE = str(Path(__file__).resolve().parents[3] / "corvin_operator" / "forge")
_SHARED = str(Path(__file__).resolve().parents[3] / "corvin_operator" / "bridges" / "shared")

# Every reply also satisfies a delegation_loop manager (COMPLETE) and an agent (any object).
# MARK-ERRENV: the CLI exits 1 but still prints its envelope (tokens were spent).
FAKE_CLAUDE = """#!/usr/bin/env python3
import json, os, sys
with open(os.environ["FAKE_CLAUDE_LOG"], "a") as fh:
    fh.write(json.dumps(sys.argv[1:]) + "\\n")
classifier = "--max-turns" in sys.argv
prompt = sys.argv[sys.argv.index("-p") + 1] if "-p" in sys.argv else ""
reply = ({"violated_rule_id": "", "confidence": 0.0, "reason": "ok"} if classifier
         else {"decision": "COMPLETE", "result": "done", "ok": True})
env = {"type": "result", "subtype": "success", "is_error": False, "result": json.dumps(reply),
       "total_cost_usd": 0.01,
       "usage": {"input_tokens": 10, "output_tokens": 5},
       "modelUsage": {os.environ.get("FAKE_REPORTED", "reported-model-1"): {"outputTokens": 5}}}
if not classifier and "MARK-ERRENV" in prompt:
    env["is_error"] = True
    print(json.dumps(env))
    sys.exit(1)
print(json.dumps(env))
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
    return WorkflowDoc.from_dict({"awp": "1.0.0", "workflow": {"name": "review_r2", "description": "d"},
                                  "orchestration": {"engine": "dag", "graph": graph}})


def _chain(tenant: str = "_default") -> list[dict]:
    from forge import paths as fp
    p = Path(fp.tenant_audit_chain(tenant))
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.exists() else []


def _engine():
    from corvin_workflows.engines_claude import ClaudeCliEngine
    return ClaudeCliEngine(tenant_id="_default")


def _run(doc, engine=None):
    return DAGRunner(doc, engine=engine or _engine(), tenant_id="_default", workflow_id="wf-r2").run({})


LOOP = {"id": "loop", "type": "delegation_loop", "agent": "a", "instructions": "MARK-LOOP",
        "config": {"manager": "m", "budget": {"max_loops": 1, "max_total_workers": 1}}}


def test_a_delegation_loop_manager_span_says_manager_and_joins_its_run(cli_log):
    before = len(_chain())
    res = _run(_doc([LOOP]))
    assert res.state == "complete", res.error
    ends = [r["details"] for r in _chain()[before:] if r.get("event_type") == "engine.span.end"]
    assert ends and ends[0]["role"] == "manager"
    assert all(d["run_id"] == res.run_id for d in ends)      # joins the run without model resolution


def test_the_span_start_names_the_resolved_model_not_the_alias(cli_log):
    from model_selector import tier_model
    before = len(_chain())
    _run(_doc([{"id": "n", "type": "agent", "agent": "a", "instructions": "x"}]))   # DAG default: haiku
    starts = [r["details"] for r in _chain()[before:] if r.get("event_type") == "engine.span.start"]
    assert [d["model_id"] for d in starts] == [tier_model("haiku")]


def test_a_failed_exit_with_an_envelope_keeps_its_tokens_and_counts_under_the_requested_model(cli_log):
    from model_selector import tier_model
    before = len(_chain())
    res = _run(_doc([{"id": "n", "type": "agent", "agent": "a", "instructions": "MARK-ERRENV"}]))
    assert res.state == "failed"
    assert res.nodes["n"].usage.get("tokens") == 15          # the spend of the failed attempt
    ends = [r["details"] for r in _chain()[before:] if r.get("event_type") == "engine.span.end"]
    assert ends[0]["status"] == "error" and ends[0]["tokens_used"] == 15
    rows = [r for r in node_profile.read_entries("_default") if r["span_id"] in {d["span_id"] for d in ends}]
    assert rows and rows[0]["requested_model_id"] == tier_model("haiku")


def test_the_loss_report_counts_a_failure_without_a_reported_model_under_the_requested_one(cli_log, monkeypatch):
    rows = [
        {"node_class": "agent|n|<8k|json", "model_id": "m-1", "requested_model_id": "m-1", "status": "ok"},
        {"node_class": "agent|n|<8k|json", "model_id": "", "requested_model_id": "m-1", "status": "error"},
    ]
    monkeypatch.setattr(node_profile, "read_entries", lambda tenant: rows)
    report = node_profile.loss_report("_default", min_runs=1)
    assert [(r["model_id"], r["runs"], r["error_rate"]) for r in report["classes"]] == [("m-1", 2, 0.5)]


@pytest.mark.parametrize("model,kept", [
    ("claude-sonnet-5@20250929", True), ("claude-opus-5[1m]", True), ("claude-haiku-5-5", True),
    ("a/b", False), ("me@example.com", False), ("two words", False),
])
def test_the_profile_keeps_real_model_id_shapes_and_nothing_else(model, kept):
    entry = node_profile.build_entry({"usage": {"model_id": model}}, "agent|n|<8k|json", "ok")
    assert (entry["model_id"] == model) is kept


def test_the_cli_json_reports_model_and_tokens_per_node(cli_log, tmp_path, monkeypatch):
    from corvin_workflows import cli
    monkeypatch.setenv("FAKE_REPORTED", "reported-model-7")
    wf = tmp_path / "cli_r2.awp.yaml"
    wf.write_text(yaml.safe_dump({"awp": "1.0.0", "workflow": {"name": "cli_r2", "description": "d"},
                                  "orchestration": {"engine": "dag", "graph": [
                                      {"id": "n", "type": "agent", "agent": "a", "instructions": "x",
                                       "model": "haiku"}]}}))
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cli.main(["run", str(wf), "--engine", "claude", "--format", "json"])
    payload = json.loads(buf.getvalue())
    assert code == 0, payload
    assert payload["nodes"]["n"]["model_id"] == "reported-model-7"
    assert payload["nodes"]["n"]["tokens"] == 15 and payload["nodes"]["n"]["attempts"] == 1


def test_model_unresolved_follows_the_retry_policy_and_model_unknown_does_not(cli_log, monkeypatch):
    calls = {"n": 0}
    real = _mr._default_deps

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient reader fault")
        return real()
    monkeypatch.setattr(_mr, "_default_deps", flaky)
    res = _run(_doc([{"id": "n", "type": "agent", "agent": "a", "instructions": "x", "model": "haiku",
                      "retry": {"max_retries": 1}}]))
    assert res.state == "complete" and res.nodes["n"].attempts == 2
    monkeypatch.setattr(_mr, "_default_deps", real)
    res = _run(_doc([{"id": "n", "type": "agent", "agent": "a", "instructions": "x",
                      "model": "no-such-model-9", "retry": {"max_retries": 2}}]))
    assert res.state == "failed" and res.nodes["n"].attempts == 1


def test_a_cli_that_cannot_start_closes_the_span_it_opened(cli_log, monkeypatch):
    """Review round 3: only FileNotFoundError closed the span; a non-executable `claude` (or an argv
    over the kernel limit, E2BIG) left `engine.span.start` without an end."""
    from corvin_workflows import engines_claude as ec
    engine = ec.ClaudeCliEngine(tenant_id="_default")       # constructed while `claude` is on PATH
    real_run = ec.subprocess.run

    def cannot_start(cmd, *a, **kw):
        if cmd and cmd[0] == "claude" and "--max-turns" not in cmd:
            raise OSError(7, "Argument list too long")        # E2BIG
        return real_run(cmd, *a, **kw)
    monkeypatch.setattr(ec.subprocess, "run", cannot_start)
    before = len(_chain())
    res = _run(_doc([{"id": "n", "type": "agent", "agent": "a", "instructions": "x"}]), engine)
    assert res.state == "failed" and "could not be started" in (res.nodes["n"].error or "")
    new = _chain()[before:]
    starts = [r for r in new if r.get("event_type") == "engine.span.start"]
    ends = [r for r in new if r.get("event_type") == "engine.span.end"]
    assert len(starts) == len(ends) == 1 and ends[0]["details"]["status"] == "error"


def test_readiness_names_the_site_default_when_it_is_set(monkeypatch):
    from corvin_workflows.readiness import check_readiness
    text = yaml.safe_dump({"awp": "1.0.0", "workflow": {"name": "wf", "description": "d"},
                           "orchestration": {"engine": "dag", "graph": [
                               {"id": "a", "type": "agent", "agent": "w", "depends_on": []}]}})
    monkeypatch.setattr(_mr, "operator_pin", lambda tenant: "")
    monkeypatch.setattr(_mr, "site_default", lambda: "site-model-1")
    sig = next(s for s in check_readiness(text, acceptance=None, repairs=0, tenant_id="t").signals
               if s.name == "node_model")
    assert "site default" in sig.detail and "site-model-1" not in sig.detail


# ── review round 3 (2026-10-29) ─────────────────────────────────────────────

@pytest.mark.parametrize("gate_status", ["stale", "rolled_back"])
def test_a_row_that_does_not_act_leaves_the_node_on_the_site_default(gate_status):
    class Deps:
        def pin(self, tenant): return None
        def env(self): return "site-model-1"
        def tier(self, name): return {"haiku": "h-1", "sonnet": "s-1", "opus": "o-1"}[name]
        def rank(self, m): return {"h-1": 1, "s-1": 2, "o-1": 3}.get(m, 0)
        def registry_id(self, v): return v if v in ("h-1", "s-1", "o-1") else None
        def current(self, m): return m
        def legacy_default(self, tenant): return None
        def autoselect_is_off(self): return False
    row = {"model_id": "h-1", "tier": "haiku", "r": "h-1", "gate_status": gate_status}
    if gate_status == "stale":
        row["r"] = "h-1"
        row["tier"] = "sonnet"          # the lineage moved on: tier_model("sonnet") != row model
    res = _mr.resolve_node_model({"id": "n", "type": "agent"}, "t", "dag", {"wf:agent": row}, deps=Deps())
    assert res.source == "site_default" and res.model_id == "site-model-1", res


@pytest.mark.parametrize("value", ["claude-haiku-5-5\n", "run-1\n"])
def test_profile_ids_reject_a_trailing_newline(value):
    entry = node_profile.build_entry({"run_id": value, "usage": {"model_id": value}}, "agent|n|<8k|json\n", "ok")
    assert entry["rid"] == "" and entry["model_id"] == "" and entry["node_class"] == ""
