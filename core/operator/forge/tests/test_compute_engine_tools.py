"""ADR-0190 M2 — compute_submit / compute_gate wiring into forge/mcp_server.py.

Before this milestone, ``compute_engine_tool_definitions()`` /
``COMPUTE_ENGINE_TOOLS_NAMES`` (ADR-0029, pipeline/HAC engines) were fully
coded in ``core/compute/corvin_compute/mcp_bridge.py`` but never imported by
the MCP server — dead code from the chat surface's perspective. This test
drives a real in-process ``MCPServer`` end to end (tools/list + tools/call)
with the compute worker socket mocked, so it exercises the exact code paths
a live chat turn would hit rather than re-testing mcp_bridge.py in isolation.
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

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "operator" / "forge"))
sys.path.insert(0, str(REPO / "core" / "compute"))

from forge import mcp_server as srv  # noqa: E402


def _server(stdout: io.StringIO) -> srv.MCPServer:
    tmp = tempfile.mkdtemp(prefix="corvin-m2-compute-")
    return srv.MCPServer(Path(tmp), stdin=io.StringIO(), stdout=stdout, stderr=io.StringIO())


def _last_result(stdout: io.StringIO) -> dict:
    lines = [ln for ln in stdout.getvalue().splitlines() if ln.strip()]
    return json.loads(lines[-1])


@unittest.skipIf(srv._COMPUTE_ENGINE_TOOL_DEFS is None, "compute plugin not importable in this env")
class TestComputeEngineToolsWired(unittest.TestCase):
    def test_tool_defs_imported_and_named(self):
        self.assertIn("compute_submit", srv._COMPUTE_ENGINE_TOOLS_NAMES)
        self.assertIn("compute_gate", srv._COMPUTE_ENGINE_TOOLS_NAMES)
        names = {d["name"] for d in srv._COMPUTE_ENGINE_TOOL_DEFS}
        self.assertEqual(names, {"compute_submit", "compute_gate"})

    def test_tools_list_includes_engine_tools_when_worker_up(self):
        stdout = io.StringIO()
        server = _server(stdout)
        with mock.patch.object(srv, "_compute_worker_reachable", return_value=True):
            server._dispatch({
                "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {},
            })
        result = _last_result(stdout)
        tool_names = {t["name"] for t in result["result"]["tools"]}
        self.assertIn("compute_submit", tool_names)
        self.assertIn("compute_gate", tool_names)

    def test_tools_list_omits_engine_tools_when_worker_down(self):
        stdout = io.StringIO()
        server = _server(stdout)
        with mock.patch.object(srv, "_compute_worker_reachable", return_value=False):
            server._dispatch({
                "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {},
            })
        result = _last_result(stdout)
        tool_names = {t["name"] for t in result["result"]["tools"]}
        self.assertNotIn("compute_submit", tool_names)
        self.assertNotIn("compute_gate", tool_names)

    def test_compute_submit_dispatches_to_submit_engine_run(self):
        stdout = io.StringIO()
        server = _server(stdout)
        fake_client = mock.Mock()
        fake_client.submit_engine_run.return_value = {"compute_handle": "pipeline_abc123"}
        with mock.patch.object(srv, "_check_compute_access", None), \
             mock.patch.object(srv, "_request_server_compute_permit", return_value="granted"), \
             mock.patch.object(srv, "_compute_socket_path_for", return_value=Path("/tmp/fake.sock")), \
             mock.patch.object(Path, "exists", return_value=True), \
             mock.patch.object(srv, "_ComputeWorkerClient", return_value=fake_client):
            server._call_compute_tool(1, "compute_submit", {
                "engine": "pipeline",
                "budget": {"max_iterations": 10},
                "extra": {"stages": [{"tool": "x"}]},
            })
        fake_client.submit_engine_run.assert_called_once_with(
            engine="pipeline",
            budget={"max_iterations": 10},
            extra={"stages": [{"tool": "x"}]},
            tenant_id=None,
        )
        result = _last_result(stdout)
        payload = json.loads(result["result"]["content"][0]["text"])
        self.assertEqual(payload["compute_handle"], "pipeline_abc123")

    def test_compute_gate_unpacks_nested_action_to_flat_params(self):
        """COMPUTE_GATE_SCHEMA nests {action: {action_type, payload}} but
        WorkerClient.gate_action() takes flat (compute_handle, action_type,
        payload=...) params — this is the exact shape mismatch a naive
        **args passthrough would have gotten wrong."""
        stdout = io.StringIO()
        server = _server(stdout)
        fake_client = mock.Mock()
        fake_client.gate_action.return_value = {"status": "resumed"}
        with mock.patch.object(srv, "_check_compute_access", None), \
             mock.patch.object(srv, "_compute_socket_path_for", return_value=Path("/tmp/fake.sock")), \
             mock.patch.object(Path, "exists", return_value=True), \
             mock.patch.object(srv, "_ComputeWorkerClient", return_value=fake_client):
            server._call_compute_tool(1, "compute_gate", {
                "compute_handle": "pipeline_abc123",
                "action": {"action_type": "resume", "payload": {"note": "ok"}},
            })
        fake_client.gate_action.assert_called_once_with(
            "pipeline_abc123", "resume", payload={"note": "ok"},
        )
        result = _last_result(stdout)
        payload = json.loads(result["result"]["content"][0]["text"])
        self.assertEqual(payload["status"], "resumed")

    def test_compute_submit_shares_license_gate_with_compute_run(self):
        """compute_submit spends compute quota exactly like compute_run —
        a denied license must block it the same way."""
        stdout = io.StringIO()
        server = _server(stdout)
        denied = mock.Mock(allowed=False, reason="trial expired", mode="denied")
        denied.as_audit_dict.return_value = {"allowed": False}
        with mock.patch.object(srv, "_check_compute_access", return_value=denied):
            server._call_compute_tool(1, "compute_submit", {
                "engine": "pipeline", "budget": {}, "extra": {},
            })
        result = _last_result(stdout)
        self.assertTrue(result["result"]["isError"])
        payload = json.loads(result["result"]["content"][0]["text"])
        self.assertEqual(payload["error"], "ComputeLicenseRequired")

    def test_compute_gate_not_license_gated(self):
        """compute_gate acts on an already-submitted run — same posture as
        compute_status/compute_abort, not re-gated by license."""
        stdout = io.StringIO()
        server = _server(stdout)
        fake_client = mock.Mock()
        fake_client.gate_action.return_value = {"status": "aborted"}
        access_check = mock.Mock(side_effect=AssertionError("compute_gate must not call the license gate"))
        with mock.patch.object(srv, "_check_compute_access", access_check), \
             mock.patch.object(srv, "_compute_socket_path_for", return_value=Path("/tmp/fake.sock")), \
             mock.patch.object(Path, "exists", return_value=True), \
             mock.patch.object(srv, "_ComputeWorkerClient", return_value=fake_client):
            server._call_compute_tool(1, "compute_gate", {
                "compute_handle": "pipeline_abc123",
                "action": {"action_type": "abort"},
            })
        result = _last_result(stdout)
        payload = json.loads(result["result"]["content"][0]["text"])
        self.assertEqual(payload["status"], "aborted")

    def test_compute_submit_trial_mode_reads_strategy_from_extra_for_flat_engine(self):
        """Verification finding: enforce_trial_strategy() reads a top-level
        args["strategy"] — correct for compute_run, but compute_submit's
        engine="flat" carries the same field nested under extra.strategy.
        Without extracting it first, a trial user submitting a bayesian
        sweep via compute_submit would silently get the wrong (more
        generous) TRIAL_ITERATION_CAP instead of the tighter
        TRIAL_BAYESIAN_CAP, since args.get("strategy") always defaults to
        "grid" for compute_submit's own shape."""
        from corvin_compute.license_gate import ComputeAccessResult  # type: ignore
        stdout = io.StringIO()
        server = _server(stdout)
        fake_client = mock.Mock()
        fake_client.submit_engine_run.return_value = {"compute_handle": "flat_trial_123"}
        trial_access = ComputeAccessResult(
            allowed=True, mode="trial", tier="free", reason=None,
            fabric_allowed=False, trial_iterations_remaining=500,
            trial_strategies_allowed=frozenset({"grid", "random", "bayesian"}),
        )
        trial_home = tempfile.mkdtemp(prefix="corvin-m2-trial-")
        # A real file (not a global Path.exists patch — that would also make
        # the trial-state loader's own path.exists() check lie and crash on
        # a subsequent path.stat()) so the compute-socket existence check
        # passes naturally.
        fake_sock = Path(trial_home) / "fake.sock"
        fake_sock.touch()
        with mock.patch.dict(os.environ, {"CORVIN_HOME": trial_home}), \
             mock.patch.object(srv, "_check_compute_access", return_value=trial_access), \
             mock.patch.object(srv, "_request_server_compute_permit", return_value="granted"), \
             mock.patch.object(srv, "_compute_socket_path_for", return_value=fake_sock), \
             mock.patch.object(srv, "_ComputeWorkerClient", return_value=fake_client):
            server._call_compute_tool(1, "compute_submit", {
                "engine": "flat",
                "budget": {"max_iterations": 10_000},
                "extra": {"strategy": "bayesian", "tool_name": "x"},
            })
        result = _last_result(stdout)
        self.assertFalse(result["result"].get("isError"), result)
        submitted_budget = fake_client.submit_engine_run.call_args.kwargs["budget"]
        # TRIAL_BAYESIAN_CAP (50), NOT TRIAL_ITERATION_CAP (500) — proves the
        # strategy was correctly read from extra.strategy, not defaulted.
        self.assertEqual(submitted_budget["max_iterations"], 50, submitted_budget)


if __name__ == "__main__":
    unittest.main()
