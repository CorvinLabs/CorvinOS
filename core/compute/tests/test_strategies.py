"""Phase 13.3 — Grid + Random strategy tests.

Six core acceptance cases per the implementation plan:

1. test_grid_enumerates_cartesian
2. test_grid_batch_smaller_than_remaining
3. test_random_independent_samples
4. test_random_respects_distributions
5. test_strategy_protocol_satisfied
6. test_strategy_registry_load
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from corvin_compute import strategies as strat_pkg  # noqa: E402
from corvin_compute.strategies import (  # noqa: E402
    UnknownStrategy, load_strategy, available_strategies,
)
from corvin_compute.strategies.base import Strategy  # noqa: E402
from corvin_compute.strategies.grid import GridStrategy  # noqa: E402
from corvin_compute.strategies.random import RandomStrategy  # noqa: E402


class GridStrategyTests(unittest.TestCase):
    def test_grid_enumerates_cartesian(self) -> None:
        """2×3×2 grid → 12 unique points in 4 batches of 3."""
        s = GridStrategy({"a": [1, 2], "b": [10, 20, 30], "c": ["x", "y"]})
        self.assertEqual(s.total_points, 12)
        all_points: list[dict] = []
        while True:
            batch = s.suggest_batch([], 3)
            if not batch:
                break
            all_points.extend(batch)
        self.assertEqual(len(all_points), 12)
        # Tuples for hashable de-dup check
        uniq = {tuple(sorted(p.items())) for p in all_points}
        self.assertEqual(len(uniq), 12, "grid produced duplicates")
        stop, reason = s.should_stop([])
        self.assertTrue(stop)
        self.assertEqual(reason, "grid-exhausted")

    def test_grid_batch_smaller_than_remaining(self) -> None:
        s = GridStrategy({"a": [1, 2, 3, 4]})
        # First call asks for 10 but grid has only 4.
        batch = s.suggest_batch([], 10)
        self.assertEqual(len(batch), 4)
        # Second call returns nothing.
        batch2 = s.suggest_batch([], 10)
        self.assertEqual(batch2, [])
        stop, reason = s.should_stop([])
        self.assertTrue(stop)
        self.assertEqual(reason, "grid-exhausted")

    def test_grid_accepts_values_dict_form(self) -> None:
        s = GridStrategy({"a": {"values": [1, 2, 3]}})
        batch = s.suggest_batch([], 10)
        self.assertEqual(len(batch), 3)

    def test_grid_rejects_empty_axis(self) -> None:
        with self.assertRaises(ValueError):
            GridStrategy({"a": []})

    def test_grid_rejects_no_axes(self) -> None:
        with self.assertRaises(ValueError):
            GridStrategy({})


class RandomStrategyTests(unittest.TestCase):
    def test_random_independent_samples_seeded(self) -> None:
        """Seeded RNG → reproducible across re-runs."""
        s1 = RandomStrategy(
            {"x": {"type": "int_uniform", "low": 0, "high": 100}}, seed=42,
        )
        s2 = RandomStrategy(
            {"x": {"type": "int_uniform", "low": 0, "high": 100}}, seed=42,
        )
        b1 = s1.suggest_batch([], 50)
        b2 = s2.suggest_batch([], 50)
        self.assertEqual(b1, b2)

    def test_random_respects_int_uniform(self) -> None:
        s = RandomStrategy(
            {"x": {"type": "int_uniform", "low": 5, "high": 200}}, seed=0,
        )
        batch = s.suggest_batch([], 1000)
        for p in batch:
            self.assertIsInstance(p["x"], int)
            self.assertGreaterEqual(p["x"], 5)
            self.assertLessEqual(p["x"], 200)

    def test_random_respects_float_log_uniform(self) -> None:
        s = RandomStrategy(
            {"lr": {"type": "float_log_uniform", "low": 1e-4, "high": 1e-1}},
            seed=0,
        )
        batch = s.suggest_batch([], 500)
        for p in batch:
            self.assertIsInstance(p["lr"], float)
            self.assertGreaterEqual(p["lr"], 1e-4 * 0.99)
            self.assertLessEqual(p["lr"], 1e-1 * 1.01)

    def test_random_respects_categorical(self) -> None:
        s = RandomStrategy(
            {"method": {"type": "categorical", "values": ["a", "b", "c"]}},
            seed=0,
        )
        batch = s.suggest_batch([], 100)
        values = {p["method"] for p in batch}
        self.assertLessEqual(values, {"a", "b", "c"})

    def test_random_no_intrinsic_stop(self) -> None:
        s = RandomStrategy({"x": [1, 2, 3]}, seed=0)
        for _ in range(20):
            stop, _ = s.should_stop([])
            self.assertFalse(stop)

    def test_random_plain_list_is_categorical(self) -> None:
        s = RandomStrategy({"x": ["a", "b"]}, seed=0)
        batch = s.suggest_batch([], 10)
        for p in batch:
            self.assertIn(p["x"], ("a", "b"))

    def test_random_range_is_int_uniform(self) -> None:
        s = RandomStrategy({"n": range(5, 15)}, seed=0)
        batch = s.suggest_batch([], 100)
        for p in batch:
            self.assertGreaterEqual(p["n"], 5)
            self.assertLessEqual(p["n"], 14)

    def test_log_uniform_rejects_zero_or_negative(self) -> None:
        s = RandomStrategy(
            {"x": {"type": "float_log_uniform", "low": 0, "high": 1}}, seed=0,
        )
        with self.assertRaises(ValueError):
            s.suggest_batch([], 1)


class ProtocolTests(unittest.TestCase):
    def test_grid_satisfies_protocol(self) -> None:
        s = GridStrategy({"a": [1, 2]})
        self.assertIsInstance(s, Strategy)

    def test_random_satisfies_protocol(self) -> None:
        s = RandomStrategy({"a": [1, 2]}, seed=0)
        self.assertIsInstance(s, Strategy)


class RegistryTests(unittest.TestCase):
    def test_load_grid(self) -> None:
        s = load_strategy("grid", {"a": [1, 2, 3]})
        self.assertEqual(s.name, "grid")

    def test_load_random(self) -> None:
        s = load_strategy("random", {"a": [1, 2, 3]}, seed=0)
        self.assertEqual(s.name, "random")

    def test_load_unknown_raises(self) -> None:
        with self.assertRaises(UnknownStrategy):
            load_strategy("nonsense_strategy", {"a": [1, 2]})

    def test_available_strategies_includes_basics(self) -> None:
        names = available_strategies()
        self.assertIn("grid", names)
        self.assertIn("random", names)


if __name__ == "__main__":
    unittest.main(verbosity=2)
