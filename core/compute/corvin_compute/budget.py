"""Budget validation + deterministic termination check (ADR-0013 Phase 13.2).

The ``Budget`` dataclass mirrors the ADR §C surface; the
:func:`evaluate_termination` function is the deterministic stop-criteria
oracle used by the driver loop. No LLM in here.
"""
from __future__ import annotations

import dataclasses
import time
from typing import Iterable

from .iteration import IterRecord, best_iter


# Defaults match the ADR §C surface and the implementation-plan
# clamps; the tenant-config layer (Phase 13.6) tightens them per-tenant.
DEFAULT_MAX_ITERATIONS = 100
DEFAULT_MAX_WALL_CLOCK_S = 600
DEFAULT_CONVERGENCE_EPS = 1e-3
DEFAULT_STALL_AFTER_N = 10


class BudgetError(ValueError):
    """Raised on budget validation failures."""


@dataclasses.dataclass(frozen=True)
class Budget:
    max_iterations: int = DEFAULT_MAX_ITERATIONS
    max_wall_clock_s: int = DEFAULT_MAX_WALL_CLOCK_S
    convergence_eps: float = DEFAULT_CONVERGENCE_EPS
    stall_after_n: int = DEFAULT_STALL_AFTER_N

    @classmethod
    def from_dict(cls, data: dict | None) -> "Budget":
        if data is None:
            return cls()
        if not isinstance(data, dict):
            raise BudgetError(f"budget must be a dict, got {type(data).__name__}")
        kwargs = {}
        for field in dataclasses.fields(cls):
            if field.name in data:
                kwargs[field.name] = data[field.name]
        b = cls(**kwargs)
        b.validate()
        return b

    def validate(self) -> None:
        if self.max_iterations < 1:
            raise BudgetError("max_iterations must be >= 1")
        if self.max_wall_clock_s < 1:
            raise BudgetError("max_wall_clock_s must be >= 1")
        if self.convergence_eps < 0:
            raise BudgetError("convergence_eps must be >= 0")
        if self.stall_after_n < 1:
            raise BudgetError("stall_after_n must be >= 1")


# Terminal states recognised by :func:`evaluate_termination`.
RUN_STATE_RUNNING = "running"
RUN_STATE_CONVERGED = "converged"
RUN_STATE_STALLED = "stalled"
RUN_STATE_BUDGET_EXHAUSTED = "budget_exhausted"
RUN_STATE_FAILED = "failed"
RUN_STATE_ABORTED = "aborted"
RUN_STATE_QUEUED = "queued"

TERMINAL_STATES = frozenset({
    RUN_STATE_CONVERGED, RUN_STATE_STALLED, RUN_STATE_BUDGET_EXHAUSTED,
    RUN_STATE_FAILED, RUN_STATE_ABORTED,
})


def evaluate_termination(
    history: Iterable[IterRecord],
    budget: Budget,
    *,
    started_at: float,
    minimise: bool = True,
    strategy_stop: tuple[bool, str] = (False, ""),
    now_fn=time.time,
) -> tuple[str, str]:
    """Return ``(state, reason)``.

    ``state`` is one of ``"running"`` (continue) or a terminal state. The
    reason is empty when running and a short string for terminal states.
    """
    history_list = list(history)

    if strategy_stop[0]:
        return (RUN_STATE_CONVERGED if "exhaust" not in strategy_stop[1].lower()
                else RUN_STATE_BUDGET_EXHAUSTED, strategy_stop[1])

    if len(history_list) >= budget.max_iterations:
        return RUN_STATE_BUDGET_EXHAUSTED, "max-iterations-reached"

    wall = now_fn() - started_at
    if wall >= budget.max_wall_clock_s:
        return RUN_STATE_BUDGET_EXHAUSTED, "max-wall-clock-reached"

    # No completed iters yet → keep running.
    valid = [h for h in history_list if h.loss is not None]
    if not valid:
        return RUN_STATE_RUNNING, ""

    best = best_iter(valid, minimise=minimise)
    if best is None:
        return RUN_STATE_RUNNING, ""

    # Convergence: last (stall_after_n // 2) iters all within eps of best.
    window = max(budget.stall_after_n // 2, 1)
    recent = valid[-window:]
    if len(recent) >= window:
        if all(abs(r.loss - best.loss) <= budget.convergence_eps for r in recent):
            return RUN_STATE_CONVERGED, "eps-threshold-reached"

    # Stall: no improvement in stall_after_n iters.
    if len(valid) >= budget.stall_after_n + 1:
        recent_n = valid[-(budget.stall_after_n + 1):]
        # Strictly improving = loss strictly < best-so-far-before-window?
        best_before = best_iter(valid[: -budget.stall_after_n], minimise=minimise)
        if best_before is not None:
            cmp = (lambda a, b: a < b - budget.convergence_eps) if minimise \
                else (lambda a, b: a > b + budget.convergence_eps)
            improved = any(cmp(r.loss, best_before.loss) for r in recent_n[1:])
            if not improved:
                return RUN_STATE_STALLED, f"stalled-after-{budget.stall_after_n}"

    return RUN_STATE_RUNNING, ""
