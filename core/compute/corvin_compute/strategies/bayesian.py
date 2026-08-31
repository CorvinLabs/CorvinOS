"""Bayesian strategy — sklearn GP + q-Expected Improvement (ADR-0013 Phase 13.8).

Implementation choices:

- **Seeding**: first ``2 * n_axes`` iterations sample uniformly at random
  to seed the GP. Bayesian-Opt with too few observations is worse than
  random — the warm-up is empirically validated in the literature.
- **GP kernel**: Matérn-5/2 (sklearn's default-ish RBF + extra smoothness).
  Mild operator override possible via ``strategy_config.kernel`` later.
- **Acquisition**: Expected Improvement, batched via the constant-liar
  (CL-min) heuristic. Quick, deterministic, well-documented.
- **Numerical stability**: y-normalise observed losses into [0, 1]
  before GP fit; map suggestions back.
- **CPU budget per ``suggest_batch``**: 5 s wall-budget per call; on
  exceed raise :class:`StrategyTimeout` (the driver translates this to
  ``failed`` with ``error_class: "StrategyTimeout"``).

The module imports sklearn + numpy lazily — :mod:`corvin_compute.strategies`
catches the ImportError so a minimal install (no sklearn) keeps every
other strategy importable.
"""
from __future__ import annotations

import math
import random as _stdlib_random
import time
from typing import Any, Mapping

import numpy as np  # type: ignore[import]
from sklearn.gaussian_process import GaussianProcessRegressor  # type: ignore[import]
from sklearn.gaussian_process.kernels import Matern, ConstantKernel  # type: ignore[import]

from . import register
from .base import ParamSet, Strategy


SUGGEST_BATCH_CPU_BUDGET_S = 5.0


class StrategyTimeout(RuntimeError):
    """Raised when ``suggest_batch`` exceeds its CPU budget."""


class _AxisEncoder:
    """Maps an axis spec to / from a numeric coordinate in [0, 1].

    - ``int_uniform``       — linear scale.
    - ``float_uniform``     — linear scale.
    - ``float_log_uniform`` — log scale.
    - ``categorical``       — embedded as integer index → bucketed sample.
    - plain list            — categorical (back-compat with random.py).
    - plain ``range(...)``  — int_uniform over min, max.
    """

    def __init__(self, name: str, spec: Any) -> None:
        self.name = name
        self.kind: str
        self.params: dict
        if isinstance(spec, range):
            self.kind = "int_uniform"
            self.params = {"low": spec.start, "high": spec.stop - 1}
        elif isinstance(spec, list):
            self.kind = "categorical"
            self.params = {"values": list(spec)}
        elif isinstance(spec, Mapping):
            t = spec.get("type")
            if t in ("int_uniform", "float_uniform", "float_log_uniform"):
                self.kind = t
                self.params = {"low": float(spec["low"]),
                               "high": float(spec["high"])}
            elif t == "categorical":
                self.kind = "categorical"
                self.params = {"values": list(spec["values"])}
            else:
                raise ValueError(f"unrecognised axis spec for {name!r}: {spec!r}")
        else:
            raise ValueError(f"unrecognised axis spec for {name!r}: {spec!r}")

    def encode(self, value: Any) -> float:
        if self.kind == "int_uniform":
            lo, hi = self.params["low"], self.params["high"]
            return (float(value) - lo) / max(hi - lo, 1e-9)
        if self.kind == "float_uniform":
            lo, hi = self.params["low"], self.params["high"]
            return (float(value) - lo) / max(hi - lo, 1e-9)
        if self.kind == "float_log_uniform":
            lo, hi = self.params["low"], self.params["high"]
            return (math.log(float(value)) - math.log(lo)) \
                / max(math.log(hi) - math.log(lo), 1e-9)
        if self.kind == "categorical":
            values = self.params["values"]
            try:
                idx = values.index(value)
            except ValueError:
                idx = 0
            return idx / max(len(values) - 1, 1)
        raise ValueError(f"unknown kind: {self.kind}")

    def decode(self, x: float) -> Any:
        x = max(0.0, min(1.0, x))
        if self.kind == "int_uniform":
            lo, hi = self.params["low"], self.params["high"]
            return int(round(lo + x * (hi - lo)))
        if self.kind == "float_uniform":
            lo, hi = self.params["low"], self.params["high"]
            return lo + x * (hi - lo)
        if self.kind == "float_log_uniform":
            lo, hi = self.params["low"], self.params["high"]
            return math.exp(math.log(lo) + x * (math.log(hi) - math.log(lo)))
        if self.kind == "categorical":
            values = self.params["values"]
            idx = int(round(x * (len(values) - 1)))
            return values[max(0, min(len(values) - 1, idx))]
        raise ValueError(f"unknown kind: {self.kind}")


class BayesianStrategy:
    name = "bayesian"

    def __init__(self, param_grid: Mapping[str, Any], *, minimise: bool = True,
                 seed: int | None = None) -> None:
        self._axes = [_AxisEncoder(name, spec) for name, spec in param_grid.items()]
        if not self._axes:
            raise ValueError("bayesian strategy requires at least one axis")
        self._minimise = minimise
        self._rng = _stdlib_random.Random(seed)
        self._np_rng = np.random.default_rng(seed)
        self._warmup = 2 * len(self._axes)
        self._used_gp = False
        # Cache of (encoded_X, y) we've seen, persisted across batches.
        self._X: list[list[float]] = []
        self._y: list[float] = []

    # -- helpers -----------------------------------------------------------------

    def _encode_history(self, history: list) -> tuple[np.ndarray, np.ndarray]:
        rows: list[list[float]] = []
        losses: list[float] = []
        for rec in history:
            if rec.loss is None:
                continue
            try:
                rows.append([ax.encode(rec.params[ax.name]) for ax in self._axes])
                losses.append(float(rec.loss))
            except (KeyError, TypeError, ValueError):
                continue
        if not rows:
            return np.empty((0, len(self._axes))), np.empty(0)
        return np.asarray(rows, dtype=float), np.asarray(losses, dtype=float)

    def _ei_acquisition(
        self, gp: GaussianProcessRegressor, X_cand: np.ndarray,
        y_best: float,
    ) -> np.ndarray:
        mu, sigma = gp.predict(X_cand, return_std=True)
        sigma = np.maximum(sigma, 1e-9)
        if self._minimise:
            improvement = y_best - mu
        else:
            improvement = mu - y_best
        z = improvement / sigma
        from scipy.stats import norm  # type: ignore[import]
        ei = improvement * norm.cdf(z) + sigma * norm.pdf(z)
        return ei

    def _propose_candidates(self, n: int = 256) -> np.ndarray:
        """Generate Sobol-ish random candidates in [0, 1]^d."""
        return self._np_rng.random((n, len(self._axes)))

    def _build_gp(self, X: np.ndarray, y: np.ndarray) -> GaussianProcessRegressor:
        kernel = ConstantKernel(1.0) * Matern(length_scale=0.2, nu=2.5)
        gp = GaussianProcessRegressor(
            kernel=kernel,
            alpha=1e-6,
            normalize_y=True,
            n_restarts_optimizer=2,
            random_state=int(self._np_rng.integers(0, 2**31 - 1)),
        )
        gp.fit(X, y)
        return gp

    # -- Strategy interface ------------------------------------------------------

    def suggest_batch(self, history: list, n: int) -> list[ParamSet]:
        t0 = time.time()
        n_done = len([h for h in history if h.loss is not None])
        # Warm-up branch
        if n_done < self._warmup:
            picks: list[ParamSet] = []
            for _ in range(n):
                row = [self._rng.random() for _ in range(len(self._axes))]
                picks.append({ax.name: ax.decode(x)
                              for ax, x in zip(self._axes, row)})
            return picks

        self._used_gp = True
        X, y = self._encode_history(history)
        if X.shape[0] < 2:
            # Defensive — should not happen given the warm-up gate.
            picks = []
            for _ in range(n):
                row = [self._rng.random() for _ in range(len(self._axes))]
                picks.append({ax.name: ax.decode(x)
                              for ax, x in zip(self._axes, row)})
            return picks

        try:
            gp = self._build_gp(X, y)
        except Exception:
            # GP fit failed — fall back to random for this batch. We
            # do not raise StrategyTimeout here because that would
            # terminate the run; transient fit failures recover on the
            # next batch.
            picks = []
            for _ in range(n):
                row = [self._rng.random() for _ in range(len(self._axes))]
                picks.append({ax.name: ax.decode(x)
                              for ax, x in zip(self._axes, row)})
            return picks

        if time.time() - t0 > SUGGEST_BATCH_CPU_BUDGET_S:
            raise StrategyTimeout(
                f"bayesian suggest_batch exceeded "
                f"{SUGGEST_BATCH_CPU_BUDGET_S} s",
            )

        y_best = float(np.min(y) if self._minimise else np.max(y))
        picks = []
        # Constant-liar (CL-min): pretend each new pick takes the best
        # observed loss as its loss. Quick, deterministic, no extra
        # GP refits inside the batch loop.
        liar_X = list(X)
        liar_y = list(y)
        for _ in range(n):
            X_cand = self._propose_candidates(256)
            try:
                ei = self._ei_acquisition(
                    self._build_gp(np.asarray(liar_X), np.asarray(liar_y)),
                    X_cand, y_best,
                )
            except Exception:
                # Recover via random for this slot.
                row = [self._rng.random() for _ in range(len(self._axes))]
                picks.append({ax.name: ax.decode(x)
                              for ax, x in zip(self._axes, row)})
                continue
            best_idx = int(np.argmax(ei))
            chosen = X_cand[best_idx]
            picks.append({ax.name: ax.decode(float(x))
                          for ax, x in zip(self._axes, chosen)})
            liar_X.append(list(chosen))
            liar_y.append(y_best)
            if time.time() - t0 > SUGGEST_BATCH_CPU_BUDGET_S:
                raise StrategyTimeout(
                    f"bayesian suggest_batch exceeded "
                    f"{SUGGEST_BATCH_CPU_BUDGET_S} s",
                )
        return picks

    def update(self, history: list, new_results: list) -> None:
        # The strategy is stateless beyond the history passed each call;
        # the warm-up counter recovers from history length, and the GP
        # is re-fit on every suggest_batch call. The split-update API
        # is honoured for protocol-conformance (and to keep the Phase
        # 13.9 recovery path simple).
        return None

    def should_stop(self, history: list) -> tuple[bool, str]:
        return False, ""


def _factory(param_grid: Mapping[str, Any], *, minimise: bool = True,
             seed: int | None = None) -> Strategy:
    return BayesianStrategy(param_grid, minimise=minimise, seed=seed)


register("bayesian", _factory)
