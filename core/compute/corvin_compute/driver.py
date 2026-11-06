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
import math
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Mapping

from .audit import redact_sensitive_fields
from .budget import (
    Budget, evaluate_termination,
    RUN_STATE_RUNNING, RUN_STATE_FAILED, RUN_STATE_ABORTED, RUN_STATE_INTERRUPTED,
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


class InterruptRequested(Exception):
    """Raised inside the loop when the worker shuts down: the run stops WITHOUT a verdict."""


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
    if isinstance(cur, bool):
        return None
    try:
        val = float(cur)
    except (TypeError, ValueError):
        return None
    # NaN / +-inf are not a loss: NaN compared false against everything and
    # survived the "no valid loss" check as best_loss=nan; -inf won every
    # minimisation (adversarial review 2026-11-06).
    return val if math.isfinite(val) else None


#: Consecutive failing iterations FROM THE START, all with the same error,
#: after which a run stops instead of spending the rest of its budget. A tool
#: that is missing, or a param_grid that does not match the tool's schema,
#: fails every point identically — iterating on is pure waste.
FAIL_FAST_IDENTICAL_ERRORS = 3


#: Failure classes that do not depend on the parameter values: a missing tool
#: or a missing loss field fails every point of the space the same way. A
#: value-dependent error (one bad value of a later grid axis) must NOT stop the
#: run — the grid enumerates the first axis fastest, so its first points can
#: share one bad value of another axis while the rest of the grid is valid.
_PARAM_INDEPENDENT_ERRORS = ("LossMetricMissing:", "ToolError: unknown tool",
                             "PermissionError: datasource ")


def _deterministic_failure(history: "list[IterRecord]") -> str | None:
    """The shared error when the run has only ever failed, identically, N times,
    with a failure class that no other parameter value can avoid."""
    if len(history) < FAIL_FAST_IDENTICAL_ERRORS:
        return None
    if any(h.loss is not None for h in history):
        return None
    errors = {h.error for h in history}
    if len(errors) != 1 or None in errors:
        return None
    err = next(iter(errors))
    return err if err.startswith(_PARAM_INDEPENDENT_ERRORS) else None


def _public_error(err: str | None) -> str:
    """The cause as it may travel to the caller (an LLM chat): never raw tool
    output. Tool stderr and jsonschema messages can quote row values or param
    values; the full text stays in the run's iterations/*.json."""
    if not err:
        return "no-valid-loss"
    if err.startswith(_PARAM_INDEPENDENT_ERRORS):
        return err  # composed by this module: names keys / the tool, never values
    cls = err.split(":", 1)[0].strip()[:60] or "Error"
    return f"{cls} (details in iterations/*.json of this run)"


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
        self._interrupt = False
        self._started_at: float | None = None
        self._terminal_state: str | None = None
        self._terminal_reason: str = ""

    # -- public API --------------------------------------------------------------

    def request_abort(self) -> None:
        """Mark this run for graceful termination on the next loop turn."""
        self._abort = True

    def request_interrupt(self) -> None:
        """Stop this run on the next loop turn WITHOUT a verdict (worker shutdown).

        An abort is final (``aborted``, terminal). A shutdown used to be one: a normal
        service restart marked every in-flight run ``aborted`` and nothing ever resumed
        it. An interrupted run stays ``running`` on disk, so the next worker start picks
        it up after its last recorded iteration (``recovery.resume_run``)."""
        self._interrupt = True

    def run(self, resume_history: "list[IterRecord] | None" = None,
            prior_wall_s: float = 0.0) -> RunRecord:
        """Block until terminal and return the final RunRecord.

        ``resume_history`` continues a run interrupted by a restart: its recorded
        iterations seed the history and the iteration counter (so nothing already
        on disk is overwritten and ``max_iterations`` counts them), the strategy
        is fast-forwarded past them, and the manifest is kept as accepted.
        ``prior_wall_s`` makes ``max_wall_clock_s`` cumulative across the restart.
        """
        resuming = resume_history is not None   # [] = a resumed run with nothing recorded yet
        self._started_at = self.time_fn() - max(0.0, float(prior_wall_s or 0.0))
        if not resuming:
            self._write_initial_manifest()
        self._write_summary(state=RUN_STATE_RUNNING)
        if not resuming:
            self._emit_audit("compute.run_started", tool_name=self.spec.tool_name,
                             strategy=self.spec.strategy_name,
                             budget=dataclasses.asdict(self.spec.budget),
                             # ADR-0127 review gap G2: record datasource binding
                             # (connection NAMES are operator handles, not PII).
                             datasources=list(getattr(self.spec, "datasources", []) or []))

        history: list[IterRecord] = list(resume_history or [])
        iter_counter = max((h.iter for h in history), default=0)
        if history:
            try:
                resume = getattr(self.strategy, "resume", None)
                if callable(resume):
                    resume(history)
                else:
                    self.strategy.update(history, history)
            except Exception:  # noqa: BLE001
                log.exception("strategy resume failed")
                self._terminal_state = RUN_STATE_FAILED
                self._terminal_reason = "strategy-resume-failed"
                return self._finalise(history)

        try:
            while True:
                if self._abort:
                    raise AbortRequested()
                if self._interrupt:
                    raise InterruptRequested()

                state, reason = evaluate_termination(
                    history, self.spec.budget,
                    started_at=self._started_at, minimise=self.spec.minimise,
                    strategy_stop=self._strategy_should_stop(history),
                    now_fn=self.time_fn,
                    early_stop=self.spec.strategy_name != "grid",
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
                    if self._interrupt:
                        raise InterruptRequested()

                failed_start = _deterministic_failure(history)
                if failed_start is not None:
                    self._terminal_state = RUN_STATE_FAILED
                    self._terminal_reason = f"all-iterations-failed: {_public_error(failed_start)}"[:240]
                    break

                try:
                    self.strategy.update(history, new_results)
                except Exception:  # noqa: BLE001
                    log.exception("strategy.update raised — terminating run")
                    self._terminal_state = RUN_STATE_FAILED
                    self._terminal_reason = "strategy-update-failed"
                    break
        except InterruptRequested:
            self._terminal_state = RUN_STATE_INTERRUPTED
            self._terminal_reason = "worker-shutdown"
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
                iter=iter_n,
                params=redact_sensitive_fields(params, self.spec.sensitive_fields),
                loss=None,
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
        # A tool that ran but whose output carries no numeric value at
        # ``loss_metric`` used to be recorded with loss=None and NO error: the
        # run then ended "budget_exhausted" with best_loss null and nothing on
        # disk said why. Name the cause on the record itself.
        error = None
        if loss is None:
            keys = sorted(envelope.keys())[:12] if isinstance(envelope, Mapping) else []
            error = (f"LossMetricMissing: no numeric value at {self.spec.loss_metric!r} "
                     f"in the tool output (top-level keys: {keys})")[:200]
        rec = IterRecord(
            iter=iter_n, params=params_for_disk, loss=loss,
            wall_ms=wall_ms, ts=now_ts(),
            cache_hit=cache_hit, param_fingerprint=fingerprint,
            error=error,
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
            # Needed to rebuild the spec after a restart: a resumed run used to
            # lose its datasource binding and ran its tool without it.
            "datasources":    list(getattr(self.spec, "datasources", []) or []),
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
            total_wall_s=max(0.0, self.time_fn() - (self._started_at or self.time_fn())),
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
        if self._terminal_state == RUN_STATE_INTERRUPTED:
            # No verdict, no terminal summary, no terminal audit record: the run stays
            # "running" on disk (the rolling summary already holds its progress) and
            # the next worker start resumes it.
            valid = [h for h in history if h.loss is not None]
            b = best_iter(valid, minimise=self.spec.minimise)
            last = history[-1] if history else None
            summary = self.store.read_summary(self.run_id)
            return RunRecord(
                run_id=self.run_id, tenant_id=self.spec.tenant_id, tool_name=self.spec.tool_name,
                strategy=self.spec.strategy_name, state=RUN_STATE_INTERRUPTED,
                best_loss=(b.loss if b else None), best_iter=(b.iter if b else None),
                total_iterations=len(history),
                total_wall_s=max(0.0, self.time_fn() - (self._started_at or self.time_fn())),
                accepted_at=self._started_at or 0.0, started_at=self._started_at,
                last_iteration_at=(last.ts if last else None),
                convergence_reason="worker-shutdown", error=None,
                manifest=self.store.read_manifest(self.run_id), summary=summary,
            )
        state = self._terminal_state or RUN_STATE_FAILED
        reason = self._terminal_reason or "unknown"
        valid = [h for h in history if h.loss is not None]
        # A run whose every iteration failed used to keep the termination
        # verdict ("budget_exhausted" / "converged") with best_loss null —
        # indistinguishable from a finished sweep for every caller that reads
        # ``state``. No valid loss means the run produced nothing: FAILED, with
        # the first iteration's error as the reason.
        if history and not valid and state not in (RUN_STATE_FAILED, RUN_STATE_ABORTED):
            first = next((h.error for h in history if h.error), None)
            state = RUN_STATE_FAILED
            reason = f"all-iterations-failed: {_public_error(first)}"[:240]
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
            error=(reason if state == RUN_STATE_FAILED else None),
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
            error=(reason if state == RUN_STATE_FAILED else None),
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
