"""REVIEW-0068 #5 and #9 - the MCP servers the acs feature writes, started for real.

* #9: a project created over HTTP from the REAL catalogue with ``acs`` on writes ``.corvin/local/mcp.json``; every server
  in it is started with its own command, args and env, answers ``initialize`` and lists its tools over JSON-RPC (stdio).
* #5: the server paths come from where the operator code really is: the checkout, or in a wheel install the vendored
  operator root (``corvin_core/_vendor``); the doctor's ``mcp_servers`` check proves that every command, script and
  ``-m`` module of the file exists - a broken path is named, not hidden behind a green byte compare.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "core" / "console" / "tests"))
from _projects_fixtures import sandbox_home_env  # noqa: E402
import test_project_kb_projector_e2e as fx  # noqa: E402
from test_admin_route import _sandbox  # noqa: E402

_MARKET = fx._projects_root() / "Corvin-Marketplace" / "plugins" / "buildin" / "knowledge_management"
_SRC = _MARKET / "project_templates" / "src"
_KB_INIT = _MARKET / "corvin_knowledge" / "src" / "kb_init.py"
_URL = "/v1/console/projects"
_ENABLED = {"corvin_knowledge": "enabled", "project_templates": "enabled", "video_producer": "enabled"}


def _rpc(server: dict, cwd: Path) -> tuple[int, list[dict], str]:
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "e2e", "version": "0"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]
    env = {**os.environ, **server.get("env", {})}
    p = subprocess.run([server["command"], *server["args"]], input="".join(json.dumps(m) + "\n" for m in msgs),
                       capture_output=True, text=True, env=env, timeout=120, cwd=str(cwd))
    out = []
    for line in p.stdout.splitlines():
        if line.startswith("{"):
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return p.returncode, out, p.stderr[-600:]


@unittest.skipUnless((fx._KB_SRC / "kb.py").is_file() and _SRC.is_dir() and "--no-engine" in _KB_INIT.read_text(),
                     "needs Corvin-Knowledge and the Corvin-Marketplace plugins")
class AcsMcpServersE2E(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._home_env = sandbox_home_env(self.tmp)
        self._home_env.__enter__()
        self.bound = self.tmp / "bound"
        fx._kb_init(self.bound, engine=True)
        fx._seed(self.bound, "bound")
        self.saved = os.environ.get("CORVIN_KB_REPO")
        os.environ["CORVIN_KB_REPO"] = str(self.bound)

    def tearDown(self):
        self._home_env.__exit__(None, None, None)
        os.environ.pop("CORVIN_KB_REPO", None) if self.saved is None else os.environ.__setitem__("CORVIN_KB_REPO", self.saved)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _ctx(self):
        import contextlib

        @contextlib.contextmanager
        def cm():
            with _sandbox(self.tmp / "s") as (client, csrf, home, _):
                from core.projects import registry
                from corvin_console import projects_service as svc
                root = self.tmp / "projects"
                root.mkdir(exist_ok=True)
                registry.set_projects_root("_default", str(root))
                with mock.patch.object(svc, "template_source", lambda t: _SRC), \
                        mock.patch.object(svc, "plugin_state", lambda t, p: _ENABLED.get(p, "absent")), \
                        mock.patch.object(svc, "kb_init_script", lambda: _KB_INIT), \
                        mock.patch.object(svc, "compute_worker_reachable", lambda t: True):
                    yield client, {"X-CSRF-Token": csrf}, svc
        return cm()

    def _acs_project(self, client, hdr) -> Path:
        p = client.post(f"{_URL}/plan", json={"template": "research-compute", "title": "Experiment lab"}, headers=hdr)
        self.assertEqual(p.status_code, 200, p.text)
        self.assertIn("acs", p.json()["features"])
        r = client.post(_URL, json={"plan_id": p.json()["plan_id"]}, headers=hdr)
        self.assertEqual(r.status_code, 201, r.text)
        return Path(client.get(f"{_URL}/{p.json()['project_id']}").json()["path"]), p.json()["project_id"]

    def test_9_every_generated_mcp_server_starts_and_lists_its_tools(self):
        with self._ctx() as (client, hdr, _svc):
            root, _pid = self._acs_project(client, hdr)
            servers = json.loads((root / ".corvin" / "local" / "mcp.json").read_text(encoding="utf-8"))["mcpServers"]
            self.assertEqual(sorted(servers), ["corvin_orchestration", "forge", "skill_forge"])
            for name, spec in servers.items():
                with self.subTest(server=name):
                    rc, out, err = _rpc(spec, self.tmp)
                    init = [m for m in out if m.get("id") == 1]
                    listed = [m for m in out if m.get("id") == 2]
                    self.assertTrue(init and "result" in init[0], f"{name}: no initialize answer (rc {rc}): {err}")
                    self.assertTrue(listed and "result" in listed[0], f"{name}: no tools/list answer (rc {rc}): {err}")
                    tools = [t["name"] for t in listed[0]["result"].get("tools", [])]
                    self.assertTrue(tools, f"{name} lists no tools")

    def test_5_the_doctor_checks_that_every_server_path_exists(self):
        with self._ctx() as (client, hdr, _svc):
            root, pid = self._acs_project(client, hdr)
            checks = {c["id"]: c for c in client.get(f"{_URL}/{pid}/health").json()["checks"]}
            self.assertIn("mcp_servers", checks)
            self.assertTrue(checks["mcp_servers"]["ok"], checks["mcp_servers"])
            f = root / ".corvin" / "local" / "mcp.json"
            data = json.loads(f.read_text(encoding="utf-8"))
            data["mcpServers"]["forge"]["args"][0] = str(self.tmp / "nowhere" / "forge.py")
            data["mcpServers"]["skill_forge"]["args"] = ["-m", "no_such_module_r68"]
            f.write_text(json.dumps(data), encoding="utf-8")
            checks = {c["id"]: c for c in client.get(f"{_URL}/{pid}/health").json()["checks"]}
            self.assertFalse(checks["mcp_servers"]["ok"])
            self.assertIn("forge", checks["mcp_servers"]["detail"])
            self.assertIn("skill_forge", checks["mcp_servers"]["detail"])
            self.assertNotIn(str(self.tmp), checks["mcp_servers"]["detail"], "the detail names servers, not paths")

    def test_5_in_a_wheel_layout_the_paths_point_at_the_vendored_operator_root(self):
        with self._ctx() as (_client, _hdr, svc):
            fake_site = self.tmp / "site"
            vend = fake_site / "corvin_core" / "_vendor" / "corvin_operator"
            (vend / "forge").mkdir(parents=True)
            from corvin_core import _operator_bootstrap
            with mock.patch.object(svc.kb_projection, "_REPO_ROOT", fake_site / "lib"), \
                    mock.patch.object(_operator_bootstrap, "vendor_operator_root", lambda: vend):
                ctx = svc._context("_default", "p", "P", self.tmp / "p")
            self.assertEqual(Path(ctx["paths.repo"]), vend.parent)
            ctx = svc._context("_default", "p", "P", self.tmp / "p")        # the checkout: the repository itself
            self.assertTrue((Path(ctx["paths.repo"]) / "corvin_operator" / "forge" / "forge.py").is_file())


if __name__ == "__main__":
    unittest.main()
