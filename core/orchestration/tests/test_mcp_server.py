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
import re
import sys
import tempfile
import threading
import time
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
            # The smoke workflows here run a trivial deterministic `code` node
            # (def main(a, b): return {"sum": a + b}). A `code` node fails closed
            # when bwrap is absent — the correct security default, which we do
            # NOT weaken in the product. CI runners have no bwrap ("sandbox tier:
            # docker (bwrap unavailable)"), so this test opts THIS controlled
            # arithmetic payload into the rlimits-only fallback. No-op wherever
            # bwrap exists (bwrap always takes precedence — see code_exec.py).
            "CORVIN_ALLOW_UNSANDBOXED_CODE": "1",
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

    def test_workflow_run_refused_at_concurrency_limit(self):
        """ADR-0190 gate-reuse rule: workflow_run must enforce the SAME
        workflows_concurrent license limit the console REST route enforces —
        previously a chat turn could start unlimited parallel runs
        (adversarial-review finding, 2026-07-12)."""
        import corvin_orchestration.mcp_server as m
        self._write_smoke_workflow()
        server = self._server()
        with mock.patch.object(m, "_lic_get_limit", lambda key: 1), \
             mock.patch.object(m, "_count_console_running", lambda tid: 1):
            resp = _drive(server, [_call("workflow_run", {
                "workflow_id": "m5_smoke", "inputs": {"a": 1, "b": 2},
            })])
        payload = json.loads(resp[0]["result"]["content"][0]["text"])
        self.assertTrue(resp[0]["result"]["isError"])
        self.assertEqual(payload["status"], "refused")
        self.assertIn("workflows_concurrent", payload["error"])

    def test_workflow_run_concurrency_gate_fails_closed(self):
        """An unreadable runs registry must refuse, not grant unlimited."""
        import corvin_orchestration.mcp_server as m
        self._write_smoke_workflow()
        server = self._server()

        def _boom(tid):
            raise OSError("runs tree unreadable")

        with mock.patch.object(m, "_lic_get_limit", lambda key: 5), \
             mock.patch.object(m, "_count_console_running", _boom):
            resp = _drive(server, [_call("workflow_run", {
                "workflow_id": "m5_smoke", "inputs": {"a": 1, "b": 2},
            })])
        payload = json.loads(resp[0]["result"]["content"][0]["text"])
        self.assertTrue(resp[0]["result"]["isError"])
        self.assertIn("fail-closed", payload["error"])

    def test_workflow_run_unlimited_tier_skips_gate(self):
        import corvin_orchestration.mcp_server as m
        self._write_smoke_workflow()
        server = self._server()
        with mock.patch.object(m, "_lic_get_limit", lambda key: None):
            resp = _drive(server, [_call("workflow_run", {
                "workflow_id": "m5_smoke", "inputs": {"a": 3, "b": 4},
            })])
        payload = json.loads(resp[0]["result"]["content"][0]["text"])
        self.assertFalse(resp[0]["result"]["isError"], payload)
        self.assertEqual(payload["status"], "complete")


class TestWorkflowResumeApproverBinding(TestOrchestrationServerBase):
    """WF-A3: a `workflow_resume` call must only be honored by the specific
    chat/session an `ask_human` checkpoint's `approver` field names.

    Fixed 2026-07-13 — `_call_workflow_resume()` previously hardcoded
    `replier=None`, which `resume_workflow()` treats as an always-authorized
    privileged-owner caller (the console's posture), so ANY chat participant
    able to reach this chat-exposed MCP tool could resume an approval
    directed at a different, specific chat_id. The fix derives `replier`
    from `CORVIN_CHANNEL_ID` — the identity the bridge adapter sets on this
    server process at spawn time (see `adapter.py::_build_spawn_env`), which
    a `tools/call` argument cannot carry, because a chat participant could
    then simply claim to be whoever they like.
    """

    def _write_ask_human_workflow(self, wid: str = "approval_flow") -> None:
        yaml_text = """
awp: "1.0.0"
workflow:
  name: %s
  description: pauses for a human approval, bound to a specific chat_id
inputs: {}
orchestration:
  engine: dag
  graph:
    - id: confirm
      type: ask_human
      depends_on: []
      channel: discord
      chat_id: "owner-chat"
      prompt: "Approve the expense?"
      expect: {field: confirmed, type: boolean}
""" % wid
        (self.home / "tenants" / "_default" / "workflows" / f"{wid}.awp.yaml").write_text(
            yaml_text, encoding="utf-8",
        )

    def _run_to_pause(self) -> str:
        self._write_ask_human_workflow()
        server = self._server()
        run_resp = _drive(server, [_call("workflow_run", {
            "workflow_id": "approval_flow", "inputs": {},
        })])
        payload = json.loads(run_resp[0]["result"]["content"][0]["text"])
        self.assertEqual(payload["status"], "paused")
        return payload["run_id"]

    def test_paused_checkpoint_binds_approver_to_directed_chat_id(self) -> None:
        """Sanity check: the checkpoint DOES record a specific approver
        (owner-chat) — the WF-A3 binding this MCP call site now enforces is
        real and present, not hypothetical."""
        import corvin_orchestration.mcp_server as m
        run_id = self._run_to_pause()
        ckpt = m._awp_checkpoint.load(run_id, tenant_id="_default")
        self.assertEqual(ckpt["approver"], "owner-chat")

    def test_workflow_resume_rejects_reply_with_no_caller_identity(self) -> None:
        """No `CORVIN_CHANNEL_ID` at all (e.g. a stripped/broken spawn env)
        must NOT fall back to the privileged `replier=None` bypass — an
        unidentified caller is rejected against a bound approver exactly
        like a genuinely-wrong caller would be. This is the flipped form of
        the pre-fix regression test: before the fix, this exact call
        sequence returned status="complete", because `replier=None` was
        hardcoded at the `_call_workflow_resume` call site and
        `resume_workflow()` treats `replier=None` as always-authorized."""
        run_id = self._run_to_pause()
        server = self._server()
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CORVIN_CHANNEL_ID", None)
            resume_resp = _drive(server, [_call("workflow_resume", {
                "run_id": run_id, "reply": "yes",
            })])
        self.assertIn("unauthorized reply", resume_resp[0]["error"]["message"])

    def test_workflow_resume_rejects_wrong_replier(self) -> None:
        """A different chat than the one the approval was directed to must be
        rejected, even though it reaches the tool through the same persona
        and MCP server."""
        run_id = self._run_to_pause()
        with mock.patch.dict(os.environ, {"CORVIN_CHANNEL_ID": "discord:someone-else"}):
            server = self._server()
            resume_resp = _drive(server, [_call("workflow_resume", {
                "run_id": run_id, "reply": "yes",
            })])
        self.assertIn("unauthorized reply", resume_resp[0]["error"]["message"])

    def test_workflow_resume_allows_matching_replier(self) -> None:
        """The chat the approval was actually directed to (bridge-prefixed in
        `CORVIN_CHANNEL_ID`, bare in the checkpoint's `approver` — see
        `_replier_from_channel_id()`) may resume it."""
        run_id = self._run_to_pause()
        with mock.patch.dict(os.environ, {"CORVIN_CHANNEL_ID": "discord:owner-chat"}):
            server = self._server()
            resume_resp = _drive(server, [_call("workflow_resume", {
                "run_id": run_id, "reply": "yes",
            })])
        payload = json.loads(resume_resp[0]["result"]["content"][0]["text"])
        self.assertFalse(resume_resp[0]["result"]["isError"], payload)
        self.assertEqual(payload["status"], "complete")
        self.assertEqual(payload["final_state"]["confirm"]["confirmed"], True)

    def test_workflow_resume_schema_has_no_caller_identity_field(self) -> None:
        """By design, not a gap: caller identity comes from the trusted
        `CORVIN_CHANNEL_ID` spawn-env value (see
        `OrchestrationServer.__init__`), never from a `tools/call` argument —
        a schema field here would just let any chat participant claim to be
        whoever they like."""
        from corvin_orchestration.mcp_server import _tool_definitions
        resume_tool = next(t for t in _tool_definitions() if t["name"] == "workflow_resume")
        props = set(resume_tool["inputSchema"]["properties"])
        self.assertEqual(props, {"run_id", "reply", "tenant_id", "budget_s"})


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


class TestBackgroundCompletionNotify(TestOrchestrationServerBase):
    """A run that outlives its wall-clock budget must not become an
    unrecoverable orphan: it must (a) register a completion_notify record so
    the originating messenger is told when it finishes, and (b) hand back a
    real run_id in the timeout response itself, even though _run_with_budget
    returns before the underlying run does. See the background-completion
    contract docstring on `_run_with_budget`/`_completion_notify_hooks`.

    These tests bypass `_clamp`'s real budget_s floor (10s) via a passthrough
    patch so the "run outlives its budget" scenario can be driven with a
    sub-second sleep instead of a real 10+ second wait — this tests the
    TIMEOUT MECHANISM itself (does exceeding budget_s trigger registration +
    a usable run_id), not the specific production floor, which is covered by
    the schema/`_clamp` unit-level guarantees elsewhere.
    """

    _RUN_ID_RE = re.compile(r"^[0-9a-f]{16}$")

    @staticmethod
    def _passthrough_clamp(value, *, lo, hi, default):  # noqa: ARG004
        return int(value) if value is not None else default

    def _notify_record_path(self, run_id: str) -> Path:
        return self.home / "pending_notifications" / f"{run_id}.json"

    def _read_record(self, run_id: str) -> dict:
        return json.loads(self._notify_record_path(run_id).read_text(encoding="utf-8"))

    def _write_smoke_workflow(self, wid: str = "m5_smoke") -> None:
        yaml_text = """
awp: "1.0.0"
workflow:
  name: %s
  description: minimal deterministic smoke test
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

    def _write_ask_human_workflow(self, wid: str = "approval_flow") -> None:
        yaml_text = """
awp: "1.0.0"
workflow:
  name: %s
  description: pauses for a human approval
inputs: {}
orchestration:
  engine: dag
  graph:
    - id: confirm
      type: ask_human
      depends_on: []
      channel: discord
      chat_id: "owner-chat"
      prompt: "Approve?"
      expect: {field: confirmed, type: boolean}
""" % wid
        (self.home / "tenants" / "_default" / "workflows" / f"{wid}.awp.yaml").write_text(
            yaml_text, encoding="utf-8",
        )

    def test_workflow_run_timeout_registers_completion_notify_and_returns_run_id(self):
        import corvin_orchestration.mcp_server as m
        self._write_smoke_workflow()
        server = self._server()

        real_run = m._DAGRunner.run

        def _slow_run(self_runner, *a, **kw):
            time.sleep(0.6)
            return real_run(self_runner, *a, **kw)

        origin_patch = mock.patch.dict(os.environ, {
            "CORVIN_CHANNEL_ID": "discord:test-chat-id",
            "CORVIN_ORIGIN_SENDER": "user-42",
        })
        origin_patch.start()
        self.addCleanup(origin_patch.stop)

        with mock.patch.object(m._DAGRunner, "run", _slow_run), \
             mock.patch.object(m, "_clamp", self._passthrough_clamp):
            resp = _drive(server, [_call("workflow_run", {
                "workflow_id": "m5_smoke", "inputs": {"a": 1, "b": 2}, "budget_s": 0.05,
            })])

        payload = json.loads(resp[0]["result"]["content"][0]["text"])
        self.assertTrue(resp[0]["result"]["isError"])
        self.assertEqual(payload["status"], "timeout")
        run_id = payload["run_id"]
        self.assertTrue(self._RUN_ID_RE.match(run_id or ""), run_id)
        # Bug 3 regression: the timeout envelope must only promise delivery
        # when hooks were actually registered for this call.
        self.assertIn("It will be delivered to the originating chat", payload["error"])
        self.assertIn(f"run_id={run_id}", payload["error"])

        # The record must exist immediately (on_timeout runs synchronously,
        # strictly before the timeout response is returned to the caller) —
        # NOT only after the background thread eventually finishes.
        rec = self._read_record(run_id)
        self.assertEqual(rec["state"], "pending")
        self.assertEqual(rec["channel"], "discord")
        self.assertEqual(rec["chat_id"], "test-chat-id")
        self.assertEqual(rec["sender"], "user-42")
        self.assertEqual(rec["tenant_id"], "_default")
        self.assertIn("m5_smoke", rec["label"])
        # Bug 2 regression: on_timeout must claim() the record (stamp THIS
        # process as producer) so the dead-producer reap in
        # completion_notify.deliver_ready — which explicitly skips
        # producer_pid=None records — can ever rescue an orphaned pending
        # record if this MCP server process is killed before the background
        # thread finishes.
        self.assertEqual(rec["producer_pid"], os.getpid())
        self.assertIsNotNone(rec["producer_boot"])

        # Give the background thread time to actually finish, then confirm
        # mark_done fired and moved the record to ready-for-delivery.
        deadline = time.time() + 5
        while time.time() < deadline and self._read_record(run_id)["state"] == "pending":
            time.sleep(0.05)
        rec = self._read_record(run_id)
        self.assertEqual(rec["state"], "ready")
        self.assertTrue(rec["ok"])
        self.assertIn("completed", rec["text"])

    def test_workflow_run_within_budget_does_not_register_completion_notify(self):
        """The common (non-timeout) case must not produce a redundant
        completion notification on top of the normal synchronous reply."""
        import corvin_orchestration.mcp_server as m  # noqa: F401
        self._write_smoke_workflow()
        server = self._server()

        origin_patch = mock.patch.dict(os.environ, {
            "CORVIN_CHANNEL_ID": "discord:test-chat-id",
            "CORVIN_ORIGIN_SENDER": "user-42",
        })
        origin_patch.start()
        self.addCleanup(origin_patch.stop)

        resp = _drive(server, [_call("workflow_run", {
            "workflow_id": "m5_smoke", "inputs": {"a": 1, "b": 2},
        })])
        payload = json.loads(resp[0]["result"]["content"][0]["text"])
        self.assertFalse(resp[0]["result"]["isError"], payload)
        self.assertEqual(payload["status"], "complete")
        run_id = payload["run_id"]
        self.assertFalse(self._notify_record_path(run_id).exists())

    def test_workflow_run_timeout_without_messenger_origin_returns_run_id_but_no_record(self):
        """No CORVIN_CHANNEL_ID/CORVIN_ORIGIN_SENDER in the environment (e.g.
        a console/REST-originated call) — the timeout envelope must still
        carry a usable run_id, but no completion_notify record should exist
        (nobody to deliver it to)."""
        import corvin_orchestration.mcp_server as m
        self._write_smoke_workflow()
        server = self._server()

        real_run = m._DAGRunner.run

        def _slow_run(self_runner, *a, **kw):
            time.sleep(0.3)
            return real_run(self_runner, *a, **kw)

        with mock.patch.object(m._DAGRunner, "run", _slow_run), \
             mock.patch.object(m, "_clamp", self._passthrough_clamp):
            resp = _drive(server, [_call("workflow_run", {
                "workflow_id": "m5_smoke", "inputs": {"a": 1, "b": 2}, "budget_s": 0.05,
            })])

        payload = json.loads(resp[0]["result"]["content"][0]["text"])
        self.assertEqual(payload["status"], "timeout")
        run_id = payload["run_id"]
        self.assertTrue(self._RUN_ID_RE.match(run_id or ""), run_id)
        self.assertFalse(self._notify_record_path(run_id).exists())
        # Bug 3 regression: with no hooks registered (no messenger origin),
        # the envelope must NOT claim a delivery it cannot make — it must say
        # so honestly instead of unconditionally promising delivery.
        self.assertNotIn("It will be delivered", payload["error"])
        self.assertIn("No automatic delivery is configured", payload["error"])

    def test_workflow_resume_timeout_registers_completion_notify_and_returns_run_id(self):
        import corvin_orchestration.mcp_server as m
        self._write_ask_human_workflow()

        # This test exercises the completion-notify/timeout wiring, not the
        # WF-A3 approver check (covered separately by
        # TestWorkflowResumeApproverBinding) — CORVIN_CHANNEL_ID's chat_key
        # ("owner-chat") matches the checkpoint's own bound approver, set by
        # _write_ask_human_workflow's ask_human node, so the slow-but-
        # legitimate resume actually completes instead of being rejected as
        # an unauthorized replier. Must be patched BEFORE server construction:
        # OrchestrationServer.__init__ reads CORVIN_CHANNEL_ID once at
        # construction time (mirroring a real process reading its own spawn
        # env exactly once), so patching it after `self._server()` would
        # leave `caller_channel_id` empty regardless of the patched value.
        origin_patch = mock.patch.dict(os.environ, {
            "CORVIN_CHANNEL_ID": "discord:owner-chat",
            "CORVIN_ORIGIN_SENDER": "user-42",
        })
        origin_patch.start()
        self.addCleanup(origin_patch.stop)
        server = self._server()

        run_resp = _drive(server, [_call("workflow_run", {
            "workflow_id": "approval_flow", "inputs": {},
        })])
        run_id = json.loads(run_resp[0]["result"]["content"][0]["text"])["run_id"]
        # The pause itself is fast (well within budget) — no record from the
        # workflow_run call.
        self.assertFalse(self._notify_record_path(run_id).exists())

        real_resume = m._resume_workflow

        def _slow_resume(*a, **kw):
            time.sleep(0.6)
            return real_resume(*a, **kw)

        with mock.patch.object(m, "_resume_workflow", _slow_resume), \
             mock.patch.object(m, "_clamp", self._passthrough_clamp):
            resume_resp = _drive(server, [_call("workflow_resume", {
                "run_id": run_id, "reply": "yes", "budget_s": 0.05,
            })])

        payload = json.loads(resume_resp[0]["result"]["content"][0]["text"])
        self.assertTrue(resume_resp[0]["result"]["isError"])
        self.assertEqual(payload["status"], "timeout")
        self.assertEqual(payload["run_id"], run_id)
        self.assertIn("It will be delivered to the originating chat", payload["error"])

        rec = self._read_record(run_id)
        self.assertEqual(rec["state"], "pending")
        self.assertEqual(rec["channel"], "discord")
        self.assertIn(run_id, rec["label"])
        # Bug 2 regression: claim() must stamp this process as producer.
        self.assertEqual(rec["producer_pid"], os.getpid())

        deadline = time.time() + 5
        while time.time() < deadline and self._read_record(run_id)["state"] == "pending":
            time.sleep(0.05)
        rec = self._read_record(run_id)
        self.assertEqual(rec["state"], "ready")
        self.assertTrue(rec["ok"])
        self.assertIn("completed", rec["text"])


class TestRunWithBudgetFinishTimeoutRace(unittest.TestCase):
    """Bug 1 regression (register/mark_done ordering race).

    ``threading.Thread.join(timeout)`` only reports ``is_alive() == False``
    once the target has fully RETURNED — including its ``finally`` clause.
    That means the background thread's ``finally`` (which calls
    ``on_finish``) can already be RUNNING while the caller's ``t.is_alive()``
    check still reads True, so the adversarial ordering "``on_finish`` fires
    before ``on_timeout`` registers anything" is reachable even though
    ``_run_with_budget`` only takes the timeout branch when ``is_alive()``
    is True. Before the fix, that left the record permanently ``pending``:
    ``on_finish``'s ``mark_done`` no-op'd (nothing registered yet) and
    nothing ever re-notified once ``on_timeout``'s ``register()`` created
    the record afterwards.

    This test forces exactly that ordering deterministically — not via a
    hopeful sleep race, but by having the *first* ``on_finish`` invocation
    block (holding `_run_with_budget`'s internal hook lock) until the caller
    thread has certainly timed out and is waiting on that same lock — then
    asserts the fix's rescue re-fire delivers the real result instead of
    leaving the notification stuck.
    """

    def test_on_finish_ahead_of_on_timeout_register_is_rescued(self):
        import corvin_orchestration.mcp_server as m

        calls: list[tuple] = []
        calls_lock = threading.Lock()
        finish_started = threading.Event()
        release_finish = threading.Event()

        def fn():
            return "actual-result"

        def on_timeout():
            with calls_lock:
                calls.append(("on_timeout",))

        def on_finish(value, error):
            with calls_lock:
                calls.append(("on_finish", value, error))
                first_call = len(calls) == 1
            if first_call:
                # Signal that on_finish has started (i.e. the background
                # thread is inside `finally`, holding `_run_with_budget`'s
                # hook_lock) and then block — still holding that lock —
                # until the test is certain the caller thread has timed out
                # and is itself waiting on the same lock. This reproduces
                # "on_finish already ran before on_timeout's register()" with
                # certainty instead of hoping a real race lands that way.
                finish_started.set()
                self.assertTrue(release_finish.wait(timeout=5), "test setup timed out")

        result_box: dict[str, object] = {}

        def _drive() -> None:
            result_box["result"] = m._run_with_budget(
                fn, budget_s=0.01, run_id_hint="rid-race",
                on_timeout=on_timeout, on_finish=on_finish,
            )

        driver = threading.Thread(target=_drive)
        driver.start()

        self.assertTrue(finish_started.wait(timeout=5), "on_finish never started")
        # budget_s=0.01s is 50x smaller than this margin, so the driver
        # thread's `t.join(budget_s)` has certainly already timed out and
        # entered the "waiting on hook_lock" branch by the time we release.
        time.sleep(0.5)
        release_finish.set()
        driver.join(timeout=5)
        self.assertFalse(driver.is_alive(), "driver thread never finished")

        # on_finish fired first (the race), then on_timeout registered, then
        # the rescue re-fired on_finish with the real result/error — proving
        # the record is no longer orphaned in the caller's hands. (The actual
        # completion_notify record recovering to "ready" is covered by the
        # MCP-server-level tests in TestBackgroundCompletionNotify; this test
        # isolates the ordering guarantee itself.)
        self.assertEqual(len(calls), 3, calls)
        self.assertEqual(calls[0], ("on_finish", "actual-result", None))
        self.assertEqual(calls[1], ("on_timeout",))
        self.assertEqual(calls[2], ("on_finish", "actual-result", None))

        self.assertEqual(result_box["result"]["status"], "timeout")
        self.assertEqual(result_box["result"]["run_id"], "rid-race")


class TestToolDefinitionsHelper(unittest.TestCase):
    def test_tool_definitions_shape(self):
        for tool in _tool_definitions():
            self.assertIn("name", tool)
            self.assertIn("description", tool)
            self.assertIn("inputSchema", tool)


if __name__ == "__main__":
    unittest.main()
