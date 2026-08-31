"""P-F — stage grade-gate + self-improving loop (ADR-0285).

  * grade_stage/get_grade accumulate {n_grades, mean_score}.
  * self-grading is excluded.
  * is_default_eligible: builtin vetted → always; opt-in needs sample+threshold.
  * bootstrap_seed is capped.
  * record_turn_outcome attributes success to the stages that ran.

Run: python3 operator/context_engineering/tests/test_grades_adr0285.py
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
for p in (_REPO / "operator" / "forge", _REPO / "core" / "console"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


def _load():
    ce = _REPO / "operator" / "context_engineering"
    spec = importlib.util.spec_from_file_location(
        "context_engineering", str(ce / "__init__.py"),
        submodule_search_locations=[str(ce)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules["context_engineering"] = mod
    spec.loader.exec_module(mod)
    return mod


class GradeGateTests(unittest.TestCase):
    def setUp(self):
        self.ce = _load()
        self.g = sys.modules["context_engineering.stages.grades"]
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        os.environ["CORVIN_HOME"] = self.td.name
        self.addCleanup(lambda: os.environ.pop("CORVIN_HOME", None))

    def test_accumulate(self):
        for s in (0.4, 0.6, 0.8):
            self.g.grade_stage("_default", "custom_x", s, grader="operator")
        gr = self.g.get_grade("_default", "custom_x")
        self.assertEqual(gr["n_grades"], 3)
        self.assertAlmostEqual(gr["mean_score"], 0.6, places=2)

    def test_self_grading_excluded(self):
        with self.assertRaises(ValueError):
            self.g.grade_stage("_default", "custom_x", 1.0, grader="custom_x")

    def test_eligibility(self):
        builtins = ["memory", "graph", "skill"]
        # builtin vetted → always
        self.assertTrue(self.g.is_default_eligible("_default", "memory", builtins))
        # opt-in with no grades → not default-eligible
        self.assertFalse(self.g.is_default_eligible("_default", "custom_x", builtins))
        # opt-in with 3 grades @ 0.6 → eligible
        for _ in range(3):
            self.g.grade_stage("_default", "custom_x", 0.6, grader="operator")
        self.assertTrue(self.g.is_default_eligible("_default", "custom_x", builtins))
        # opt-in with grades below threshold → not eligible
        for _ in range(3):
            self.g.grade_stage("_default", "weak_x", 0.2, grader="operator")
        self.assertFalse(self.g.is_default_eligible("_default", "weak_x", builtins))

    def test_empty_grader_rejected(self):
        # review R2 B2: an anonymous grade (empty grader) is rejected — the old
        # guard let a stage self-grade via the default empty grader.
        with self.assertRaises(ValueError):
            self.g.grade_stage("_default", "x", 0.9)
        with self.assertRaises(ValueError):
            self.g.grade_stage("_default", "x", 0.9, grader="x")  # self-grade

    def test_only_operator_grades_promote(self):
        # review R2 B2/C3/C4: neither a spoofed grader name NOR mere loop usage
        # promotes — only EXPLICIT operator grades count toward default-eligibility.
        builtins = ["memory"]
        for _ in range(3):
            self.g.grade_stage("_default", "sneaky", 1.0, grader="sneaky_ally")
        self.assertFalse(self.g.is_default_eligible("_default", "sneaky", builtins))
        # 3 loop-outcome grades are ADVISORY — they do NOT auto-promote (no human intent)
        for _ in range(3):
            self.g.grade_stage("_default", "used_a_lot", 1.0, grader="__loop__")
        self.assertFalse(self.g.is_default_eligible("_default", "used_a_lot", builtins))
        # only explicit operator grades promote
        for _ in range(3):
            self.g.grade_stage("_default", "approved", 1.0, grader="operator")
        self.assertTrue(self.g.is_default_eligible("_default", "approved", builtins))

    def test_bootstrap_capped(self):
        self.g.bootstrap_seed("_default", "new_x", score=0.9)  # asks 0.9
        gr = self.g.get_grade("_default", "new_x")
        self.assertLessEqual(gr["mean_score"], self.g._BOOTSTRAP_CAP)

    def test_outcome_loop(self):
        self.g.record_turn_outcome("_default", ["memory", "graph"], success=True)
        self.assertEqual(self.g.get_grade("_default", "memory")["mean_score"], 1.0)
        self.g.record_turn_outcome("_default", ["memory"], success=False)
        # mean now (1.0 + 0.0)/2
        self.assertAlmostEqual(self.g.get_grade("_default", "memory")["mean_score"], 0.5, places=2)


if __name__ == "__main__":
    unittest.main()
