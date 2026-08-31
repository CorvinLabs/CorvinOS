"""Grid strategy — Cartesian product walker (ADR-0013 Phase 13.3).

Lazy enumeration: state is the next index into the conceptual Cartesian
product. Memory complexity O(n_axes), not O(product). 10⁹-cell grids
don't pre-enumerate into RAM (cf. ADR §Risks).
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from . import register
from .base import ParamSet, Strategy


def _resolve_axis_values(spec: Any) -> list[Any]:
    """Accept either a plain list or ``{"type": "values", "values": [...]}``."""
    if isinstance(spec, list):
        return list(spec)
    if isinstance(spec, Mapping):
        if "values" in spec:
            return list(spec["values"])
    raise ValueError(f"grid axis spec must be a list or {{'values': [...]}}, got {spec!r}")


class GridStrategy:
    name = "grid"

    def __init__(self, param_grid: Mapping[str, Any], *, minimise: bool = True,
                 seed: int | None = None) -> None:
        self._axes: list[tuple[str, list[Any]]] = [
            (name, _resolve_axis_values(spec)) for name, spec in param_grid.items()
        ]
        if not self._axes:
            raise ValueError("grid strategy requires at least one axis")
        for axis_name, values in self._axes:
            if not values:
                raise ValueError(f"grid axis {axis_name!r} has no values")
        sizes = [len(v) for _, v in self._axes]
        self._total = 1
        for s in sizes:
            self._total *= s
        self._sizes = sizes
        self._next_index = 0

    def _point_at(self, idx: int) -> ParamSet:
        out: dict[str, Any] = {}
        for (name, values), size in zip(self._axes, self._sizes):
            out[name] = values[idx % size]
            idx //= size
        return out

    def suggest_batch(self, history: list, n: int) -> list[ParamSet]:
        end = min(self._next_index + n, self._total)
        batch = [self._point_at(i) for i in range(self._next_index, end)]
        self._next_index = end
        return batch

    def update(self, history: list, new_results: list) -> None:
        # Grid is stateless; the index is the only state.
        return None

    def should_stop(self, history: list) -> tuple[bool, str]:
        if self._next_index >= self._total:
            return True, "grid-exhausted"
        return False, ""

    @property
    def total_points(self) -> int:
        return self._total


def _factory(param_grid: Mapping[str, Any], *, minimise: bool = True,
             seed: int | None = None) -> Strategy:
    return GridStrategy(param_grid, minimise=minimise, seed=seed)


register("grid", _factory)
