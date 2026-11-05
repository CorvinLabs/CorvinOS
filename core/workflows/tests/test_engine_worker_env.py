"""Workflow `agent` node spawn: MCP isolation, env strip, explicit tenant (review 2026-11-05).

* DLG-9  — `ClaudeCliEngine.spawn` ran `claude -p` without `--strict-mcp-config` (the operator's
  user/project MCP servers loaded; `_DENIED_TOOLS` covers built-ins only) and with the FULL
  process environment.
* DLG-23 — the engine fell back to `CORVIN_TENANT_ID` when it had no tenant.

Real DAGRunner, real ClaudeCliEngine (the four pre-spawn gates run), a real subprocess: the
`claude` on PATH is a script that records its argv and environment and answers with the CLI's
`--output-format json` envelope. Sandboxed CORVIN_HOME (repo-root conftest).
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
from corvin_workflows.engines import EngineCall
from corvin_workflows.storage import load_workflow

_mr._ensure_shared_on_path()
_FORGE = str(Path(__file__).resolve().parents[3] / "corvin_operator" / "forge")
_SHARED = str(Path(__file__).resolve().parents[3] / "corvin_operator" / "bridges" / "shared")

_SECRETS = {
    "OPENAI_API_KEY": "sk-dlg9-openai-marker",
    "GITHUB_TOKEN": "ghp_dlg9githubmarker000000000000000000",
    "CORVIN_WDAT_KEY": "dlg9-wdat-marker",
    "SOME_BOT_TOKEN": "dlg9-bot-marker",
}


@pytest.fixture(autouse=True)
def _gates_and_forge_only_here(monkeypatch):
    """Same isolation as test_node_model_engine_e2e: forge on the path for this test only, and
    every module newly imported from forge / bridges/shared dropped again afterwards."""
    before = set(sys.modules)
    monkeypatch.syspath_prepend(_FORGE)
    yield
    for name in set(sys.modules) - before:
        origin = getattr(sys.modules.get(name), "__file__", None) or ""
        if name == "forge" or name.startswith("forge.") or origin.startswith((_FORGE, _SHARED)):
            sys.modules.pop(name, None)


FAKE_CLAUDE = """#!/usr/bin/env python3
import json, os, sys
classifier = "--max-turns" in sys.argv
with open(os.environ["FAKE_CLAUDE_LOG"], "a") as fh:
    fh.write(json.dumps({"argv": sys.argv[1:], "env": dict(os.environ)}) + "\\n")
reply = ({"violated_rule_id": "", "confidence": 0.0, "reason": "ok"} if classifier
         else {"ok": True})
print(json.dumps({"type": "result", "subtype": "success", "is_error": False,
                  "result": json.dumps(reply), "usage": {"input_tokens": 1, "output_tokens": 1},
                  "modelUsage": {"m": {}}}))
"""

WORKFLOW = """awp: "1.0.0"
workflow:
  name: worker_env_e2e
  description: One agent node.
orchestration:
  engine: dag
  graph:
    - id: only
      type: agent
      agent: a
      instructions: "MARK-ENV"
"""


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    claude = bindir / "claude"
    claude.write_text(FAKE_CLAUDE, encoding="utf-8")
    claude.chmod(claude.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "spawns.jsonl"
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    for name, value in _SECRETS.items():
        monkeypatch.setenv(name, value)
    return log


def _node_spawns(log: Path) -> list[dict]:
    recs = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [r for r in recs if "--max-turns" not in r["argv"]]


def test_agent_node_spawn_is_mcp_isolated_and_secret_free(cli_env, tmp_path):
    from corvin_workflows.engines_claude import ClaudeCliEngine

    wf = tmp_path / "wf.awp.yaml"
    wf.write_text(WORKFLOW, encoding="utf-8")
    res = DAGRunner(load_workflow(wf), engine=ClaudeCliEngine(tenant_id="_default"),
                    tenant_id="_default", workflow_id="wf-env").run({})
    assert res.state == "complete", getattr(res, "error", res)

    spawns = _node_spawns(cli_env)
    assert len(spawns) == 1
    argv, env = spawns[0]["argv"], spawns[0]["env"]
    assert "--strict-mcp-config" in argv
    assert "--mcp-config" not in argv
    for name, value in _SECRETS.items():
        assert name not in env, f"{name} reached a workflow node"
        assert value not in json.dumps(spawns[0])
    assert env["CORVIN_TENANT_ID"] == "_default"


def test_engine_without_a_tenant_refuses_instead_of_reading_the_env(cli_env):
    from corvin_workflows.engines_claude import ClaudeCliEngine, SpawnRefusedError

    with pytest.raises(SpawnRefusedError, match="no tenant bound"):
        ClaudeCliEngine().spawn(EngineCall(agent="a", instructions="MARK-NO-TENANT"))
    assert not cli_env.exists() or not _node_spawns(cli_env)


def test_cli_binds_its_process_tenant_explicitly(cli_env, monkeypatch):
    from corvin_workflows import cli

    monkeypatch.setenv("CORVIN_TENANT_ID", "beta")
    assert cli._build_engine("claude").tenant_id == "beta"
