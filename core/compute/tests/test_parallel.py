"""Phase 13.7 — Parallel driver + parametric cache tests."""
from __future__ import annotations

import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PLUGIN_ROOT.parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))
sys.path.insert(0, str(REPO_ROOT / "operator" / "forge"))

from corvin_compute.budget import Budget  # noqa: E402
from corvin_compute.driver import ComputeRunSpec  # noqa: E402
from corvin_compute.parallel import (  # noqa: E402
    IterationTimeout, ParallelDriver,
)
from corvin_compute import strategies as strat_pkg  # noqa: E402

from forge.cache import cache_key  # noqa: E402


class _Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self.td = tempfile.mkdtemp(prefix="corvin-compute-par-")
        self.corvin_home = Path(self.td) / "corvin"
        (self.corvin_home / "tenants" / "_default" / "compute" / "runs"
         ).mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.td, ignore_errors=True)


class SpeedupTests(_Fixture):
    def test_parallel_driver_shows_real_speedup(self) -> None:
        """Stub runner sleeps 100ms; batch=8 with 4 workers should be
        materially faster than serial (8 × 100 ms = 800 ms serial)."""
        call_log: list[float] = []
        log_lock = threading.Lock()

        def slow_runner(tool_name, payload):
            with log_lock:
                call_log.append(time.time())
            time.sleep(0.1)
            return {"loss": float(payload["x"])}

        spec = ComputeRunSpec(
            tenant_id="_default",
            tool_name="slow",
            param_grid={"x": list(range(8))},
            loss_metric="loss",
            strategy_name="grid",
            budget=Budget(max_iterations=8, max_wall_clock_s=30,
                          convergence_eps=1e-12, stall_after_n=999),
            minimise=True,
        )
        driver = ParallelDriver(
            spec, corvin_home=self.corvin_home, runner_fn=slow_runner,
            strategy_factory=strat_pkg.load_strategy,
            max_parallel=4,
        )
        t0 = time.time()
        rec = driver.run()
        wall = time.time() - t0
        self.assertGreaterEqual(rec.total_iterations, 8)
        # 4 workers should be ≥ 2× faster than serial (allow generous
        # slack for thread overhead on shared CI runners).
        self.assertLess(wall, 0.6,
                        f"parallel run should be < 600 ms, took {wall*1000:.0f} ms")

    def test_parallel_single_worker_is_sequential(self) -> None:
        """max_parallel=1 → no thread pool, deterministic ordering."""
        order: list[int] = []

        def runner(tool_name, payload):
            order.append(payload["x"])
            return {"loss": float(payload["x"])}

        spec = ComputeRunSpec(
            tenant_id="_default", tool_name="echo",
            param_grid={"x": [10, 20, 30, 40]},
            loss_metric="loss", strategy_name="grid",
            budget=Budget(max_iterations=4, max_wall_clock_s=5,
                          convergence_eps=1e-12, stall_after_n=999),
        )
        driver = ParallelDriver(
            spec, corvin_home=self.corvin_home, runner_fn=runner,
            strategy_factory=strat_pkg.load_strategy,
            max_parallel=1,
        )
        rec = driver.run()
        # Grid order: x=10, x=20, x=30, x=40
        self.assertEqual(order, [10, 20, 30, 40])
        self.assertGreaterEqual(rec.total_iterations, 4)


class TimeoutTests(_Fixture):
    def test_iteration_timeout_fails_run(self) -> None:
        def hang_runner(tool_name, payload):
            time.sleep(3.0)
            return {"loss": 0.0}

        spec = ComputeRunSpec(
            tenant_id="_default", tool_name="hang",
            param_grid={"x": [1, 2, 3, 4]},
            loss_metric="loss", strategy_name="grid",
            budget=Budget(max_iterations=4, max_wall_clock_s=30,
                          convergence_eps=1e-12, stall_after_n=999),
        )
        driver = ParallelDriver(
            spec, corvin_home=self.corvin_home, runner_fn=hang_runner,
            strategy_factory=strat_pkg.load_strategy,
            max_parallel=2, iter_timeout_s=0.5,
        )
        rec = driver.run()
        self.assertEqual(rec.state, "failed")
        self.assertIn("iteration-timeout", rec.convergence_reason)


class BatchSizeTests(_Fixture):
    def test_strategy_batch_size_respects_max_parallel(self) -> None:
        seen_sizes: list[int] = []

        class _SpyStrategy:
            name = "spy"

            def __init__(self):
                self.idx = 0

            def suggest_batch(self, history, n):
                seen_sizes.append(n)
                if self.idx >= 20:
                    return []
                points = [{"x": self.idx + i} for i in range(n)]
                self.idx += n
                return points

            def update(self, history, new_results):
                pass

            def should_stop(self, history):
                return self.idx >= 20, "done"

        def runner(tool_name, payload):
            return {"loss": float(payload["x"])}

        def fac(name, grid, *, minimise=True, seed=None):
            return _SpyStrategy()

        spec = ComputeRunSpec(
            tenant_id="_default", tool_name="echo",
            param_grid={"x": list(range(20))},
            loss_metric="loss", strategy_name="spy",
            budget=Budget(max_iterations=20, max_wall_clock_s=10,
                          convergence_eps=1e-12, stall_after_n=999),
        )
        driver = ParallelDriver(
            spec, corvin_home=self.corvin_home, runner_fn=runner,
            strategy_factory=fac, max_parallel=5,
        )
        driver.run()
        self.assertTrue(seen_sizes, "strategy.suggest_batch never called")
        # Every call must respect the max_parallel cap (5).
        self.assertTrue(all(s <= 5 for s in seen_sizes),
                        f"batch size exceeded max_parallel: {seen_sizes}")


class ParametricCacheTests(unittest.TestCase):
    """Forge cache extension: x-cache-key honoured."""

    def test_no_x_cache_key_uses_full_payload(self) -> None:
        # Two payloads differing in _artifacts_dir hit the same key
        # (legacy behaviour preserved).
        schema = {"properties": {"window": {"type": "integer"}}}
        p1 = {"window": 7, "_artifacts_dir": "/tmp/a"}
        p2 = {"window": 7, "_artifacts_dir": "/tmp/b"}
        k1 = cache_key(tool_sha="abc", payload=p1, input_schema=schema)
        k2 = cache_key(tool_sha="abc", payload=p2, input_schema=schema)
        self.assertEqual(k1, k2)
        # And changing `window` changes the key.
        k3 = cache_key(tool_sha="abc", payload={"window": 8}, input_schema=schema)
        self.assertNotEqual(k1, k3)

    def test_x_cache_key_subset_hits_when_irrelevant_field_changes(self) -> None:
        schema = {"properties": {
            "window":  {"type": "integer", "x-cache-key": True},
            "verbose": {"type": "boolean"},
        }}
        p1 = {"window": 7, "verbose": False}
        p2 = {"window": 7, "verbose": True}  # outside cache key
        k1 = cache_key(tool_sha="abc", payload=p1, input_schema=schema)
        k2 = cache_key(tool_sha="abc", payload=p2, input_schema=schema)
        self.assertEqual(k1, k2)

    def test_x_cache_key_miss_on_relevant_field_change(self) -> None:
        schema = {"properties": {
            "window":  {"type": "integer", "x-cache-key": True},
        }}
        p1 = {"window": 7}
        p2 = {"window": 8}
        k1 = cache_key(tool_sha="abc", payload=p1, input_schema=schema)
        k2 = cache_key(tool_sha="abc", payload=p2, input_schema=schema)
        self.assertNotEqual(k1, k2)

    def test_back_compat_no_schema(self) -> None:
        """Calling without input_schema — pre-13.7 behaviour."""
        p = {"window": 7}
        k_legacy = cache_key(tool_sha="abc", payload=p)
        k_with_schema_none = cache_key(tool_sha="abc", payload=p,
                                       input_schema=None)
        self.assertEqual(k_legacy, k_with_schema_none)


if __name__ == "__main__":
    unittest.main(verbosity=2)
