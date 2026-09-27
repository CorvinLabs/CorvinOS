"""The forge MCP server's security events land in THE tenant audit chain.

Regression for 2026-09-27: ``MCPServer._log_security_event`` wrote to
``registry.root / "audit.jsonl"`` — the WORKSPACE root. A server started
without ``FORGE_ROOT`` from inside a git checkout resolves that root to
``<checkout>/.corvin/forge`` (project scope) whatever ``CORVIN_HOME`` says, so
a sandboxed test run (``core/delegate/tests/test_live_e2e.py``) appended
records to the live checkout's ``.corvin/forge/audit.jsonl``. CLAUDE.md: one
chain per tenant, resolved by ``tenant_audit_chain()``.

Runs under pytest and as a plain script (run-all-tests.sh "forge plugin").
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

_FORGE_PLUGIN = Path(__file__).resolve().parents[1]
if str(_FORGE_PLUGIN) not in sys.path:
    sys.path.insert(0, str(_FORGE_PLUGIN))
# With `<repo>/corvin_operator` on sys.path, an earlier `import forge` in the
# same pytest process can bind `forge` to the corvin_operator/forge/ DIRECTORY
# as a namespace package (no __file__), which then shadows the real package.
if "forge" in sys.modules and getattr(sys.modules["forge"], "__file__", None) is None:
    for _m in [m for m in sys.modules if m == "forge" or m.startswith("forge.")]:
        del sys.modules[_m]

from forge import mcp_server  # noqa: E402

_KEYS = ("CORVIN_HOME", "CORVIN_TENANT_ID", "FORGE_ROOT", "VOICE_AUDIT_PATH",
         "CORVIN_AUDIT_ANCHOR_KEY", "XDG_CONFIG_HOME")


def _records(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]


class _Sandbox(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory(prefix="forge-mcp-chain-")
        self.tmp = Path(self._td.name)
        self._saved = {k: os.environ.get(k) for k in _KEYS}
        for k in ("FORGE_ROOT", "VOICE_AUDIT_PATH", "CORVIN_TENANT_ID"):
            os.environ.pop(k, None)
        self.home = self.tmp / "home"
        self.home.mkdir()
        os.environ["CORVIN_HOME"] = str(self.home)
        os.environ["XDG_CONFIG_HOME"] = str(self.tmp / "xdg")
        os.environ["CORVIN_AUDIT_ANCHOR_KEY"] = str(self.tmp / "anchor.key")
        # A workspace OUTSIDE the home — the shape of <checkout>/.corvin/forge.
        self.ws = self.tmp / "checkout" / ".corvin" / "forge"
        self.ws.mkdir(parents=True)

    def tearDown(self) -> None:
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._td.cleanup()

    def chain(self, tenant: str = "_default") -> Path:
        return self.home / "tenants" / tenant / "global" / "forge" / "audit.jsonl"


class TestServerAuditChainResolution(_Sandbox):
    def test_default_is_the_tenant_chain_not_the_workspace(self):
        self.assertEqual(mcp_server._server_audit_chain(), self.chain())

    def test_tenant_comes_from_corvin_tenant_id(self):
        os.environ["CORVIN_TENANT_ID"] = "acme"
        self.assertEqual(mcp_server._server_audit_chain(), self.chain("acme"))

    def test_redirects_match_the_bridge_audit_writer(self):
        os.environ["FORGE_ROOT"] = str(self.tmp / "fr")
        self.assertEqual(mcp_server._server_audit_chain(),
                         self.tmp / "fr" / "audit.jsonl")
        os.environ["VOICE_AUDIT_PATH"] = str(self.tmp / "v.jsonl")
        self.assertEqual(mcp_server._server_audit_chain(), self.tmp / "v.jsonl")

    def test_log_security_event_writes_the_tenant_chain(self):
        srv = mcp_server.MCPServer(self.ws)
        srv._log_security_event("policy.reload_failed",
                                details={"error": "x", "kept_old_policy": True})
        types = [r["event_type"] for r in _records(self.chain())]
        self.assertIn("policy.reload_failed", types)
        self.assertFalse((self.ws / "audit.jsonl").exists())

    def test_unchained_workspace_policy_does_not_break_event_logging(self):
        """``audit.hash_chain: false`` in policy.json used to reach
        ``write_event(hash_chain=False)``, which raises ValueError for every
        event but the gap marker — out of every security-event call site."""
        (self.ws / "policy.json").write_text(json.dumps({"audit": {"hash_chain": False}}))
        srv = mcp_server.MCPServer(self.ws)
        srv._log_security_event("policy.reload_failed",
                                details={"error": "x", "kept_old_policy": True})
        recs = _records(self.chain())
        self.assertTrue(recs and recs[-1].get("hash"), recs)


class TestServerAuditChainOverTheWire(_Sandbox):
    """E2E through the real stdio transport: ``forge.py --root <ws> mcp``."""

    def test_spawned_server_event_lands_in_the_tenant_chain(self):
        env = os.environ.copy()
        env.pop("FORGE_ROOT", None)
        env.pop("VOICE_AUDIT_PATH", None)
        proc = subprocess.Popen(
            [sys.executable, str(_FORGE_PLUGIN / "forge.py"), "--root", str(self.ws),
             "mcp", "--permission-mode", "yes"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, cwd=str(self.tmp), env=env,
        )
        try:
            def rpc(i: int, method: str, params: dict) -> dict:
                proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": i,
                                             "method": method, "params": params}) + "\n")
                proc.stdin.flush()
                while True:
                    line = proc.stdout.readline()
                    self.assertTrue(line, "server closed stdout")
                    msg = json.loads(line)
                    if msg.get("id") == i:
                        return msg

            rpc(1, "initialize", {})
            # A malformed policy edit → next call logs policy.reload_failed.
            pol = self.ws / "policy.json"
            pol.write_text("{ broken")
            future = time.time() + 2
            os.utime(pol, (future, future))
            rpc(2, "tools/call", {"name": "no.such.tool", "arguments": {}})
        finally:
            proc.stdin.close()
            proc.wait(timeout=30)
            proc.stdout.close()
            proc.stderr.close()
        types = [r["event_type"] for r in _records(self.chain())]
        self.assertIn("policy.reload_failed", types)
        self.assertFalse((self.ws / "audit.jsonl").exists(),
                         "security event written beside the workspace")


if __name__ == "__main__":
    unittest.main(verbosity=2)
