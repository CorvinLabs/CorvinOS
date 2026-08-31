"""Parallel driver — batched ThreadPoolExecutor (ADR-0013 Phase 13.7).

Wraps the sequential :class:`ComputeRun` to dispatch iteration batches
across N worker threads. Each batch comes from the strategy via
``suggest_batch(history, n)``. The bwrap subprocess (Forge runner) is
the per-iteration unit of parallelism — strategy state stays in the
driver thread.

The cache layer is the Forge cache. ``x-cache-key: true`` fields in
the tool's input schema decide which fields contribute to the cache
key (Phase 13.7's parametric-cache extension). When NO field has the
annotation, the full payload contributes — preserving back-compat.

This module is additive: tests can pin the sequential driver
(:class:`ComputeRun`) and the parallel driver (:class:`ParallelDriver`)
side-by-side; the worker (Phase 13.4) constructs whichever variant
the tenant config requests.
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from pathlib import Path
from typing import Any, Callable, Mapping

from .audit import redact_sensitive_fields
from .budget import (
    Budget, evaluate_termination,
    RUN_STATE_RUNNING, RUN_STATE_FAILED, RUN_STATE_ABORTED, TERMINAL_STATES,
)
from .driver import (
    AbortRequested, ComputeRun, ComputeRunSpec, RunnerFn, _resolve_loss,
)
from .iteration import IterRecord, best_iter, now_ts, param_fingerprint
from .state import RunRecord, RunStore, new_run_id, validate_run_id

log = logging.getLogger(__name__)


# Default per-iteration timeout. Operator-tunable via constructor or
# (Phase 13.6) tenant.compute.max_wall_clock_per_run_s — there we treat
# the run-level wall-clock cap as the upper bound; per-iter timeout
# stays smaller so a single hung iter doesn't burn the whole budget.
DEFAULT_ITER_TIMEOUT_S = 60.0


class IterationTimeout(RuntimeError):
    pass


class ParallelDriver(ComputeRun):
    """Batch-parallel iteration driver.

    Behaves identically to :class:`ComputeRun` when ``max_parallel == 1``;
    in that regime there is no thread overhead and tests can assert
    byte-identical outcomes.
    """

    def __init__(
        self,
        spec: ComputeRunSpec,
        *,
        corvin_home: Path,
        runner_fn: RunnerFn,
        strategy_factory: Callable[..., Any],
        max_parallel: int = 4,
        iter_timeout_s: float = DEFAULT_ITER_TIMEOUT_S,
        run_id: str | None = None,
        audit_emit: Callable[..., None] | None = None,
        time_fn: Callable[[], float] = time.time,
    ) -> None:
        super().__init__(
            spec, corvin_home=corvin_home, runner_fn=runner_fn,
            strategy_factory=strategy_factory, run_id=run_id,
            audit_emit=audit_emit, time_fn=time_fn,
        )
        self.max_parallel = max(1, int(max_parallel))
        self.iter_timeout_s = max(1.0, float(iter_timeout_s))

    def run(self) -> RunRecord:  # type: ignore[override]
        self._started_at = self.time_fn()
        self._write_initial_manifest()
        self._write_summary(state=RUN_STATE_RUNNING)
        self._emit_audit(
            "compute.run_started", tool_name=self.spec.tool_name,
            strategy=self.spec.strategy_name,
            budget=__import__("dataclasses").asdict(self.spec.budget),
        )

        history: list[IterRecord] = []
        iter_counter = 0

        try:
            while True:
                if self._abort:
                    raise AbortRequested()

                state, reason = evaluate_termination(
                    history, self.spec.budget,
                    started_at=self._started_at, minimise=self.spec.minimise,
                    strategy_stop=self._strategy_should_stop(history),
                    now_fn=self.time_fn,
                )
                if state in TERMINAL_STATES:
                    self._terminal_state, self._terminal_reason = state, reason
                    break

                remaining_budget = max(
                    1, self.spec.budget.max_iterations - len(history),
                )
                n = min(self.max_parallel, remaining_budget)
                batch = self.strategy.suggest_batch(history, n=n)
                if not batch:
                    self._terminal_state = "converged"
                    self._terminal_reason = "strategy-empty-batch"
                    break

                new_results = self._run_iteration_batch(
                    iter_counter, list(batch),
                )
                # Iter numbers are assigned by the batch helper in
                # batch-order; new_results is sorted by iter ascending.
                iter_counter += len(new_results)
                history.extend(new_results)
                self._update_rolling_summary(history)
                for rec in new_results:
                    self._emit_audit(
                        "compute.iteration_completed", iter=rec.iter,
                        loss=rec.loss, wall_ms=rec.wall_ms,
                        param_fingerprint=rec.param_fingerprint,
                        cache_hit=rec.cache_hit,
                        strategy=self.spec.strategy_name,
                    )
                if self._abort:
                    raise AbortRequested()

                try:
                    self.strategy.update(history, new_results)
                except Exception:  # noqa: BLE001
                    log.exception("strategy.update raised — terminating run")
                    self._terminal_state = RUN_STATE_FAILED
                    self._terminal_reason = "strategy-update-failed"
                    break
        except AbortRequested:
            self._terminal_state = RUN_STATE_ABORTED
            self._terminal_reason = "external-abort"
        except IterationTimeout as exc:
            self._terminal_state = RUN_STATE_FAILED
            self._terminal_reason = f"iteration-timeout:{exc}"
        except Exception as exc:  # noqa: BLE001
            log.exception("parallel driver loop crashed")
            self._terminal_state = RUN_STATE_FAILED
            self._terminal_reason = f"driver-exception:{type(exc).__name__}"

        return self._finalise(history)

    def _run_iteration_batch(
        self, base_iter: int, batch: list[Mapping[str, Any]],
    ) -> list[IterRecord]:
        """Submit ``batch`` to a ThreadPoolExecutor; gather in batch order."""
        if self.max_parallel <= 1 or len(batch) == 1:
            # Sequential path — keeps tests deterministic and avoids
            # the thread-overhead for trivially-parallel single-point
            # batches.
            return [
                self._run_one(base_iter + idx + 1, params)
                for idx, params in enumerate(batch)
            ]
        results: list[IterRecord | None] = [None] * len(batch)
        with ThreadPoolExecutor(max_workers=self.max_parallel) as pool:
            futures = {
                pool.submit(self._run_one, base_iter + idx + 1, params): idx
                for idx, params in enumerate(batch)
            }
            for fut in futures:
                idx = futures[fut]
                try:
                    results[idx] = fut.result(timeout=self.iter_timeout_s)
                except FuturesTimeout as exc:
                    raise IterationTimeout(
                        f"iter {base_iter + idx + 1} exceeded "
                        f"{self.iter_timeout_s}s",
                    ) from exc
        # type: ignore[return-value] — fill-in guaranteed by `for fut`
        return [r for r in results if r is not None]
