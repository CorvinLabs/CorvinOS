"""ADR-0190 M3 — datasource_connect: General Availability MCP tool for DSI v1.

Before this milestone, registering a typed database/warehouse connection
(DataSourceRegistry.register()) was reachable from the console REST route
(free-tier gated to local_file) and from the Fabric MCP tools (Enterprise-
only, routed through the compute worker) — but there was no chat path for
a paid-but-non-Enterprise tenant to register e.g. a Postgres connection.

This test drives a real in-process MCPServer end to end (tools/list +
tools/call) against a REAL (non-mocked) DataSourceRegistry writing to a
temp CORVIN_HOME, so it exercises the exact license-gate + register() +
audit-write code path a live chat turn would hit.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
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


class TestLicenseGateRealImport(unittest.TestCase):
    """Verification finding: an earlier version of the license-gate import
    block in mcp_server.py imported license.validator BEFORE operator/ was
    on sys.path, so the import always failed and every datasource_connect
    call silently fell through to the hardcoded local_file-only fallback
    regardless of the tenant's real license tier. This spawns a REAL
    subprocess with the EXACT PYTHONPATH resolver.py's
    _inject_forge_capability sets (operator/forge only) — an in-process
    import here would be misleadingly green, since pytest's own sys.path
    already carries operator/ transitively from unrelated test setup."""

    def test_license_validator_resolves_for_real_with_resolver_pythonpath(self):
        script = (
            "from forge import mcp_server as srv\n"
            "import license.validator as lv\n"
            "assert srv._lic_get_limit is lv.get_limit, "
            "(srv._lic_get_limit, 'fell through to a fallback, not the real gate')\n"
            "print('OK')\n"
        )
        env = dict(os.environ)
        env["PYTHONPATH"] = str(REPO / "operator" / "forge")
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, cwd=str(REPO), env=env, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("OK", result.stdout)


def _last_result(stdout: io.StringIO) -> dict:
    lines = [ln for ln in stdout.getvalue().splitlines() if ln.strip()]
    return json.loads(lines[-1])


@unittest.skipIf(srv._DataSourceRegistry is None, "compute plugin not importable in this env")
class TestDatasourceConnectWired(unittest.TestCase):
    def setUp(self):
        self.home_tmp = tempfile.TemporaryDirectory(prefix="corvin-m3-ds-")
        self.home = Path(self.home_tmp.name)
        (self.home / "tenants" / "_default").mkdir(parents=True)
        self.env_patch = mock.patch.dict(os.environ, {"CORVIN_HOME": str(self.home)})
        self.env_patch.start()
        self.workspace = tempfile.mkdtemp(prefix="corvin-m3-ds-ws-")

    def tearDown(self):
        self.env_patch.stop()
        self.home_tmp.cleanup()

    def _server(self, stdout: io.StringIO) -> srv.MCPServer:
        return srv.MCPServer(Path(self.workspace), stdin=io.StringIO(), stdout=stdout, stderr=io.StringIO())

    def _local_manifest(self, name: str, path: Path) -> dict:
        return {
            "dsi_version": "1",
            "name": name,
            "adapter": "local_file",
            "config": {"path": str(path)},
            "data_classification": "PUBLIC",
        }

    def test_tools_list_includes_datasource_connect_unconditionally(self):
        """Unlike compute_submit/gate, this needs no worker socket — advertised
        whenever the compute plugin (which ships DataSourceRegistry) imports."""
        stdout = io.StringIO()
        server = self._server(stdout)
        with mock.patch.object(srv, "_compute_worker_reachable", return_value=False):
            server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
        tool_names = {t["name"] for t in _last_result(stdout)["result"]["tools"]}
        self.assertIn("datasource_connect", tool_names)
        self.assertNotIn("compute_submit", tool_names)  # worker-gated tool correctly absent

    def test_free_tier_allows_local_file(self):
        stdout = io.StringIO()
        server = self._server(stdout)
        csv_path = self.home / "sample.csv"
        csv_path.write_text("a,b\n1,2\n", encoding="utf-8")
        with mock.patch.object(srv, "_lic_get_limit", {"datasource_adapters_allowed": ["local_file"]}.get):
            server._call_datasource_connect(1, {
                "manifest": self._local_manifest("m3_local_test", csv_path),
            })
        result = _last_result(stdout)
        payload = json.loads(result["result"]["content"][0]["text"])
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["adapter"], "local_file")
        # Real file actually written under the temp CORVIN_HOME.
        written = self.home / "tenants" / "_default" / "datasource_connections" / "m3_local_test.json"
        self.assertTrue(written.exists())
        on_disk = json.loads(written.read_text())
        self.assertEqual(on_disk["adapter"], "local_file")

    def test_free_tier_denies_postgresql_same_as_console_route(self):
        """Exact same feature key + allowlist decision as
        core/console/corvin_console/routes/data_sources.py's gate."""
        stdout = io.StringIO()
        server = self._server(stdout)
        with mock.patch.object(srv, "_lic_get_limit", {"datasource_adapters_allowed": ["local_file"]}.get):
            server._call_datasource_connect(1, {
                "manifest": {
                    "dsi_version": "1",
                    "name": "m3_pg_test",
                    "adapter": "postgresql",
                    "config": {"host": "db.example.com"},
                    "data_classification": "INTERNAL",
                },
            })
        result = _last_result(stdout)
        self.assertTrue(result["result"]["isError"])
        payload = json.loads(result["result"]["content"][0]["text"])
        self.assertEqual(payload["error"], "license_limit")
        self.assertEqual(payload["feature"], "datasource_adapters_allowed")
        # Nothing written to disk on a denied registration.
        written = self.home / "tenants" / "_default" / "datasource_connections" / "m3_pg_test.json"
        self.assertFalse(written.exists())

    def test_member_tier_unlimited_allows_postgresql_manifest_to_reach_register(self):
        """allowed_adapters=None (unlimited) must let a non-local_file adapter
        past the gate — proves the None-means-unlimited semantics match the
        console route exactly. (Registration itself will still fail past the
        gate for postgresql without live connectivity/adapter deps in this
        sandboxed test — we only assert the license gate was NOT the blocker.)"""
        stdout = io.StringIO()
        server = self._server(stdout)
        with mock.patch.object(srv, "_lic_get_limit", {"datasource_adapters_allowed": None}.get):
            server._call_datasource_connect(1, {
                "manifest": {
                    "dsi_version": "1",
                    "name": "m3_pg_unlimited",
                    "adapter": "postgresql",
                    "config": {"host": "db.example.com"},
                    "data_classification": "INTERNAL",
                },
            })
        result = _last_result(stdout)
        payload = json.loads(result["result"]["content"][0]["text"])
        # Whatever happens next (adapter validation etc.), it must NOT be the
        # license_limit error — the gate correctly let it through.
        self.assertNotEqual(payload.get("error"), "license_limit")

    def test_compute_plugin_absent_returns_typed_error_not_crash(self):
        stdout = io.StringIO()
        server = self._server(stdout)
        with mock.patch.object(srv, "_DataSourceRegistry", None):
            server._call_datasource_connect(1, {"manifest": {}})
        result = _last_result(stdout)
        self.assertIn("error", result)
        self.assertEqual(result["error"]["code"], srv.METHOD_NOT_FOUND)


if __name__ == "__main__":
    unittest.main()
