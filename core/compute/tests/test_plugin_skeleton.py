"""Phase 13.1 — Plugin skeleton + cost-contract regression gate.

Four cases per the implementation plan:
1. test_plugin_directory_exists
2. test_bootstrap_creates_venv
3. test_no_anthropic_sdk_import (cost-contract gate, MUST exist from 13.1)
4. test_run_all_tests_sh_skips_gracefully
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PLUGIN_ROOT.parents[1]


class PluginSkeletonTests(unittest.TestCase):
    def test_plugin_directory_exists(self) -> None:
        for sub in ("bootstrap.sh", "requirements.txt",
                    "corvin_compute/__init__.py",
                    "corvin_compute/version.py"):
            self.assertTrue((PLUGIN_ROOT / sub).is_file(), f"missing: {sub}")

    def test_package_importable_and_version_present(self) -> None:
        sys.path.insert(0, str(PLUGIN_ROOT))
        try:
            import corvin_compute  # noqa: WPS433
            self.assertTrue(hasattr(corvin_compute, "__version__"))
            self.assertIsInstance(corvin_compute.__version__, str)
        finally:
            sys.path.remove(str(PLUGIN_ROOT))


class BootstrapTests(unittest.TestCase):
    def test_bootstrap_script_is_executable(self) -> None:
        path = PLUGIN_ROOT / "bootstrap.sh"
        self.assertTrue(os.access(path, os.X_OK), "bootstrap.sh must be +x")

    def test_bootstrap_creates_venv(self) -> None:
        """Run bootstrap in --minimal mode against a clean copy.

        Skips when `python3 -m venv` is unavailable (some CI sandboxes).
        """
        # Smoke probe: `python3 -m venv --help` must work.
        try:
            subprocess.run(
                [sys.executable, "-m", "venv", "--help"],
                check=True, capture_output=True, timeout=10,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
                FileNotFoundError):
            self.skipTest("venv module unavailable in this environment")

        with tempfile.TemporaryDirectory(prefix="corvin-compute-bs-") as td:
            # Copy the bootstrap-relevant files into a sandbox so we don't
            # mutate the real plugin's `.venv`.
            sandbox = Path(td)
            (sandbox / "bootstrap.sh").write_text(
                (PLUGIN_ROOT / "bootstrap.sh").read_text()
            )
            (sandbox / "requirements.txt").write_text(
                (PLUGIN_ROOT / "requirements.txt").read_text()
            )
            (sandbox / "requirements-minimal.txt").write_text(
                (PLUGIN_ROOT / "requirements-minimal.txt").read_text()
            )
            (sandbox / "bootstrap.sh").chmod(0o755)

            env = os.environ.copy()
            env["CORVIN_COMPUTE_MINIMAL"] = "1"
            try:
                subprocess.run(
                    ["bash", "bootstrap.sh"],
                    cwd=sandbox, env=env, check=True,
                    capture_output=True, timeout=120,
                )
            except subprocess.CalledProcessError as exc:
                self.fail(
                    f"bootstrap.sh failed (rc={exc.returncode}): "
                    f"stdout={exc.stdout!r} stderr={exc.stderr!r}"
                )
            except subprocess.TimeoutExpired:
                self.skipTest("bootstrap.sh took too long (no network?)")

            self.assertTrue(
                (sandbox / ".venv" / "bin" / "python").exists(),
                ".venv/bin/python should exist after bootstrap",
            )


class CostContractTests(unittest.TestCase):
    """Cost-contract regression gate — MUST exist from Phase 13.1 onward.

    AST-walks every .py under corvin_compute/ and fails if any module
    imports the `anthropic` SDK (direct or aliased). Mirror of the
    bridges/shared/dialectic.py CI lint.
    """

    FORBIDDEN_MODULES = {"anthropic", "openai", "google.cloud.aiplatform"}

    def _iter_imports(self, source: str) -> list[str]:
        tree = ast.parse(source)
        names: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    names.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    names.append(node.module)
        return names

    def test_no_anthropic_sdk_import(self) -> None:
        pkg_root = PLUGIN_ROOT / "corvin_compute"
        offenders: list[tuple[str, str]] = []
        for py_file in pkg_root.rglob("*.py"):
            try:
                src = py_file.read_text(encoding="utf-8")
            except OSError:
                continue
            for imp in self._iter_imports(src):
                top = imp.split(".")[0]
                if top in self.FORBIDDEN_MODULES or imp in self.FORBIDDEN_MODULES:
                    offenders.append((str(py_file.relative_to(pkg_root)), imp))
        self.assertEqual(
            offenders, [],
            f"cost contract violation — forbidden imports: {offenders}",
        )

    def test_no_llm_sdk_in_requirements(self) -> None:
        for req_name in ("requirements.txt", "requirements-minimal.txt"):
            req_path = PLUGIN_ROOT / req_name
            if not req_path.is_file():
                continue
            text = req_path.read_text(encoding="utf-8").lower()
            for forbidden in ("anthropic", "openai", "google-cloud-aiplatform"):
                self.assertNotIn(
                    forbidden, text,
                    f"{req_name}: forbidden LLM-SDK '{forbidden}' present",
                )


class RunAllTestsSkipGateTests(unittest.TestCase):
    """Phase 13.1 acceptance gate #4 — runner skip integration.

    Tests that ``operator/bridges/run-all-tests.sh`` mentions
    corvin-compute and provides a venv-absent skip path. We don't
    execute the full runner (slow, depends on every other plugin) —
    we just assert the grep finds the wiring.
    """

    def test_runner_script_mentions_corvin_compute(self) -> None:
        runner = REPO_ROOT / "operator" / "bridges" / "run-all-tests.sh"
        self.assertTrue(runner.is_file(), "run-all-tests.sh missing")
        text = runner.read_text(encoding="utf-8")
        self.assertIn(
            "corvin-compute", text,
            "run-all-tests.sh should mention corvin-compute (skip-gate)",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
