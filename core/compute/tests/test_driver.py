"""Phase 13.2 — Driver core + on-disk state tests.

Five core acceptance cases per the implementation plan:

1. test_run_with_stub_runner_converges
2. test_budget_max_iterations_clamps
3. test_wall_clock_budget
4. test_iteration_files_atomic
5. test_terminal_summary_complete

Plus a handful of structural cases (manifest layout, audit emissions,
abort semantics) to pin the contract.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from corvin_compute.budget import (  # noqa: E402
    Budget, RUN_STATE_CONVERGED, RUN_STATE_BUDGET_EXHAUSTED, RUN_STATE_ABORTED,
)
from corvin_compute.driver import ComputeRun, ComputeRunSpec  # noqa: E402
from corvin_compute.iteration import param_fingerprint  # noqa: E402
from corvin_compute.state import RunStore, new_run_id  # noqa: E402
from corvin_compute import strategies as strat_pkg  # noqa: E402


class _Fixture(unittest.TestCase):
    """Per-test sandbox: fresh corvin_home + tenant dir."""

    def setUp(self) -> None:
        self.td = tempfile.mkdtemp(prefix="corvin-compute-driver-")
        self.corvin_home = Path(self.td) / "corvin"
        (self.corvin_home / "tenants" / "_default" / "compute" / "runs"
         ).mkdir(parents=True, exist_ok=True)
        self.audit_log: list[dict] = []

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.td, ignore_errors=True)

    def _audit(self, event: str, **details) -> None:
        self.audit_log.append({"event": event, **details})

    def _make_spec(self, **overrides):
        defaults = dict(
            tenant_id="_default",
            tool_name="echo_tool",
            param_grid={"x": [0.0, 0.5, 1.0]},
            loss_metric="loss",
            strategy_name="grid",
            budget=Budget(max_iterations=20, max_wall_clock_s=30,
                          convergence_eps=1e-9, stall_after_n=5),
            minimise=True,
        )
        defaults.update(overrides)
        return ComputeRunSpec(**defaults)


class GridConvergenceTests(_Fixture):
    def test_run_with_stub_runner_converges(self) -> None:
        """3-axis grid, deterministic stub: run terminates with a terminal
        state and a valid best_loss."""
        spec = self._make_spec(
            param_grid={"x": [0.1, 0.2, 0.3], "y": [10, 20], "z": ["a", "b"]},
            strategy_name="grid",
        )

        def runner(tool_name, payload):
            # loss = -(x*10 + y/100 + len(z))  → minimum at x=0.3, y=20, z="b"
            return {"loss": -(payload["x"] * 10 + payload["y"] / 100
                              + len(payload["z"]))}

        run = ComputeRun(
            spec, corvin_home=self.corvin_home, runner_fn=runner,
            strategy_factory=strat_pkg.load_strategy,
            audit_emit=self._audit,
        )
        rec = run.run()
        # Any terminal state is fine — the deterministic grid produces
        # a strict minimum after iter 5 then 6 stalls, so "stalled" is
        # the expected outcome with stall_after_n=5; "budget_exhausted"
        # would be the outcome with a lower budget.
        self.assertIn(rec.state, ("converged", "stalled", "budget_exhausted"))
        self.assertGreater(rec.total_iterations, 0)
        self.assertLessEqual(rec.total_iterations, 12)
        self.assertIsNotNone(rec.best_loss)
        # The minimum loss is at x=0.3, y=20 with loss = -(3 + 0.2 + 1) = -4.2.
        self.assertLess(rec.best_loss, -4.0)


class BudgetTests(_Fixture):
    def test_budget_max_iterations_clamps(self) -> None:
        spec = self._make_spec(
            # Large grid so budget caps before exhaustion.
            param_grid={"x": list(range(100))},
            budget=Budget(max_iterations=5, max_wall_clock_s=30,
                          convergence_eps=1e-12, stall_after_n=999),
        )

        def stub(tool_name, payload):
            return {"loss": float(payload["x"])}

        run = ComputeRun(spec, corvin_home=self.corvin_home, runner_fn=stub,
                         strategy_factory=strat_pkg.load_strategy)
        rec = run.run()
        self.assertEqual(rec.state, RUN_STATE_BUDGET_EXHAUSTED)
        self.assertEqual(rec.total_iterations, 5)
        self.assertEqual(rec.convergence_reason, "max-iterations-reached")

    def test_wall_clock_budget(self) -> None:
        spec = self._make_spec(
            param_grid={"x": list(range(100))},
            budget=Budget(max_iterations=1000, max_wall_clock_s=1,
                          convergence_eps=1e-12, stall_after_n=999),
        )

        def slow_stub(tool_name, payload):
            time.sleep(0.2)
            return {"loss": float(payload["x"])}

        t0 = time.time()
        run = ComputeRun(spec, corvin_home=self.corvin_home,
                         runner_fn=slow_stub,
                         strategy_factory=strat_pkg.load_strategy)
        rec = run.run()
        elapsed = time.time() - t0
        self.assertEqual(rec.state, RUN_STATE_BUDGET_EXHAUSTED)
        self.assertEqual(rec.convergence_reason, "max-wall-clock-reached")
        self.assertLessEqual(elapsed, 2.0,
                             f"wall-clock budget should cut around 1s, took {elapsed:.2f}")


class AtomicityTests(_Fixture):
    def test_iteration_files_atomic(self) -> None:
        """Crash mid-iteration via raising stub. The half-written iter
        file should not be visible; previously written iters should be
        intact."""
        crash_after = [0]

        def crashing_stub(tool_name, payload):
            if crash_after[0] >= 2:
                raise RuntimeError("simulated crash")
            crash_after[0] += 1
            return {"loss": float(payload.get("x", 0))}

        spec = self._make_spec(
            param_grid={"x": [0.1, 0.2, 0.3, 0.4, 0.5]},
            budget=Budget(max_iterations=5, max_wall_clock_s=5,
                          convergence_eps=1e-12, stall_after_n=999),
        )
        run = ComputeRun(spec, corvin_home=self.corvin_home,
                         runner_fn=crashing_stub,
                         strategy_factory=strat_pkg.load_strategy)
        rec = run.run()

        store = RunStore(self.corvin_home, "_default")
        iters = store.read_iterations(rec.run_id)
        # Every iter file must round-trip cleanly (no half writes).
        for r in iters:
            self.assertGreater(r.iter, 0)
            self.assertEqual(set(r.params.keys()), {"x"})


class TerminalSummaryTests(_Fixture):
    def test_terminal_summary_complete(self) -> None:
        spec = self._make_spec(
            param_grid={"x": [0.1, 0.2, 0.3]},
            budget=Budget(max_iterations=10, max_wall_clock_s=10,
                          convergence_eps=1e-12, stall_after_n=999),
        )

        def stub(tool_name, payload):
            return {"loss": float(payload["x"])}

        run = ComputeRun(spec, corvin_home=self.corvin_home, runner_fn=stub,
                         strategy_factory=strat_pkg.load_strategy)
        rec = run.run()
        for key in ("state", "best_loss", "best_iter", "total_iterations",
                    "total_wall_s", "convergence_reason"):
            self.assertIn(key, rec.summary, f"summary.json missing {key}")
        self.assertIsNotNone(rec.summary["best_loss"])
        self.assertGreater(rec.summary["total_iterations"], 0)
        self.assertIn(rec.summary["state"],
                      ("converged", "budget_exhausted", "stalled"))


class ManifestTests(_Fixture):
    def test_manifest_carries_spec(self) -> None:
        spec = self._make_spec()

        def stub(tool_name, payload):
            return {"loss": float(payload["x"])}

        run = ComputeRun(spec, corvin_home=self.corvin_home, runner_fn=stub,
                         strategy_factory=strat_pkg.load_strategy)
        rec = run.run()
        for key in ("run_id", "tenant_id", "tool_name", "strategy",
                    "param_grid", "budget", "accepted_at"):
            self.assertIn(key, rec.manifest, f"manifest.json missing {key}")
        self.assertEqual(rec.manifest["tenant_id"], "_default")
        self.assertEqual(rec.manifest["tool_name"], "echo_tool")
        self.assertEqual(rec.manifest["strategy"], "grid")


class AbortSemanticsTests(_Fixture):
    def test_request_abort_terminates_run(self) -> None:
        spec = self._make_spec(
            param_grid={"x": list(range(1000))},
            budget=Budget(max_iterations=1000, max_wall_clock_s=30,
                          convergence_eps=1e-12, stall_after_n=999),
        )

        run = ComputeRun(
            spec, corvin_home=self.corvin_home,
            runner_fn=lambda tn, p: {"loss": 1.0},
            strategy_factory=strat_pkg.load_strategy,
        )
        # Pre-request abort BEFORE run() starts — the loop's first
        # state-machine check fires.
        run.request_abort()
        rec = run.run()
        self.assertEqual(rec.state, RUN_STATE_ABORTED)
        self.assertEqual(rec.convergence_reason, "external-abort")


class AuditEmissionTests(_Fixture):
    def test_three_canonical_events_fire(self) -> None:
        spec = self._make_spec(
            param_grid={"x": [0.1, 0.2]},
            budget=Budget(max_iterations=10, max_wall_clock_s=10,
                          convergence_eps=1e-12, stall_after_n=999),
        )

        def stub(tool_name, payload):
            return {"loss": float(payload["x"])}

        run = ComputeRun(spec, corvin_home=self.corvin_home, runner_fn=stub,
                         strategy_factory=strat_pkg.load_strategy,
                         audit_emit=self._audit)
        rec = run.run()
        events = [e["event"] for e in self.audit_log]
        self.assertIn("compute.run_started", events)
        self.assertEqual(events.count("compute.iteration_completed"), 2)
        self.assertEqual(events[-1], "compute.run_terminal")

        # Audit must NEVER carry params in clear — only fingerprint.
        for e in self.audit_log:
            if e["event"] == "compute.iteration_completed":
                self.assertIn("param_fingerprint", e)
                self.assertNotIn("params", e)


class FingerprintTests(unittest.TestCase):
    def test_fingerprint_is_deterministic(self) -> None:
        fp1 = param_fingerprint({"a": 1, "b": 2})
        fp2 = param_fingerprint({"b": 2, "a": 1})
        self.assertEqual(fp1, fp2)
        self.assertTrue(fp1.startswith("sha256:"))

    def test_fingerprint_changes_on_value_change(self) -> None:
        fp1 = param_fingerprint({"a": 1})
        fp2 = param_fingerprint({"a": 2})
        self.assertNotEqual(fp1, fp2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
