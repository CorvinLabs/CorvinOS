"""ADR-0190 M4/M5/M6 — E2E for the consolidated orchestration MCP server.

Drives the real stdio JSON-RPC loop (mirrors core/delegate/tests/test_mcp_
server.py's ``_drive()`` pattern) against real dependencies — a real
DAGRunner executing a real (deterministic, no-LLM) code node, a real
RemoteEndpointRegistry reading a temp endpoints dir, a real
run_acs_workflow(dry_run=True) validation pass — rather than mocking the
engines, so this proves the wiring, not just the dispatch shape.
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_PKG_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PKG_DIR))
_REPO = _PKG_DIR.parents[1]
sys.path.insert(0, str(_REPO / "core" / "workflows"))
sys.path.insert(0, str(_REPO / "operator" / "bridges" / "shared"))
sys.path.insert(0, str(_REPO / "operator" / "forge"))

from corvin_orchestration.mcp_server import (  # noqa: E402
    METHOD_NOT_FOUND,
    OrchestrationServer,
    _tool_definitions,
)


def _drive(server: OrchestrationServer, messages: list[dict]) -> list[dict]:
    stdin = io.StringIO("\n".join(json.dumps(m) for m in messages) + "\n")
    stdout = io.StringIO()
    server._stdin = stdin
    server._stdout = stdout
    server.serve()
    return [json.loads(ln) for ln in stdout.getvalue().splitlines() if ln.strip()]


def _call(name: str, args: dict, msgid: int = 1) -> dict:
    return {"jsonrpc": "2.0", "id": msgid, "method": "tools/call",
            "params": {"name": name, "arguments": args}}


class TestOrchestrationServerBase(unittest.TestCase):
    def setUp(self):
        self.home_tmp = tempfile.TemporaryDirectory(prefix="corvin-m456-")
        self.home = Path(self.home_tmp.name)
        (self.home / "tenants" / "_default" / "workflows").mkdir(parents=True)
        self.endpoints_tmp = tempfile.TemporaryDirectory(prefix="corvin-m456-eps-")
        self.env_patch = mock.patch.dict(os.environ, {
            "CORVIN_HOME": str(self.home),
            "REMOTE_ENDPOINTS_DIR": self.endpoints_tmp.name,
        })
        self.env_patch.start()

    def tearDown(self):
        self.env_patch.stop()
        self.home_tmp.cleanup()
        self.endpoints_tmp.cleanup()

    def _server(self) -> OrchestrationServer:
        return OrchestrationServer(stdin=io.StringIO(), stdout=io.StringIO(), stderr=io.StringIO())


class TestHandshakeAndListing(TestOrchestrationServerBase):
    def test_initialize_and_tools_list(self):
        server = self._server()
        resp = _drive(server, [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"clientInfo": {"name": "test"}}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        ])
        self.assertEqual(resp[0]["result"]["serverInfo"]["name"], "corvin-orchestration")
        names = {t["name"] for t in resp[1]["result"]["tools"]}
        self.assertEqual(names, {
            "workflow_run", "workflow_resume", "workflow_list_paused",
            "a2a_send", "a2a_list_endpoints", "acs_delegate",
        })

    def test_unknown_tool_returns_method_not_found(self):
        server = self._server()
        resp = _drive(server, [_call("bogus_tool", {})])
        self.assertEqual(resp[0]["error"]["code"], METHOD_NOT_FOUND)


class TestWorkflowTools(TestOrchestrationServerBase):
    def _write_smoke_workflow(self, wid: str = "m5_smoke") -> None:
        yaml_text = """
awp: "1.0.0"
workflow:
  name: %s
  description: minimal deterministic smoke test for ADR-0190 M5 wiring
inputs:
  a: {type: number}
  b: {type: number}
orchestration:
  engine: dag
  graph:
    - id: add_numbers
      type: code
      depends_on: []
      language: python3
      inputs: {a: a, b: b}
      source: |
        def main(a: float, b: float) -> dict:
            return {"sum": a + b}
      outputs: [sum]
""" % wid
        (self.home / "tenants" / "_default" / "workflows" / f"{wid}.awp.yaml").write_text(
            yaml_text, encoding="utf-8",
        )

    def test_workflow_run_executes_real_code_node(self):
        self._write_smoke_workflow()
        server = self._server()
        resp = _drive(server, [_call("workflow_run", {
            "workflow_id": "m5_smoke", "inputs": {"a": 3, "b": 4},
        })])
        payload = json.loads(resp[0]["result"]["content"][0]["text"])
        self.assertFalse(resp[0]["result"]["isError"], payload)
        self.assertEqual(payload["status"], "complete")
        self.assertEqual(payload["final_state"]["add_numbers"]["sum"], 7)

    def test_workflow_run_writes_to_audit_chain(self):
        self._write_smoke_workflow()
        server = self._server()
        _drive(server, [_call("workflow_run", {
            "workflow_id": "m5_smoke", "inputs": {"a": 1, "b": 1},
        })])
        audit_path = self.home / "tenants" / "_default" / "audit.jsonl"
        self.assertTrue(audit_path.exists())
        self.assertIn("workflow.run.started", audit_path.read_text())

    def test_workflow_run_rejects_unknown_workflow_id(self):
        server = self._server()
        resp = _drive(server, [_call("workflow_run", {"workflow_id": "does_not_exist"})])
        self.assertIn("not found", resp[0]["error"]["message"])

    def test_workflow_run_rejects_malformed_workflow_id(self):
        server = self._server()
        resp = _drive(server, [_call("workflow_run", {"workflow_id": "../../etc/passwd"})])
        self.assertIn("must match", resp[0]["error"]["message"])

    def test_workflow_run_malformed_yaml_is_invalid_params_not_internal_error(self):
        """Verification finding: yaml.YAMLError is not a subclass of
        (OSError, ValueError), so a corrupted .awp.yaml file used to escape
        the local except block and surface as an opaque INTERNAL_ERROR from
        the top-level handler instead of a clean 'invalid workflow'."""
        (self.home / "tenants" / "_default" / "workflows" / "broken.awp.yaml").write_text(
            "awp: \"1.0.0\"\nworkflow:\n  name: broken\n  bad_indent:\n\tthis: is not valid yaml\n",
            encoding="utf-8",
        )
        server = self._server()
        resp = _drive(server, [_call("workflow_run", {"workflow_id": "broken"})])
        self.assertIn("error", resp[0])
        self.assertEqual(resp[0]["error"]["code"], -32602)  # INVALID_PARAMS, not -32603
        self.assertIn("invalid workflow", resp[0]["error"]["message"])

    def test_workflow_list_paused_empty(self):
        server = self._server()
        resp = _drive(server, [_call("workflow_list_paused", {})])
        payload = json.loads(resp[0]["result"]["content"][0]["text"])
        self.assertEqual(payload["paused"], [])

    def test_workflow_resume_unknown_run_id_is_invalid_params(self):
        server = self._server()
        resp = _drive(server, [_call("workflow_resume", {"run_id": "nope", "reply": "ok"})])
        self.assertIn("no paused run found", resp[0]["error"]["message"])


class TestA2ATools(TestOrchestrationServerBase):
    def _write_endpoint(self, endpoint_id: str = "test-peer", label: str = "Test Peer") -> None:
        cfg = {
            "endpoint_id": endpoint_id,
            "url": "https://example.invalid/a2a",
            "hmac_key": "x" * 32,
            "recv_key": "y" * 32,
            "enabled": True,
            "default_ttl_s": 60,
            "label": label,
        }
        path = Path(self.endpoints_tmp.name) / f"{endpoint_id}.json"
        path.write_text(json.dumps(cfg), encoding="utf-8")
        path.chmod(0o600)

    def test_a2a_list_endpoints_finds_configured_peer(self):
        self._write_endpoint()
        server = self._server()
        resp = _drive(server, [_call("a2a_list_endpoints", {})])
        payload = json.loads(resp[0]["result"]["content"][0]["text"])
        self.assertEqual(payload["endpoints"], [
            {"endpoint_id": "test-peer", "label": "Test Peer", "enabled": True},
        ])

    def test_a2a_list_endpoints_empty_dir(self):
        server = self._server()
        resp = _drive(server, [_call("a2a_list_endpoints", {})])
        payload = json.loads(resp[0]["result"]["content"][0]["text"])
        self.assertEqual(payload["endpoints"], [])

    def test_a2a_send_unknown_endpoint_fails_cleanly_not_raise(self):
        server = self._server()
        resp = _drive(server, [_call("a2a_send", {
            "endpoint_id": "does-not-exist", "instruction": "ping",
        })])
        self.assertTrue(resp[0]["result"]["isError"])
        payload = json.loads(resp[0]["result"]["content"][0]["text"])
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "error")

    def test_a2a_send_missing_args_is_invalid_params(self):
        server = self._server()
        resp = _drive(server, [_call("a2a_send", {"endpoint_id": "x"})])
        self.assertIn("required", resp[0]["error"]["message"])


class TestACSTool(TestOrchestrationServerBase):
    def test_acs_delegate_dry_run_validates_without_spending_quota(self):
        server = self._server()
        resp = _drive(server, [_call("acs_delegate", {"task": "say hello", "dry_run": True})])
        payload = json.loads(resp[0]["result"]["content"][0]["text"])
        self.assertFalse(resp[0]["result"]["isError"], payload)
        self.assertEqual(payload["engine"], "acs")
        self.assertIn("dry_run", payload["summary"])

    def test_acs_delegate_missing_task_is_invalid_params(self):
        server = self._server()
        resp = _drive(server, [_call("acs_delegate", {})])
        self.assertIn("required", resp[0]["error"]["message"])


class TestToolDefinitionsHelper(unittest.TestCase):
    def test_tool_definitions_shape(self):
        for tool in _tool_definitions():
            self.assertIn("name", tool)
            self.assertIn("description", tool)
            self.assertIn("inputSchema", tool)


if __name__ == "__main__":
    unittest.main()
