"""No test module may disable a fail-closed gate at IMPORT time.

pytest imports every collected module before the first test runs, so a
``patch(...).start()`` at module level (never stopped) changes the process for
EVERY test in a combined session — including the tests that prove the gate
denies. Found 2026-10-05: four A2A test modules switched off the L44
house-rules gate (``spawn_gates.check_l44`` / ``house_rules.HouseRulesGate``)
at import, and the console's house-rules deny test saw a spawn after a DENY
verdict whenever they were collected in the same run.

Allowed: starting such a patch inside setUpModule/setup_module (and stopping it
in the teardown), a fixture, a test body, or a ``with`` block.
"""
from __future__ import annotations

import ast
import tempfile
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_GATES = ("check_l44", "HouseRulesGate", "_house_rules_classifier", "check_console_spawn_or_refusal",
          "check_house_rules", "_check_house_rules_or_fail", "data_flow_guard", "egress")
_SKIP = {"node_modules", ".venv", "venv", ".git", "dist", "build", "__pycache__"}


def _module_level_gate_starts(path: Path) -> list[int]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return []
    patch_vars = {}
    hits = []
    for node in tree.body:  # module level only
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            src = ast.unparse(node.value)
            if any(g in src for g in _GATES):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        patch_vars[t.id] = node.lineno
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            call = node.value
            if isinstance(call.func, ast.Attribute) and call.func.attr == "start":
                target = call.func.value
                src = ast.unparse(target)
                if any(g in src for g in _GATES) or (isinstance(target, ast.Name) and target.id in patch_vars):
                    hits.append(node.lineno)
    return hits


def _test_files():
    """Tracked test modules only (worktrees and vendored copies are not
    this repo's tests)."""
    import subprocess  # noqa: PLC0415
    out = subprocess.run(["git", "-C", str(_REPO), "ls-files", "*test_*.py"],
                         capture_output=True, text=True, check=True).stdout
    for rel in out.splitlines():
        p = _REPO / rel
        if p.name.startswith("test_") and not _SKIP.intersection(p.parts) and p.is_file():
            yield p


class NoImportTimeGatePatchTests(unittest.TestCase):
    def test_no_module_level_gate_patch_is_started(self):
        offenders = []
        scanned = 0
        for p in _test_files():
            scanned += 1
            for line in _module_level_gate_starts(p):
                offenders.append(f"{p.relative_to(_REPO)}:{line}")
        self.assertGreater(scanned, 500, "positive control: the scan must reach the repo's tests")
        self.assertEqual(offenders, [], "start the gate patch in setUpModule/setup_module instead")

    def test_scan_catches_an_injected_violation(self):
        src = ('from unittest import mock\nimport spawn_gates\n'
               'mock.patch.object(spawn_gates, "check_l44", lambda *a, **k: None).start()\n'
               '_P = mock.patch.object(spawn_gates, "check_l44", None)\n_P.start()\n'
               'def setUpModule():\n    _P.start()\n')
        p = Path(tempfile.mkdtemp()) / "test_x.py"
        p.write_text(src)
        self.assertEqual(_module_level_gate_starts(p), [3, 5])


if __name__ == "__main__":
    unittest.main()
