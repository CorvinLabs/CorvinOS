"""ComputeRun driver core (ADR-0013 Phase 13.2).

Sequential single-threaded loop driver. Phase 13.7 introduces a
:class:`ParallelDriver` subclass that batches via ThreadPoolExecutor.
Phase 13.2 ships the sequential reference so unit tests can pin the
state-machine + stop-criteria without thread interleavings.

The driver is process-local — the worker daemon (Phase 13.4) wraps it
in an asyncio task per submitted run.
"""
from __future__ import annotations

import dataclasses
import logging
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Mapping

from .audit import redact_sensitive_fields
from .budget import (
    Budget, evaluate_termination,
    RUN_STATE_RUNNING, RUN_STATE_FAILED, RUN_STATE_ABORTED,
    RUN_STATE_QUEUED, TERMINAL_STATES,
)
from .iteration import IterRecord, best_iter, now_ts, param_fingerprint
from .state import RunRecord, RunStore, new_run_id, validate_run_id


log = logging.getLogger(__name__)


# A runner_fn matches Forge's runner.run_tool() signature minimally:
#   runner_fn(tool_name: str, payload: dict) -> dict
# Phase 13.2 tests inject a stub; Phase 13.4's worker injects the real
# Forge runner via dependency injection.
RunnerFn = Callable[[str, Mapping[str, Any]], Mapping[str, Any]]


class AbortRequested(Exception):
    """Raised inside the loop when a compute_abort fires."""


def _resolve_loss(envelope: Mapping[str, Any], loss_metric: str) -> float | None:
    """Pull ``envelope.<dotted-path>`` out by walking the dict.

    ``loss_metric`` is a dotted path like ``"result.sharpe"`` or
    ``"meta.loss"``. Missing path / non-numeric value → returns None
    (the iteration is recorded with loss=None and contributes to the
    failure counter but not the convergence check).
    """
    if not loss_metric:
        return None
    cur: Any = envelope
    for part in loss_metric.split("."):
        if not isinstance(cur, Mapping):
            return None
        if part not in cur:
            return None
        cur = cur[part]
    try:
        return float(cur)
    except (TypeError, ValueError):
        return None


@dataclasses.dataclass
class ComputeRunSpec:
    """Inputs for a single compute run.

    Mirrors the MCP ``compute_run`` surface from ADR §C.
    """

    tenant_id: str
    tool_name: str
    param_grid: dict
    loss_metric: str
    strategy_name: str
    budget: Budget
    minimise: bool = True
    data_handle: str | None = None
    seed: int | None = None
    top_k_size: int = 5
    sensitive_fields: list[str] = dataclasses.field(default_factory=list)
    # ADR-0127 — DSI v1 connection names exposed to the tool this run. The
    # runner resolves each name to its connection manifest and injects the
    # connection env (+ vault secrets) into the sandboxed tool. Empty ⇒ no
    # datasource binding (tool runs network-denied, the strict default).
    datasources: list[str] = dataclasses.field(default_factory=list)


class ComputeRun:
    """Sequential driver — one iteration at a time.

    Public entry point is :meth:`run` which blocks until terminal and
    returns the final :class:`RunRecord`. Tests inject ``runner_fn``;
    the worker injects the real Forge runner.

    The class is intentionally re-entrant-safe across threads via the
    per-run lock in :mod:`state` — a parallel subclass can call
    ``_run_one`` from multiple threads.
    """

    def __init__(
        self,
        spec: ComputeRunSpec,
        *,
        corvin_home: Path,
        runner_fn: RunnerFn,
        strategy_factory: Callable[[str, dict, bool, int | None], Any],
        run_id: str | None = None,
        audit_emit: Callable[..., None] | None = None,
        time_fn: Callable[[], float] = time.time,
    ) -> None:
        self.spec = spec
        self.corvin_home = Path(corvin_home)
        self.runner_fn = runner_fn
        self.strategy_factory = strategy_factory
        self.run_id = run_id or new_run_id()
        validate_run_id(self.run_id)
        self.audit_emit = audit_emit or (lambda *a, **kw: None)
        self.time_fn = time_fn

        self.store = RunStore(self.corvin_home, spec.tenant_id)
        self.strategy = strategy_factory(
            spec.strategy_name, spec.param_grid,
            minimise=spec.minimise, seed=spec.seed,
        )

        self._abort = False
        self._started_at: float | None = None
        self._terminal_state: str | None = None
        self._terminal_reason: str = ""

    # -- public API --------------------------------------------------------------

    def request_abort(self) -> None:
        """Mark this run for graceful termination on the next loop turn."""
        self._abort = True

    def run(self) -> RunRecord:
        """Block until terminal and return the final RunRecord."""
        self._started_at = self.time_fn()
        self._write_initial_manifest()
        self._write_summary(state=RUN_STATE_RUNNING)
        self._emit_audit("compute.run_started", tool_name=self.spec.tool_name,
                         strategy=self.spec.strategy_name,
                         budget=dataclasses.asdict(self.spec.budget),
                         # ADR-0127 review gap G2: record datasource binding
                         # (connection NAMES are operator handles, not PII).
                         datasources=list(getattr(self.spec, "datasources", []) or []))

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

                batch = self.strategy.suggest_batch(history, n=1)
                if not batch:
                    self._terminal_state = "converged"
                    self._terminal_reason = "strategy-empty-batch"
                    break

                new_results: list[IterRecord] = []
                for params in batch:
                    iter_counter += 1
                    rec = self._run_one(iter_counter, params)
                    history.append(rec)
                    new_results.append(rec)
                    self._update_rolling_summary(history)
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
        except Exception as exc:  # noqa: BLE001
            log.exception("driver loop crashed")
            self._terminal_state = RUN_STATE_FAILED
            self._terminal_reason = f"driver-exception:{type(exc).__name__}"

        return self._finalise(history)

    # -- internals ---------------------------------------------------------------

    def _strategy_should_stop(self, history: list[IterRecord]) -> tuple[bool, str]:
        try:
            return self.strategy.should_stop(history)
        except Exception:  # noqa: BLE001
            log.exception("strategy.should_stop raised")
            return False, ""

    def _runner_takes_datasources(self) -> bool:
        """Whether ``runner_fn`` accepts a ``datasources`` kwarg — decided by
        signature inspection, NOT by catching a call-time TypeError (which
        would silently double-invoke a tool with side effects and mask real
        TypeErrors from inside the runner). Minimal 2-arg test stubs report
        False and take the plain call path."""
        try:
            import inspect
            sig = inspect.signature(self.runner_fn)
        except (TypeError, ValueError):
            return False
        params = sig.parameters.values()
        return ("datasources" in sig.parameters
                or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params))

    def _invoke_runner(self, params: dict) -> Mapping[str, Any]:
        """Call the injected runner, passing datasource bindings when the
        runner's signature accepts them. Any exception propagates to
        ``_run_one``'s handler — no silent retry."""
        datasources = list(getattr(self.spec, "datasources", []) or [])
        if datasources and self._runner_takes_datasources():
            return self.runner_fn(self.spec.tool_name, params,
                                  datasources=datasources)
        return self.runner_fn(self.spec.tool_name, params)

    def _run_one(self, iter_n: int, params: Mapping[str, Any]) -> IterRecord:
        fingerprint = param_fingerprint(params)
        started = self.time_fn()
        try:
            envelope = self._invoke_runner(dict(params))
        except Exception as exc:  # noqa: BLE001
            wall_ms = int((self.time_fn() - started) * 1000)
            rec = IterRecord(
                iter=iter_n, params=dict(params), loss=None,
                wall_ms=wall_ms, ts=now_ts(),
                cache_hit=False, param_fingerprint=fingerprint,
                error=f"{type(exc).__name__}: {exc}"[:200],
            )
            self.store.append_iteration(self.run_id, rec)
            return rec

        wall_ms = int((self.time_fn() - started) * 1000)
        loss = _resolve_loss(envelope, self.spec.loss_metric)
        cache_hit = bool(envelope.get("meta", {}).get("cache_hit")) \
            if isinstance(envelope, Mapping) else False
        # Tier-3 sensitivity (ADR §F): per-field x-sensitive replaces
        # the value with a 12-char SHA-256 prefix BEFORE the iter file
        # hits disk. Non-sensitive fields stay clear so the operator
        # can reconstruct runs from the artifact tree.
        params_for_disk = redact_sensitive_fields(
            params, self.spec.sensitive_fields,
        )
        rec = IterRecord(
            iter=iter_n, params=params_for_disk, loss=loss,
            wall_ms=wall_ms, ts=now_ts(),
            cache_hit=cache_hit, param_fingerprint=fingerprint,
        )
        self.store.append_iteration(self.run_id, rec)
        return rec

    def _write_initial_manifest(self) -> None:
        manifest = {
            "run_id":         self.run_id,
            "tenant_id":      self.spec.tenant_id,
            "tool_name":      self.spec.tool_name,
            "strategy":       self.spec.strategy_name,
            "param_grid":     self.spec.param_grid,
            "loss_metric":    self.spec.loss_metric,
            "budget":         dataclasses.asdict(self.spec.budget),
            "minimise":       self.spec.minimise,
            "data_handle":    self.spec.data_handle,
            "seed":           self.spec.seed,
            "top_k_size":     self.spec.top_k_size,
            "sensitive_fields": list(self.spec.sensitive_fields),
            "accepted_at":    self._started_at,
        }
        self.store.write_manifest(self.run_id, manifest)

    def _update_rolling_summary(self, history: list[IterRecord]) -> None:
        valid = [h for h in history if h.loss is not None]
        b = best_iter(valid, minimise=self.spec.minimise)
        last = history[-1] if history else None
        top_k = self._top_k(history)
        self._write_summary(
            state=RUN_STATE_RUNNING,
            best_iter=(b.iter if b else None),
            best_loss=(b.loss if b else None),
            total_iterations=len(history),
            last_iteration_at=(last.ts if last else None),
            top_k=top_k,
        )

    def _top_k(self, history: list[IterRecord]) -> list[dict]:
        valid = [h for h in history if h.loss is not None]
        reverse = not self.spec.minimise
        valid.sort(key=lambda r: r.loss, reverse=reverse)
        return [
            {"iter": r.iter, "loss": r.loss, "param_fingerprint": r.param_fingerprint}
            for r in valid[: self.spec.top_k_size]
        ]

    def _write_summary(self, *, state: str, **fields: Any) -> None:
        existing: dict[str, Any] = {}
        try:
            existing = self.store.read_summary(self.run_id)
        except (OSError, FileNotFoundError):
            pass
        existing.update({
            "state": state,
            "tenant_id": self.spec.tenant_id,
            "started_at": self._started_at,
        })
        existing.update(fields)
        self.store.write_summary(self.run_id, existing)

    def _finalise(self, history: list[IterRecord]) -> RunRecord:
        state = self._terminal_state or RUN_STATE_FAILED
        reason = self._terminal_reason or "unknown"
        valid = [h for h in history if h.loss is not None]
        b = best_iter(valid, minimise=self.spec.minimise)
        last = history[-1] if history else None
        total_wall_s = max(0.0, self.time_fn() - (self._started_at or self.time_fn()))

        self._write_summary(
            state=state,
            best_iter=(b.iter if b else None),
            best_loss=(b.loss if b else None),
            total_iterations=len(history),
            total_wall_s=total_wall_s,
            last_iteration_at=(last.ts if last else None),
            convergence_reason=reason,
            top_k=self._top_k(history),
        )
        self._emit_audit(
            "compute.run_terminal", state=state,
            total_iterations=len(history), total_wall_s=total_wall_s,
            best_loss=(b.loss if b else None),
            convergence_reason=reason,
        )

        manifest = self.store.read_manifest(self.run_id)
        summary = self.store.read_summary(self.run_id)
        return RunRecord(
            run_id=self.run_id,
            tenant_id=self.spec.tenant_id,
            tool_name=self.spec.tool_name,
            strategy=self.spec.strategy_name,
            state=state,
            best_loss=(b.loss if b else None),
            best_iter=(b.iter if b else None),
            total_iterations=len(history),
            total_wall_s=total_wall_s,
            accepted_at=self._started_at or 0.0,
            started_at=self._started_at,
            last_iteration_at=(last.ts if last else None),
            convergence_reason=reason,
            error=None,
            manifest=manifest,
            summary=summary,
        )

    def _emit_audit(self, event: str, **details: Any) -> None:
        try:
            self.audit_emit(
                event,
                run_id=self.run_id,
                tenant_id=self.spec.tenant_id,
                **details,
            )
        except Exception:  # noqa: BLE001
            log.exception("audit emit failed for %s", event)
