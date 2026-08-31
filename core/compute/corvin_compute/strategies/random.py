"""Random strategy — per-axis distribution sampler (ADR-0013 Phase 13.3).

Per-axis distribution types (matching the implementation-plan §13.3):

- ``int_uniform``        — ``{"type": "int_uniform", "low": int, "high": int}``
- ``float_uniform``      — ``{"type": "float_uniform", "low": float, "high": float}``
- ``float_log_uniform``  — log-uniform over [low, high]; both must be > 0.
- ``categorical``        — ``{"type": "categorical", "values": [...]}``
- plain list             — interpreted as ``categorical``.
- plain ``range(...)``   — interpreted as ``int_uniform`` over min/max-1.

Random has no intrinsic stop signal — ``should_stop`` always returns
``(False, "")``. The driver-level budget is the stop authority.
"""
from __future__ import annotations

import math
import random as _stdlib_random
from typing import Any, Mapping

from . import register
from .base import ParamSet, Strategy


class RandomStrategy:
    name = "random"

    def __init__(self, param_grid: Mapping[str, Any], *, minimise: bool = True,
                 seed: int | None = None) -> None:
        self._axes: list[tuple[str, Mapping[str, Any]]] = []
        for axis_name, spec in param_grid.items():
            self._axes.append((axis_name, self._normalise_spec(spec)))
        if not self._axes:
            raise ValueError("random strategy requires at least one axis")
        self._rng = _stdlib_random.Random(seed)

    @staticmethod
    def _normalise_spec(spec: Any) -> dict[str, Any]:
        if isinstance(spec, range):
            return {"type": "int_uniform", "low": spec.start,
                    "high": spec.stop - 1}
        if isinstance(spec, list):
            return {"type": "categorical", "values": spec}
        if isinstance(spec, Mapping):
            t = spec.get("type")
            if t in ("int_uniform", "float_uniform", "float_log_uniform",
                     "categorical"):
                return dict(spec)
        raise ValueError(f"random axis spec is not recognised: {spec!r}")

    def _sample_one(self, spec: Mapping[str, Any]) -> Any:
        t = spec["type"]
        if t == "int_uniform":
            return self._rng.randint(int(spec["low"]), int(spec["high"]))
        if t == "float_uniform":
            return self._rng.uniform(float(spec["low"]), float(spec["high"]))
        if t == "float_log_uniform":
            lo, hi = float(spec["low"]), float(spec["high"])
            if lo <= 0 or hi <= 0:
                raise ValueError("float_log_uniform requires low and high > 0")
            return math.exp(self._rng.uniform(math.log(lo), math.log(hi)))
        if t == "categorical":
            return self._rng.choice(list(spec["values"]))
        raise ValueError(f"unknown distribution type: {t!r}")

    def suggest_batch(self, history: list, n: int) -> list[ParamSet]:
        return [
            {name: self._sample_one(spec) for name, spec in self._axes}
            for _ in range(max(n, 0))
        ]

    def update(self, history: list, new_results: list) -> None:
        return None

    def should_stop(self, history: list) -> tuple[bool, str]:
        return False, ""


def _factory(param_grid: Mapping[str, Any], *, minimise: bool = True,
             seed: int | None = None) -> Strategy:
    return RandomStrategy(param_grid, minimise=minimise, seed=seed)


register("random", _factory)
