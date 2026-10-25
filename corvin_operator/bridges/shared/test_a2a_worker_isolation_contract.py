"""The isolation contract of the inbound A2A worker, pinned for the engine that is shareable today (claude_code).

PLAN-0965 F-06 / ADR-2291 (draft): an engine may be shared with a peer only if it keeps these properties, and the
properties must be tests that fail, not sentences in a docstring. Until now NO test referenced ``--strict-mcp-config``,
so the "a remote worker has zero MCP servers" guarantee (what stops a prompt from another organisation reaching a tool)
was unpinned: a refactor could drop the flag and everything stayed green.

Goes through the real HTTP receiver and the real sender. The engine is a recording stand-in named ``claude_code`` so the
claude-specific posture applies; the test reads exactly what the receiver handed to ``engine.spawn``.

Contract asserted (claude_code):
  (a) zero MCP servers: ``--strict-mcp-config`` is passed and no ``--mcp-config`` is;
  (b) secrets cleared: the environment override blanks the credentials a worker must never inherit;
  (c) tool grant: an origin with no ``allow_*`` gets the dangerous tools denied; a grant lifts exactly its own tool;
  (d) workspace: the worker runs in a private scratch directory, not in the repo or the home directory.
"""
from __future__ import annotations

import json
import os
import secrets
import sys
import tempfile
import unittest
import unittest.mock as mock
from dataclasses import dataclass
from pathlib import Path

_here = Path(__file__).resolve().parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

import a2a_http_server  # noqa: E402
import remote_trigger_sender as rts  # noqa: E402
import spawn_gates  # noqa: E402

_SAVED_LICENSE_MODULES: dict[str, object | None] = {}
_L44_PATCH = mock.patch.object(spawn_gates, "check_l44", lambda *a, **kw: None)
_mock_se = mock.MagicMock()
_mock_se.write_event = mock.MagicMock(return_value={"hash": "abc"})


def setUpModule() -> None:
    import a2a_test_tier

    a2a_test_tier.pin_member_tier()
    for name in ("license.compute_quota", "license.limits"):
        _SAVED_LICENSE_MODULES[name] = sys.modules.get(name)
        sys.modules[name] = None  # type: ignore[assignment]
    _L44_PATCH.start()


def tearDownModule() -> None:
    _L44_PATCH.stop()
    for name, mod in _SAVED_LICENSE_MODULES.items():
        if mod is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = mod


@dataclass
class _Ev:
    type: str
    text: str | None = None
    usage: dict | None = None
    error: str | None = None


class _RecordingClaude:
    """Named like the shareable engine; records what the receiver passed to spawn()."""

    name = "claude_code"
    capabilities: dict = {}
    seen: list = []

    def spawn(self, prompt, **kwargs):
        wd = kwargs.get("working_dir")
        snap = dict(kwargs)
        snap["_workdir_exists"] = bool(wd) and Path(wd).is_dir()
        snap["_workdir_mode"] = (Path(wd).stat().st_mode & 0o777) if wd and Path(wd).exists() else None
        type(self).seen.append(snap)
        yield _Ev(type="text_delta", text="{}")
        yield _Ev(type="turn_completed", text="{}")

    def cancel(self) -> None:
        pass


def _write(path: Path, cfg: dict) -> None:
    path.write_text(json.dumps(cfg))
    path.chmod(0o600)


class TestInboundWorkerIsolationContract(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["CORVIN_A2A_ATTESTATION_DISABLED"] = "1"
        self.addCleanup(lambda: os.environ.pop("CORVIN_A2A_ATTESTATION_DISABLED", None))
        _RecordingClaude.seen = []
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def _run(self, **grants) -> dict:
        origins, endpoints = self.tmp / "o", self.tmp / "e"
        origins.mkdir(exist_ok=True)
        endpoints.mkdir(exist_ok=True)
        k_hmac, k_recv = secrets.token_hex(32), secrets.token_hex(32)
        _write(origins / "req.json", {"origin_id": "req", "hmac_key": k_hmac, "recv_key": k_recv, "enabled": True,
                                      "max_ttl_s": 300, "allowed_personas": [], "spawn_worker": True, **grants})
        server = a2a_http_server.build_server(host="127.0.0.1", port=0, origins_dir=origins,
                                              engine_factory=lambda: _RecordingClaude(),
                                              instance_id="iid-contract-receiver", forge_se=_mock_se)
        a2a_http_server.serve_in_thread(server)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        h, p = server.server_address[:2]
        _write(endpoints / "peer.json", {"endpoint_id": "peer", "url": f"http://{h}:{p}/v1/a2a/receive", "hmac_key": k_hmac,
                                         "recv_key": k_recv, "instance_id": "iid-contract-receiver", "enabled": True,
                                         "default_ttl_s": 60, "our_origin_id": "req"})
        sender = rts.RemoteTriggerSender(endpoints_dir=endpoints, instance_id="iid-contract-sender", forge_se=_mock_se)
        res = sender.send("peer", instruction="do the task", result_schema={"properties": {"output": {"type": "string"}}},
                          ttl_s=30, timeout_s=40)
        self.assertTrue(res.ok or res.status in ("ok", "filtered"), msg=f"{res.status} {res.error_category}")
        self.assertEqual(len(_RecordingClaude.seen), 1, "the worker engine must have been spawned exactly once")
        return _RecordingClaude.seen[0]

    def test_a_zero_mcp_servers(self):
        kw = self._run()
        extra = list(kw.get("extra_args") or [])
        self.assertIn("--strict-mcp-config", extra, "a remote worker must load NO MCP server")
        self.assertNotIn("--mcp-config", extra, "nothing may add an MCP config to a remote worker")

    def test_b_credentials_are_cleared_from_the_environment(self):
        env = self._run().get("env") or {}
        for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "OPENAI_API_KEY",
                     "CORVIN_LICENSE_KEY", "FORGE_ROOT"):
            self.assertIn(name, env, f"{name} must be blanked, not inherited")
            self.assertEqual(env[name], "", f"{name} must be blanked, not inherited")

    def test_c_tool_grant_denies_by_default_and_a_grant_lifts_exactly_its_own_tool(self):
        denied = set(self._run().get("disallowed_tools") or [])
        for tool in ("Bash", "WebFetch", "WebSearch", "Read", "Write", "Edit"):
            self.assertIn(tool, denied, f"{tool} must be denied for an origin without a grant")
        _RecordingClaude.seen = []
        granted = set(self._run(allow_read_files=True).get("disallowed_tools") or [])
        self.assertNotIn("Read", granted, "allow_read_files lifts Read")
        for tool in ("Bash", "WebFetch", "WebSearch", "Write"):
            self.assertIn(tool, granted, f"{tool} must stay denied when only reading was granted")

    def test_d_the_worker_runs_in_a_private_scratch_directory(self):
        kw = self._run()
        wd = Path(str(kw.get("working_dir")))
        self.assertTrue(kw["_workdir_exists"])
        self.assertEqual(kw["_workdir_mode"], 0o700, "scratch workspace must be private")
        repo = Path(__file__).resolve().parents[3]
        self.assertTrue(wd.name.startswith("a2a-worker-"), f"unexpected workspace name: {wd.name}")
        self.assertNotEqual(wd.resolve(), Path.home().resolve())
        self.assertNotIn(repo.resolve(), wd.resolve().parents, "the worker must not run inside the repository")
        self.assertFalse(wd.exists(), "the scratch directory must be gone after the task")


if __name__ == "__main__":
    unittest.main()
