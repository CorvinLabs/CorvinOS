"""ADR-2308 / T-0328 — `python -m corvin_workflows run` as a real subprocess: the node model reaches the
`claude` argv the CLI (and the scheduler, which shares this path) spawns, and the audit event lands.

The `claude` on PATH is a script that records its argv and answers with the CLI's JSON envelope;
the L44 classifier calls of the pre-spawn gates are answered like a healthy model. Sandboxed
CORVIN_HOME (repo-root conftest); no network, no model.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
REPORTED = "reported-model-1"

FAKE_CLAUDE = """#!/usr/bin/env python3
import json, os, sys
argv = sys.argv[1:]
with open(os.environ["FAKE_CLAUDE_LOG"], "a") as fh:
    fh.write(json.dumps({"argv": argv}) + "\\n")
reply = ({"violated_rule_id": "", "confidence": 0.0, "reason": "ok"} if "--max-turns" in argv
         else {"ok": True})
print(json.dumps({
    "type": "result", "subtype": "success", "is_error": False, "result": json.dumps(reply),
    "total_cost_usd": 0.01,
    "usage": {"input_tokens": 3, "output_tokens": 2, "cache_read_input_tokens": 0,
              "cache_creation_input_tokens": 0},
    "modelUsage": {"%s": {"outputTokens": 2}},
}))
""" % REPORTED

WORKFLOW = """awp: "1.0.0"
workflow:
  name: cli_node_model
  description: One node with a model and one without, run from the command line.
orchestration:
  engine: dag
  graph:
    - id: with_model
      type: agent
      agent: a
      instructions: "Do the first thing."
      model: sonnet
    - id: plain
      type: agent
      agent: b
      instructions: "Do the second thing."
"""


def test_cli_run_passes_the_node_model_and_writes_the_resolution_event(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    claude = bindir / "claude"
    claude.write_text(FAKE_CLAUDE)
    claude.chmod(claude.stat().st_mode | stat.S_IEXEC)
    wf = tmp_path / "cli_node_model.awp.yaml"
    wf.write_text(WORKFLOW)
    log = tmp_path / "argv.jsonl"

    env = dict(os.environ)  # keeps the sandboxed CORVIN_HOME / XDG / anchor key of the test session
    env.update({
        "PATH": f"{bindir}{os.pathsep}{env.get('PATH', '')}",
        "FAKE_CLAUDE_LOG": str(log),
        "CORVIN_TENANT_ID": "_default",
        # what the launcher gives the service: the package, and the forge package the audit writer needs
        "PYTHONPATH": os.pathsep.join([str(REPO / "core" / "workflows"), str(REPO / "corvin_operator" / "forge")]),
    })
    env.pop("CORVIN_HELPER_MODEL_WORKFLOW_NODE", None)
    proc = subprocess.run(
        [sys.executable, "-m", "corvin_workflows", "run", str(wf), "--engine", "claude", "--format", "json"],
        capture_output=True, text=True, env=env, cwd=str(tmp_path), timeout=120)
    assert proc.returncode == 0, proc.stderr[-1500:]

    calls = [json.loads(line)["argv"] for line in log.read_text().splitlines() if line.strip()]
    nodes = [c for c in calls if "--max-turns" not in c]
    assert len(nodes) == 2, nodes
    models = [c[c.index("--model") + 1] for c in nodes]
    assert models.count("haiku") == 1                 # the node without a model: the alias it always had
    assert [m for m in models if m != "haiku"][0].startswith("claude-sonnet"), models   # `sonnet` -> a real id
    assert all(c[c.index("--output-format") + 1] == "json" for c in nodes)

    # the audit event is on the sandbox chain, written by the subprocess
    sys.path.insert(0, str(REPO / "corvin_operator" / "forge"))
    try:
        from forge import paths as fp
        rows = [json.loads(line) for line in Path(fp.tenant_audit_chain("_default")).read_text().splitlines()
                if line.strip()]
    finally:
        sys.path.remove(str(REPO / "corvin_operator" / "forge"))
        for name in [n for n in sys.modules if n == "forge" or n.startswith("forge.")]:
            sys.modules.pop(name, None)
    resolved = [r["details"] for r in rows if r.get("event_type") == "workflow.node_model_resolved"
                and r["details"].get("node_id") in ("with_model", "plain")]
    assert {d["node_id"]: d["source"] for d in resolved} == {"with_model": "node", "plain": "site_default"}

    # T-0329: the profile those two spawns wrote is read by `corvin tde workflow-loss`, a real subprocess
    # against the same sandboxed CORVIN_HOME. Production entries are proxies, so the report has no loss.
    launcher_env = dict(env)
    launcher_env["PYTHONPATH"] = os.pathsep.join([str(REPO / "ops" / "launcher"), env["PYTHONPATH"]])
    rep = subprocess.run([sys.executable, "-m", "corvin", "tde", "workflow-loss", "--tenant", "_default"],
                         capture_output=True, text=True, env=launcher_env, cwd=str(tmp_path), timeout=120)
    assert rep.returncode == 0, rep.stderr[-1500:]
    report = json.loads(rep.stdout)
    assert report["status"] in ("underpowered", "ok") and report["loss"] is None
    assert report["entries"] >= 2
    classes = {row["node_class"] for row in report["classes"]}
    assert "agent|n|<8k|json" in classes and all(c.endswith("|json") for c in classes)
    assert {row["model_id"] for row in report["classes"]} >= {REPORTED}
    assert all(row["underpowered"] for row in report["classes"] if row["runs"] < report["min_runs"])
