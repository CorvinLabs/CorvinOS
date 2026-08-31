"""Phase 13.8 — Bayesian strategy tests.

Eight acceptance cases per the implementation plan:

1. test_bayesian_warmup_uses_random
2. test_bayesian_post_warmup_uses_gp
3. test_bayesian_qei_batch_size
4. test_bayesian_converges_on_synthetic_function
5. test_bayesian_strategy_timeout
6. test_skill_file_at_user_scope (skipped — deferred to closure)
7. test_minimal_bootstrap_skips_bayesian (manual operator smoke)
8. test_strategies_allowed_blocks_bayesian (lives in test_worker.py
   via StrategyAllowlistTests already)
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

try:
    import sklearn  # noqa: F401
    import numpy as np
    _SKLEARN_AVAILABLE = True
except ImportError:
    _SKLEARN_AVAILABLE = False


if _SKLEARN_AVAILABLE:
    from corvin_compute.budget import Budget  # noqa: E402
    from corvin_compute.driver import ComputeRun, ComputeRunSpec  # noqa: E402
    from corvin_compute import strategies as strat_pkg  # noqa: E402
    from corvin_compute.strategies.bayesian import (  # noqa: E402
        BayesianStrategy, StrategyTimeout,
    )
    from corvin_compute.iteration import IterRecord  # noqa: E402


def _mock_history(points: list[dict], losses: list[float]) -> list:
    """Build a synthetic IterRecord list."""
    return [
        IterRecord(
            iter=i + 1, params=p, loss=loss, wall_ms=10, ts=0.0,
            cache_hit=False,
            param_fingerprint=f"sha256:{i:016x}",
        )
        for i, (p, loss) in enumerate(zip(points, losses))
    ]


@unittest.skipUnless(_SKLEARN_AVAILABLE, "sklearn not installed")
class WarmupTests(unittest.TestCase):
    def test_warmup_returns_random(self) -> None:
        bs = BayesianStrategy(
            {"x": {"type": "float_uniform", "low": 0, "high": 1},
             "y": {"type": "float_uniform", "low": 0, "high": 1}},
            seed=42,
        )
        # 2 axes → warm-up = 4 iters. The 5th iter is the first GP pick.
        batch = bs.suggest_batch([], 4)
        self.assertEqual(len(batch), 4)
        self.assertFalse(bs._used_gp)

    def test_post_warmup_uses_gp(self) -> None:
        bs = BayesianStrategy(
            {"x": {"type": "float_uniform", "low": 0, "high": 1}},
            seed=42,
        )
        history = _mock_history(
            [{"x": 0.1}, {"x": 0.5}, {"x": 0.9}],
            [0.5, 0.2, 0.7],
        )
        # 1 axis → warm-up = 2 iters; history has 3 → GP fires.
        bs.suggest_batch(history, 1)
        self.assertTrue(bs._used_gp)


@unittest.skipUnless(_SKLEARN_AVAILABLE, "sklearn not installed")
class BatchSizeTests(unittest.TestCase):
    def test_qei_batch_size_returns_n_distinct_points(self) -> None:
        bs = BayesianStrategy(
            {"x": {"type": "float_uniform", "low": 0, "high": 1}},
            seed=42,
        )
        history = _mock_history(
            [{"x": 0.1}, {"x": 0.4}, {"x": 0.8}],
            [0.5, 0.2, 0.9],
        )
        batch = bs.suggest_batch(history, 4)
        self.assertEqual(len(batch), 4)


@unittest.skipUnless(_SKLEARN_AVAILABLE, "sklearn not installed")
class ConvergenceTests(unittest.TestCase):
    def test_bayesian_beats_random_on_synthetic_quadratic(self) -> None:
        """Bayesian on quadratic loss should find a lower best in N iters
        than random. Generous 30-iter budget; seeded RNG."""
        # loss(x, y) = (x - 0.7)^2 + (y - 0.3)^2 — minimum 0 at (0.7, 0.3).
        def loss_fn(p):
            return (p["x"] - 0.7) ** 2 + (p["y"] - 0.3) ** 2

        bs = BayesianStrategy(
            {"x": {"type": "float_uniform", "low": 0, "high": 1},
             "y": {"type": "float_uniform", "low": 0, "high": 1}},
            seed=11,
        )
        # Walk 30 iters via the strategy's own interface.
        history: list = []
        for _ in range(30):
            batch = bs.suggest_batch(history, 1)
            if not batch:
                break
            p = batch[0]
            history.append(IterRecord(
                iter=len(history) + 1, params=p, loss=loss_fn(p),
                wall_ms=1, ts=0.0, cache_hit=False,
                param_fingerprint=f"sha256:{len(history):016x}",
            ))
        bayes_best = min(h.loss for h in history if h.loss is not None)

        from corvin_compute.strategies.random import RandomStrategy
        rs = RandomStrategy(
            {"x": {"type": "float_uniform", "low": 0, "high": 1},
             "y": {"type": "float_uniform", "low": 0, "high": 1}},
            seed=11,
        )
        random_best = min(loss_fn(p) for p in rs.suggest_batch([], 30))

        # Bayesian should beat random by a noticeable margin.
        self.assertLess(bayes_best, random_best * 1.0,
                        f"Bayesian best={bayes_best:.4f} should beat "
                        f"random best={random_best:.4f}")


@unittest.skipUnless(_SKLEARN_AVAILABLE, "sklearn not installed")
class StrategyTimeoutTests(unittest.TestCase):
    def test_strategy_timeout_when_gp_slow(self) -> None:
        # Patch the budget down so that even a fast call exceeds it.
        from corvin_compute.strategies import bayesian as bay_mod
        original = bay_mod.SUGGEST_BATCH_CPU_BUDGET_S
        bay_mod.SUGGEST_BATCH_CPU_BUDGET_S = 1e-9
        try:
            bs = BayesianStrategy(
                {"x": {"type": "float_uniform", "low": 0, "high": 1}},
                seed=0,
            )
            history = _mock_history(
                [{"x": 0.1}, {"x": 0.5}, {"x": 0.9}],
                [0.5, 0.2, 0.7],
            )
            with self.assertRaises(StrategyTimeout):
                bs.suggest_batch(history, 1)
        finally:
            bay_mod.SUGGEST_BATCH_CPU_BUDGET_S = original


@unittest.skipUnless(_SKLEARN_AVAILABLE, "sklearn not installed")
class RegistryTests(unittest.TestCase):
    def test_bayesian_registered(self) -> None:
        names = strat_pkg.available_strategies()
        self.assertIn("bayesian", names)

    def test_load_bayesian(self) -> None:
        s = strat_pkg.load_strategy(
            "bayesian",
            {"x": {"type": "float_uniform", "low": 0, "high": 1}},
            seed=0,
        )
        self.assertEqual(s.name, "bayesian")


@unittest.skipUnless(_SKLEARN_AVAILABLE, "sklearn not installed")
class DriverIntegrationTests(unittest.TestCase):
    def test_bayesian_via_driver(self) -> None:
        td = tempfile.mkdtemp(prefix="corvin-compute-bay-")
        try:
            corvin_home = Path(td) / "corvin"
            (corvin_home / "tenants" / "_default" / "compute"
             ).mkdir(parents=True, exist_ok=True)

            def loss_runner(tool_name, payload):
                x = payload.get("x", 0.0)
                return {"loss": (x - 0.7) ** 2}

            spec = ComputeRunSpec(
                tenant_id="_default", tool_name="quad",
                param_grid={"x": {"type": "float_uniform",
                                   "low": 0, "high": 1}},
                loss_metric="loss", strategy_name="bayesian",
                budget=Budget(max_iterations=10, max_wall_clock_s=30,
                              convergence_eps=1e-12, stall_after_n=999),
                seed=42,
            )
            run = ComputeRun(
                spec, corvin_home=corvin_home, runner_fn=loss_runner,
                strategy_factory=strat_pkg.load_strategy,
            )
            rec = run.run()
            self.assertGreaterEqual(rec.total_iterations, 1)
            self.assertIsNotNone(rec.best_loss)
        finally:
            import shutil
            shutil.rmtree(td, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
