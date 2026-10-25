"""E2E: ``corvin-a2a diagnose`` against a real peer, through the real CLI process (PLAN-0965 F-12).

Real HTTP receiver (a2a_http_server, real HMAC pairing) in this process; the CLI runs as a SUBPROCESS exactly as an
operator runs it, with the member tier pinned by the test-install pin (``tests/e2e/fresh_system/a2a_member_pin``).
Covers: a reachable peer, a peer that is down (closed error text + a hint, exit code 1), a task-status question,
and the delegation ledger rows toward the peer.
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path

_here = Path(__file__).resolve().parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

import a2a_http_server  # noqa: E402

REPO = _here.parents[2]
CLI = REPO / "corvin_operator" / "voice" / "scripts" / "corvin_a2a.py"
PIN = REPO / "tests" / "e2e" / "fresh_system" / "a2a_member_pin"

_mock_se = mock.MagicMock()
_mock_se.write_event = mock.MagicMock(return_value={"hash": "abc"})


def setUpModule() -> None:
    import a2a_test_tier

    a2a_test_tier.pin_member_tier()


def _write_json(path: Path, cfg: dict) -> None:
    path.write_text(json.dumps(cfg))
    path.chmod(0o600)


class _Engine:
    name = "noop"
    capabilities: dict = {}

    def spawn(self, prompt, **_k):
        return iter(())

    def cancel(self) -> None:
        pass


class TestDiagnoseCli(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.origins, self.endpoints = self.tmp / "origins", self.tmp / "endpoints"
        self.origins.mkdir()
        self.endpoints.mkdir()
        self.k_hmac, self.k_recv = secrets.token_hex(32), secrets.token_hex(32)
        _write_json(self.origins / "req.json", {
            "origin_id": "req", "hmac_key": self.k_hmac, "recv_key": self.k_recv, "enabled": True,
            "max_ttl_s": 300, "allowed_personas": ["compute-csv"], "spawn_worker": True})
        self.server = a2a_http_server.build_server(
            host="127.0.0.1", port=0, origins_dir=self.origins, engine_factory=lambda: _Engine(),
            instance_id="iid-diag-receiver", forge_se=_mock_se)
        a2a_http_server.serve_in_thread(self.server)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        h, p = self.server.server_address[:2]
        self.url = f"http://{h}:{p}/v1/a2a/receive"

    def _endpoint(self, url: str, name: str = "peer") -> None:
        _write_json(self.endpoints / f"{name}.json", {
            "endpoint_id": name, "label": "Diag Peer", "url": url, "hmac_key": self.k_hmac, "recv_key": self.k_recv,
            "instance_id": "iid-diag-receiver", "enabled": True, "default_ttl_s": 60, "our_origin_id": "req"})

    def _cli(self, *args: str) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env.update({"REMOTE_ENDPOINTS_DIR": str(self.endpoints), "CORVIN_A2A_ATTESTATION_DISABLED": "1",
                    "PYTHONPATH": os.pathsep.join(filter(None, [str(PIN), env.get("PYTHONPATH", "")]))})
        return subprocess.run([sys.executable, str(CLI), "diagnose", *args], capture_output=True, text=True,
                              timeout=120, env=env)

    def test_reachable_peer_is_reported_with_a_verdict_and_the_blind_spot(self):
        self._endpoint(self.url)
        out = self._cli("peer", "--json")
        self.assertEqual(out.returncode, 0, msg=out.stdout + out.stderr)
        rep = json.loads(out.stdout)
        self.assertTrue(rep["ping"]["reachable"])
        self.assertEqual(rep["endpoint_id"], "peer")
        self.assertTrue(rep["instance_pinned"])
        self.assertIn("reachable", rep["verdict"])
        self.assertIn("Not available by design", rep["blind_spot"])
        # keys never leave the endpoint file
        self.assertNotIn(self.k_hmac, out.stdout)
        self.assertNotIn(self.k_recv, out.stdout)

    def test_peer_that_is_down_gives_a_closed_text_a_hint_and_exit_1(self):
        self._endpoint("http://127.0.0.1:1/v1/a2a/receive")  # nothing listens on port 1
        out = self._cli("Diag Peer")  # by label, human output
        self.assertEqual(out.returncode, 1, msg=out.stdout + out.stderr)
        self.assertIn("NOT reachable", out.stdout)
        self.assertIn("verdict", out.stdout)
        self.assertIn("Not available by design", out.stdout)

    def test_task_status_of_an_unknown_task_is_asked_and_answered(self):
        self._endpoint(self.url)
        out = self._cli("peer", "--task-id", "task_unknown_0001", "--json")
        self.assertEqual(out.returncode, 0, msg=out.stdout + out.stderr)
        task = json.loads(out.stdout)["task"]
        self.assertEqual(task["task_id"], "task_unknown_0001")
        self.assertEqual(task["stage"], "unknown")
        self.assertIn("supported", task)

    def test_unknown_peer_name_is_an_error_not_a_crash(self):
        out = self._cli("no-such-peer")
        self.assertEqual(out.returncode, 2, msg=out.stdout + out.stderr)
        self.assertIn("error", out.stderr)


if __name__ == "__main__":
    unittest.main()
