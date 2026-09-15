"""Unit tests for ato_loss.py (ADR-0164 M4).

Tests cover:
  - EMA update correctness (alpha=0.2)
  - record_outcome persists data atomically
  - get_stats / get_summary read back correct values
  - Alert thresholds trigger _maybe_alert (mocked)
  - File mode 0600 on output
  - No cross-tenant reads
  - Does NOT import anthropic (CI invariant)
"""
from __future__ import annotations

import ast
import importlib
import json
import os
import sys
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path

_here = Path(__file__).resolve().parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

import ato_loss as al


class TestEMAHelper(unittest.TestCase):
    def test_first_sample_returns_value(self):
        self.assertAlmostEqual(al._ema(None, 1.0), 1.0)
        self.assertAlmostEqual(al._ema(None, 0.0), 0.0)

    def test_ema_alpha_0_2(self):
        # r ← 0.2 * new + 0.8 * current
        result = al._ema(0.8, 1.0)
        self.assertAlmostEqual(result, 0.2 * 1.0 + 0.8 * 0.8, places=6)

    def test_ema_converges_toward_new_val(self):
        r = 0.0
        for _ in range(50):
            r = al._ema(r, 1.0)
        self.assertGreater(r, 0.99)


class TestRecordOutcome(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        # Patch _stats_path to use isolated tmpdir
        self._patcher = mock.patch(
            "ato_loss._ato_dir",
            return_value=Path(self._tmpdir),
        )
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_first_record_creates_file(self):
        al.record_outcome("iterative_fix", did_converge=True)
        p = al._stats_path()
        self.assertTrue(p.exists())

    def test_file_mode_0600(self):
        al.record_outcome("iterative_fix", did_converge=True)
        p = al._stats_path()
        mode = oct(p.stat().st_mode & 0o777)
        self.assertEqual(mode, oct(0o600), "loss_stats.json must be mode 0600")

    def test_returns_entry_dict(self):
        entry = al.record_outcome("iterative_fix", did_converge=True)
        self.assertIn("samples", entry)
        self.assertIn("convergence_rate", entry)
        self.assertIn("goal_revision_rate", entry)
        self.assertIn("strategy_correction_rate", entry)

    def test_samples_increment(self):
        for _ in range(3):
            al.record_outcome("iterative_fix", did_converge=True)
        entry = al.get_stats("iterative_fix")
        self.assertEqual(entry["samples"], 3)

    def test_convergence_rate_ema(self):
        # converge=True → 1.0, converge=False → 0.0
        # First: conv_rate = 1.0; Second (False): 0.2*0 + 0.8*1 = 0.8
        al.record_outcome("t", did_converge=True)
        al.record_outcome("t", did_converge=False)
        entry = al.get_stats("t")
        expected = round(0.2 * 0.0 + 0.8 * 1.0, 4)
        self.assertAlmostEqual(entry["convergence_rate"], expected, places=3)

    def test_goal_revision_rate_ema(self):
        al.record_outcome("t", did_converge=True, goal_revised=True)
        entry = al.get_stats("t")
        self.assertAlmostEqual(entry["goal_revision_rate"], 1.0, places=3)

    def test_strategy_correction_rate_ema(self):
        al.record_outcome("t", did_converge=True, strategy_corrected=True)
        entry = al.get_stats("t")
        self.assertAlmostEqual(entry["strategy_correction_rate"], 1.0, places=3)

    def test_multiple_task_types_isolated(self):
        al.record_outcome("iterative_fix", did_converge=True)
        al.record_outcome("one_shot",      did_converge=False)
        e1 = al.get_stats("iterative_fix")
        e2 = al.get_stats("one_shot")
        self.assertAlmostEqual(e1["convergence_rate"], 1.0, places=3)
        self.assertAlmostEqual(e2["convergence_rate"], 0.0, places=3)

    def test_get_stats_returns_none_when_not_tracked(self):
        self.assertIsNone(al.get_stats("nonexistent_type"))

    def test_get_summary_returns_all_types(self):
        al.record_outcome("a", did_converge=True)
        al.record_outcome("b", did_converge=False)
        summary = al.get_summary()
        self.assertIn("a", summary)
        self.assertIn("b", summary)

    def test_json_on_disk_parseable(self):
        al.record_outcome("iterative_fix", did_converge=True)
        raw = al._stats_path().read_text(encoding="utf-8")
        data = json.loads(raw)
        self.assertIn("iterative_fix", data)


class TestAlertThresholds(unittest.TestCase):
    """_maybe_alert fires for threshold violations; silent otherwise."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._patcher = mock.patch("ato_loss._ato_dir", return_value=Path(self._tmpdir))
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _fill(self, task_type: str, n: int,
              converge: bool = True, goal: bool = False, strat: bool = False) -> None:
        for _ in range(n):
            al.record_outcome(task_type, did_converge=converge,
                              goal_revised=goal, strategy_corrected=strat)

    def test_no_alert_below_min_samples(self):
        with mock.patch("ato_loss._maybe_alert") as m:
            # 4 samples < _MIN_SAMPLES (5)
            self._fill("t", 4, converge=False)
            m.assert_not_called()

    def test_convergence_low_alert_fires(self):
        with mock.patch("ato_loss._maybe_alert") as m:
            # 5 samples, all non-converging → conv_rate low
            self._fill("t", 5, converge=False)
            m.assert_called()

    def test_alert_called_when_samples_enough(self):
        with mock.patch("ato_loss._maybe_alert") as m:
            self._fill("t", 6, converge=True)
            # _maybe_alert must be called once >= _MIN_SAMPLES (5) is reached
            m.assert_called()


class TestAtoDirTenantIsolation(unittest.TestCase):
    """Regression tests for the _ato_dir() HIGH bugs (ADR-0007 cross-tenant isolation)."""

    def _fake_paths(self, fn):
        """Return a fake 'paths' module with tenant_home = fn."""
        import types
        m = types.ModuleType("paths")
        m.tenant_home = fn  # plain function, no self-binding
        return m

    def test_invalid_tenant_id_propagates_value_error(self):
        """ValueError from paths.tenant_home() must NOT be swallowed by _ato_dir()."""
        def bad_home(tid):
            raise ValueError("bad tenant")

        with mock.patch.dict("sys.modules", {"paths": self._fake_paths(bad_home)}):
            with self.assertRaises(ValueError):
                al._ato_dir("../../evil")

    def test_none_tenant_id_delegates_to_tenant_home(self):
        """_ato_dir(None) must call tenant_home(None), not bypass it."""
        calls: list = []
        fake_home = Path("/fake/tenants/_default")

        def mock_home(tid):
            calls.append(tid)
            return fake_home

        with mock.patch.dict("sys.modules", {"paths": self._fake_paths(mock_home)}):
            result = al._ato_dir(None)

        self.assertIn(None, calls, "_ato_dir(None) must call tenant_home(None)")
        self.assertEqual(result, fake_home / "global" / "ato")


class TestNoCrossImport(unittest.TestCase):
    """ato_loss.py must not import anthropic (CI AST lint invariant)."""

    def test_no_anthropic_import(self):
        src = Path(_here / "ato_loss.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = (
                    [alias.name for alias in node.names]
                    if isinstance(node, ast.Import)
                    else ([node.module] if node.module else [])
                )
                for name in names:
                    self.assertFalse(
                        name and name.startswith("anthropic"),
                        f"ato_loss.py must not import anthropic — found: {name}",
                    )


if __name__ == "__main__":
    unittest.main(verbosity=2)
