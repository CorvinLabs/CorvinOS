"""Strategy registry (ADR-0013 Phase 13.3).

Strategies implement the Protocol in :mod:`corvin_compute.strategies.base`
and are loaded by name via :func:`load_strategy`. Bundled strategies:

- ``grid``     — Cartesian product walker (Phase 13.3).
- ``random``   — Per-axis distribution sampler (Phase 13.3).
- ``bayesian`` — sklearn GP + q-EI (Phase 13.8).
"""
from __future__ import annotations

from typing import Any, Callable

from .base import Strategy


class UnknownStrategy(KeyError):
    """Raised when ``load_strategy(name)`` cannot resolve ``name``."""


# Registry maps name -> factory(param_grid, *, minimise, seed) -> Strategy.
_REGISTRY: dict[str, Callable[..., Strategy]] = {}


def register(name: str, factory: Callable[..., Strategy]) -> None:
    _REGISTRY[name] = factory


def load_strategy(name: str, param_grid: dict, *, minimise: bool = True,
                  seed: int | None = None) -> Strategy:
    if name not in _REGISTRY:
        raise UnknownStrategy(f"unknown strategy: {name!r}")
    return _REGISTRY[name](param_grid, minimise=minimise, seed=seed)


def available_strategies() -> list[str]:
    return sorted(_REGISTRY)


# ------------------------------------------------------------------
# Eager-load the two non-LLM bundled strategies. Bayesian (sklearn-
# backed) auto-registers on import if the SDK is present; on a minimal
# install the import fails silently and the strategy is unavailable.
# ------------------------------------------------------------------

from . import grid as _grid  # noqa: E402,F401
from . import random as _random  # noqa: E402,F401

try:  # Bayesian is optional; available iff sklearn + numpy are installed.
    from . import bayesian as _bayesian  # noqa: E402,F401
except ImportError:
    pass
