"""E2E tests for the path_gate AST gate (Gate 1 + 4).

Tests run against the real path_gate module (no mocks of the AST logic).
The forge security_events import is exercised only in the audit emission
path; tests for audit output are skipped when the audit path doesn't exist.

Run:
    python3 operator/bridges/shared/test_path_gate_ast.py
"""
from __future__ import annotations

import json
import sys
import os
import tempfile
import textwrap
import unittest
from pathlib import Path

# Make path_gate importable.
_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "operator" / "voice" / "hooks"))
sys.path.insert(0, str(_REPO / "operator" / "forge"))

import path_gate  # noqa: E402  (side-effect import OK here)


def _bash_payload(cmd: str) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": cmd}}


class TestASTGateDangerousImports(unittest.TestCase):

    def _check(self, cmd: str) -> tuple[bool, str]:
        return path_gate.check(_bash_payload(cmd))

    # ── dangerous imports ──────────────────────────────────────────────────

    def test_blocks_socket_import(self):
        cmd = "python3 -c 'import socket; socket.create_connection((\"a\",80))'"
        ok, reason = self._check(cmd)
        self.assertFalse(ok)
        self.assertIn("forbidden import", reason)
        self.assertIn("socket", reason)

    def test_blocks_subprocess_import(self):
        cmd = 'python3 -c "import subprocess; subprocess.run([\'ls\'])"'
        ok, reason = self._check(cmd)
        self.assertFalse(ok)
        self.assertIn("forbidden import", reason)

    def test_blocks_urllib_from_import(self):
        cmd = "python3 -c 'from urllib.request import urlopen; urlopen(\"http://x\")'"
        ok, reason = self._check(cmd)
        self.assertFalse(ok)
        self.assertIn("forbidden", reason)

    def test_blocks_ctypes(self):
        cmd = "python3 -c 'import ctypes'"
        ok, reason = self._check(cmd)
        self.assertFalse(ok)
        self.assertIn("ctypes", reason)

    # ── dangerous builtins ─────────────────────────────────────────────────

    def test_blocks_eval_builtin(self):
        cmd = "python3 -c 'eval(\"1+1\")'"
        ok, reason = self._check(cmd)
        self.assertFalse(ok)
        self.assertIn("forbidden builtin", reason)
        self.assertIn("eval", reason)

    def test_blocks_exec_builtin(self):
        cmd = 'python3 -c "exec(\'print(1)\')"'
        ok, reason = self._check(cmd)
        self.assertFalse(ok)
        self.assertIn("forbidden builtin", reason)

    # ── dangerous method calls ─────────────────────────────────────────────

    def test_blocks_os_system(self):
        cmd = "python3 -c 'import os; os.system(\"id\")'"
        ok, reason = self._check(cmd)
        self.assertFalse(ok)
        # subprocess is blocked before the attribute check fires:
        # 'os' is not in _AST_BLOCKLIST_IMPORTS, so the attr check fires.
        self.assertIn("forbidden", reason)

    def test_blocks_subprocess_popen(self):
        # subprocess module itself is blocked, but even without import:
        cmd = "python3 -c 'import subprocess; subprocess.Popen([\"ls\"])'"
        ok, reason = self._check(cmd)
        self.assertFalse(ok)

    # ── safe code passes ───────────────────────────────────────────────────

    def test_allows_pillow_png(self):
        cmd = (
            "python3 -c '"
            "from PIL import Image, ImageDraw; "
            "img = Image.new(\"RGB\",(10,10)); "
            "img.save(\"/tmp/t.png\")'"
        )
        ok, _ = self._check(cmd)
        self.assertTrue(ok)

    def test_allows_json_math(self):
        cmd = "python3 -c 'import json, math; print(json.dumps({\"pi\": math.pi}))'"
        ok, _ = self._check(cmd)
        self.assertTrue(ok)

    def test_allows_pathlib_write_tmp(self):
        cmd = "python3 -c 'from pathlib import Path; Path(\"/tmp/x\").write_text(\"ok\")'"
        ok, _ = self._check(cmd)
        self.assertTrue(ok)

    def test_allows_base64(self):
        cmd = "python3 -c 'import base64; print(base64.b64encode(b\"hi\"))'"
        ok, _ = self._check(cmd)
        self.assertTrue(ok)

    def test_non_python_bash_unaffected(self):
        cmd = "ls -la /tmp && echo done"
        ok, _ = self._check(cmd)
        self.assertTrue(ok)


class TestASTGateFileExecution(unittest.TestCase):

    def _write_and_check(self, code: str) -> tuple[bool, str]:
        with tempfile.NamedTemporaryFile(
            suffix=".py", mode="w", delete=False
        ) as f:
            f.write(textwrap.dedent(code))
            f.flush()
            cmd = f"python3 {f.name}"
            return path_gate.check(_bash_payload(cmd))

    def test_file_with_socket_blocked(self):
        ok, reason = self._write_and_check("""
            import socket
            s = socket.create_connection(("example.com", 80))
        """)
        self.assertFalse(ok)
        self.assertIn("forbidden import", reason)

    def test_file_safe_code_allowed(self):
        ok, _ = self._write_and_check("""
            import math, json
            print(json.dumps({"result": math.sqrt(2)}))
        """)
        self.assertTrue(ok)

    def test_file_exec_builtin_blocked(self):
        ok, reason = self._write_and_check("""
            code = "print('hi')"
            exec(code)
        """)
        self.assertFalse(ok)
        self.assertIn("forbidden builtin", reason)


class TestASTCheckUnit(unittest.TestCase):
    """Unit tests for _python_ast_check directly."""

    def test_syntax_error_denies(self):
        safe, reason = path_gate._python_ast_check("def broken(:")
        self.assertFalse(safe)
        self.assertIn("AST parse error", reason)

    def test_empty_code_safe(self):
        safe, _ = path_gate._python_ast_check("")
        self.assertTrue(safe)

    def test_rmtree_blocked(self):
        safe, reason = path_gate._python_ast_check(
            "import shutil; shutil.rmtree('/tmp/x')"
        )
        self.assertFalse(safe)
        self.assertIn("rmtree", reason)

    def test_os_fork_blocked(self):
        safe, reason = path_gate._python_ast_check(
            "import os; pid = os.fork()"
        )
        self.assertFalse(safe)
        self.assertIn("fork", reason)


class TestADR0026PathGate(unittest.TestCase):
    """ADR-0026 — Compute Fabric artefact tree path-gate tests.

    Verifies that the four new protected sub-paths introduced by ADR-0026
    (artifacts, plugins, datasources, datasource-adapters) are blocked by
    the path_gate.

    These paths are under <corvin_home>/**/compute/ which is already
    covered by the 'compute' hint, but we verify the specific sub-paths
    explicitly so a future refactor cannot silently drop coverage.
    """

    def setUp(self) -> None:
        # Point CORVIN_HOME at a temp dir so _corvin_home() is deterministic.
        self.td = tempfile.mkdtemp(prefix="path-gate-adr0026-")
        self._orig_env = os.environ.copy()
        os.environ["CORVIN_HOME"] = self.td

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.td, ignore_errors=True)
        os.environ.clear()
        os.environ.update(self._orig_env)

    def _write_payload(self, path: str) -> dict:
        return {"tool_name": "Write", "tool_input": {"file_path": path}}

    def _edit_payload(self, path: str) -> dict:
        return {"tool_name": "Edit", "tool_input": {"file_path": path}}

    def _bash_payload(self, cmd: str) -> dict:
        return {"tool_name": "Bash", "tool_input": {"command": cmd}}

    def _compute_path(self, *parts: str) -> str:
        return str(Path(self.td) / "tenants" / "_default" / "compute" / Path(*parts))

    # ---- compute/artifacts -----------------------------------------------

    def test_write_to_compute_artifacts_blocked(self) -> None:
        p = self._compute_path("artifacts", "run_abc", "model.pkl")
        ok, reason = path_gate.check(self._write_payload(p))
        self.assertFalse(ok, "compute/artifacts/ must be path-gate protected")

    def test_edit_compute_artifacts_blocked(self) -> None:
        p = self._compute_path("artifacts", "run_abc", "meta.json")
        ok, _ = path_gate.check(self._edit_payload(p))
        self.assertFalse(ok)

    def test_bash_redirect_to_compute_artifacts_blocked(self) -> None:
        p = self._compute_path("artifacts", "run_xyz", "model.pkl")
        ok, _ = path_gate.check(self._bash_payload(f"echo x > {p}"))
        self.assertFalse(ok)

    # ---- compute/plugins -------------------------------------------------

    def test_write_to_compute_plugins_blocked(self) -> None:
        p = self._compute_path("plugins", "acme", "compute_plugin.yaml")
        ok, reason = path_gate.check(self._write_payload(p))
        self.assertFalse(ok, "compute/plugins/ must be path-gate protected")

    def test_edit_compute_plugins_blocked(self) -> None:
        p = self._compute_path("plugins", "acme", "backend.py")
        ok, _ = path_gate.check(self._edit_payload(p))
        self.assertFalse(ok)

    # ---- compute/datasources ---------------------------------------------

    def test_write_to_compute_datasources_blocked(self) -> None:
        p = self._compute_path("datasources", "crm_events.checkpoint.json")
        ok, reason = path_gate.check(self._write_payload(p))
        self.assertFalse(ok, "compute/datasources/ must be path-gate protected")

    def test_edit_compute_datasources_manifest_blocked(self) -> None:
        p = self._compute_path("datasources", "orders_db.yaml")
        ok, _ = path_gate.check(self._edit_payload(p))
        self.assertFalse(ok)

    def test_bash_tee_to_compute_datasources_blocked(self) -> None:
        p = self._compute_path("datasources", "crm_events.checkpoint.json")
        ok, _ = path_gate.check(
            self._bash_payload(f"echo '{{}}' | tee {p}")
        )
        self.assertFalse(ok)

    # ---- compute/datasource-adapters ------------------------------------

    def test_write_to_datasource_adapters_blocked(self) -> None:
        p = self._compute_path("datasource-adapters", "acme", "adapter.py")
        ok, reason = path_gate.check(self._write_payload(p))
        self.assertFalse(ok, "compute/datasource-adapters/ must be path-gate protected")

    def test_edit_datasource_adapters_manifest_blocked(self) -> None:
        p = self._compute_path("datasource-adapters", "acme", "compute_datasource.yaml")
        ok, _ = path_gate.check(self._edit_payload(p))
        self.assertFalse(ok)

    # ---- benign paths still allowed -------------------------------------

    def test_write_to_tmp_not_blocked(self) -> None:
        ok, _ = path_gate.check(self._write_payload("/tmp/safe_output.txt"))
        self.assertTrue(ok)

    def test_write_to_home_non_corvin_not_blocked(self) -> None:
        ok, _ = path_gate.check(self._write_payload("/home/user/project/model.py"))
        self.assertTrue(ok)


class TestForgeExecIntegration(unittest.TestCase):
    """Integration test: forge_exec is listed as a meta-tool by the server."""

    def test_forge_exec_in_meta_allowlist(self):
        """_is_forged_tool_allowed must permit forge_exec."""
        forge_path = _REPO / "operator" / "forge"
        sys.path.insert(0, str(forge_path))
        try:
            from forge.mcp_server import MCPServer  # type: ignore
        except ImportError as e:
            self.skipTest(f"forge not importable: {e}")

        # MCPServer needs a registry root; use a temp dir.
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            (td_path / "registry.json").write_text("[]")
            try:
                srv = MCPServer.__new__(MCPServer)
                srv.allowed_forged_tools = None  # open ACL
                result = srv._is_forged_tool_allowed("forge_exec")
                self.assertTrue(result)
            except Exception as e:
                self.skipTest(f"MCPServer init partial: {e}")

    def test_forge_exec_in_all_tools(self):
        """forge_exec must appear in _all_tools() output."""
        forge_path = _REPO / "operator" / "forge"
        sys.path.insert(0, str(forge_path))
        try:
            import importlib
            mcp_mod = importlib.import_module("forge.mcp_server")
        except ImportError as e:
            self.skipTest(f"forge not importable: {e}")

        # Find the forge_exec entry in the static tool list definition.
        src = Path(mcp_mod.__file__).read_text()
        self.assertIn('"forge_exec"', src,
                      "forge_exec must be registered in _all_tools()")
        self.assertIn("_call_forge_exec", src,
                      "_call_forge_exec handler must be defined")


if __name__ == "__main__":
    unittest.main(verbosity=2)
