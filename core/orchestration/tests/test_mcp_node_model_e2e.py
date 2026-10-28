"""ADR-2308 / T-0328 — the orchestration MCP server's `workflow_run` passes a node's model to the real
`claude` argv.

Same real stdio JSON-RPC loop as test_mcp_server.py (real DAGRunner, real ClaudeCliEngine so the
pre-spawn gates run); the `claude` on PATH is a script that records its argv and answers with the
CLI's JSON envelope. No network, no model.
"""
from __future__ import annotations

import json
import os
import stat
import unittest
from pathlib import Path

from test_mcp_server import TestOrchestrationServerBase, _call, _drive

FAKE_CLAUDE = """#!/usr/bin/env python3
import json, os, sys
argv = sys.argv[1:]
with open(os.environ["FAKE_CLAUDE_LOG"], "a") as fh:
    fh.write(json.dumps({"argv": argv}) + "\\n")
reply = ({"violated_rule_id": "", "confidence": 0.0, "reason": "ok"} if "--max-turns" in argv
         else {"ok": True})
print(json.dumps({"type": "result", "subtype": "success", "is_error": False,
                  "result": json.dumps(reply), "total_cost_usd": 0.01,
                  "usage": {"input_tokens": 3, "output_tokens": 2},
                  "modelUsage": {"reported-model-1": {"outputTokens": 2}}}))
"""

WORKFLOW = """
awp: "1.0.0"
workflow:
  name: mcp_node_model
  description: One node with a model and one without, run through the MCP workflow_run tool.
orchestration:
  engine: dag
  graph:
    - id: with_model
      type: agent
      agent: a
      instructions: "Do the first thing."
      model: opus
    - id: plain
      type: agent
      agent: b
      instructions: "Do the second thing."
"""


class TestMcpWorkflowRunNodeModel(TestOrchestrationServerBase):
    def setUp(self):
        super().setUp()
        bindir = self.home / "bin"
        bindir.mkdir()
        claude = bindir / "claude"
        claude.write_text(FAKE_CLAUDE)
        claude.chmod(claude.stat().st_mode | stat.S_IEXEC)
        self.log = self.home / "calls.jsonl"
        self._saved = {k: os.environ.get(k) for k in
                       ("PATH", "FAKE_CLAUDE_LOG", "CORVIN_HELPER_MODEL_WORKFLOW_NODE", "CORVIN_TENANT_ID")}
        os.environ["PATH"] = f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}"
        os.environ["FAKE_CLAUDE_LOG"] = str(self.log)
        os.environ.pop("CORVIN_HELPER_MODEL_WORKFLOW_NODE", None)
        os.environ["CORVIN_TENANT_ID"] = "_default"
        (self.home / "tenants" / "_default" / "workflows" / "mcp_node_model.awp.yaml").write_text(
            WORKFLOW, encoding="utf-8")

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        super().tearDown()

    def test_workflow_run_passes_the_node_model_to_the_claude_argv(self):
        from model_selector import tier_model

        resp = _drive(self._server(), [_call("workflow_run", {"workflow_id": "mcp_node_model", "inputs": {}})])
        payload = json.loads(resp[0]["result"]["content"][0]["text"])
        self.assertFalse(resp[0]["result"]["isError"], payload)
        self.assertEqual(payload["status"], "complete", payload)

        calls = [json.loads(line)["argv"] for line in self.log.read_text().splitlines() if line.strip()]
        nodes = [c for c in calls if "--max-turns" not in c]
        self.assertEqual(len(nodes), 2, nodes)
        models = sorted(c[c.index("--model") + 1] for c in nodes)
        self.assertEqual(models, sorted([tier_model("opus"), "haiku"]))
        for c in nodes:
            self.assertEqual(c[c.index("--output-format") + 1], "json")


if __name__ == "__main__":
    unittest.main(verbosity=2)
