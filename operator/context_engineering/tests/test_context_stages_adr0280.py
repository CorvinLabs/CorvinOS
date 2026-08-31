"""P-A — ContextStage contract + config-driven pipeline (ADR-0280).

Verifies the pipeline is now data + a dependency-ordered runner, without changing
the default behaviour:
  * default pipeline = the five first-party stages, correctly topo-ordered.
  * a config reorder is repaired by topo-sort (deps always precede dependents).
  * unknown stage ids are dropped (audited), never a crash.
  * a non-builtin stage is refused (no in-process isolation until P-G).
  * a cycle raises; the runner degrades to plain context.
  * memory (the root) failing → no brief (nothing downstream can attach).

Run: python3 operator/context_engineering/tests/test_context_stages_adr0280.py
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

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


class ContextStagesTests(unittest.TestCase):
    def setUp(self):
        self.ce = _load()
        self.stages = sys.modules["context_engineering.stages"]

    def test_default_pipeline_is_five_stages(self):
        # Isolate the tenant read: `resolve_pipeline` consults the AMBIENT
        # `spec.context_engineering.pipeline`, so on any install whose operator
        # authored a pipeline in the console editor this asserted against their
        # config instead of the default (it did — the test was red before the
        # 2026-08-18 session touched anything).
        with patch.object(self.stages.config, "_read_pipeline_config",
                          return_value=None):
            specs, dropped = self.stages.resolve_pipeline("_default")
        self.assertEqual([s.id for s in specs], self.stages.DEFAULT_PIPELINE)
        self.assertEqual(dropped, [])

    def test_topo_sort_repairs_a_reorder(self):
        # config puts skill before graph before memory — topo must fix it
        Spec = self.stages.StageSpec
        specs = [Spec("skill"), Spec("graph"), Spec("memory"),
                 Spec("blocker_id"), Spec("approach_synthesis")]
        order = [s.id for s in self.stages.topo_order(specs)]
        self.assertLess(order.index("memory"), order.index("graph"))
        self.assertLess(order.index("graph"), order.index("skill"))
        self.assertLess(order.index("skill"), order.index("approach_synthesis"))

    def test_unknown_id_dropped(self):
        Spec = self.stages.StageSpec
        with patch.object(self.stages.config, "_read_pipeline_config",
                          return_value=[{"stage": "memory"}, {"stage": "nonsense_x"}]):
            specs, dropped = self.stages.resolve_pipeline("_default")
        self.assertEqual([s.id for s in specs], ["memory"])
        self.assertIn("nonsense_x", dropped)

    def test_non_builtin_stage_refused(self):
        class Rogue:
            id = "rogue"; requires = (); effect = "pure"; trust = "community"
        with self.assertRaises(ValueError):
            self.stages.register_stage(Rogue())

    def test_cycle_raises(self):
        # a synthetic cycle via two fake registered stages
        class A:
            id = "cyc_a"; requires = ("cyc_b",); effect = "pure"; trust = "builtin"
            def run(self, b, c): return b, None
        class B:
            id = "cyc_b"; requires = ("cyc_a",); effect = "pure"; trust = "builtin"
            def run(self, b, c): return b, None
        self.stages.register_stage(A()); self.stages.register_stage(B())
        Spec = self.stages.StageSpec
        with self.assertRaises(ValueError):
            self.stages.topo_order([Spec("cyc_a"), Spec("cyc_b")])

    def test_memory_failure_yields_no_brief(self):
        from context_engineering.pipeline import build_brief
        ml = sys.modules["context_engineering.memory_lookup"]
        with patch.object(ml.MemoryLookup, "enrich_task",
                          side_effect=RuntimeError("mem down")):
            brief, trace = build_brief("x", "_default", None, meter=False)
        self.assertIsNone(brief)
        mem = next(s for s in trace["stages"] if s["stage"] == "memory")
        self.assertEqual(mem["status"], "failed")


if __name__ == "__main__":
    unittest.main()
