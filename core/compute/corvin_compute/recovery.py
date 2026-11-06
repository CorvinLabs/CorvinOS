"""Crash recovery (ADR-0013 Phase 13.9).

On worker startup, scan the tenant's compute/runs/ tree and resume any
runs whose ``summary.json::state`` is non-terminal. The driver's
on-disk state is the source of truth: iteration files are append-only,
so re-running ``strategy.update(history, history)`` is a defensible
no-op for the bundled strategies (grid is stateless; random is
stateless; Bayesian re-fits the GP from history on every batch).

A run whose strategy is no longer installed (e.g. operator removed a
forged custom strategy) → ``state="failed"``, ``error_class="RecoveryFailed"``.
"""
from __future__ import annotations

import dataclasses
import logging
import time
from pathlib import Path
from typing import Any, Callable

from .audit import emit as _audit_emit
from .budget import (
    Budget, RUN_STATE_FAILED, RUN_STATE_QUEUED, RUN_STATE_RUNNING,
    TERMINAL_STATES,
)
from .driver import ComputeRun, ComputeRunSpec, RunnerFn
from . import strategies as strat_pkg
from .state import RunStore

log = logging.getLogger(__name__)


def scan_resumable(corvin_home: Path, tenant_id: str) -> list[str]:
    """Return run_ids that left disk in a non-terminal state."""
    store = RunStore(corvin_home, tenant_id)
    out: list[str] = []
    for run_id in store.list_runs():
        try:
            summary = store.read_summary(run_id)
        except (OSError, FileNotFoundError):
            continue
        if summary.get("state") not in TERMINAL_STATES:
            out.append(run_id)
    return out


#: A run that keeps being resumed WITHOUT making progress (it crashes the worker, or its
#: first iteration never finishes) is failed after this many consecutive attempts instead of
#: looping forever under ``Restart=on-failure`` (adversarial review 2026-11-06).
MAX_RESUME_ATTEMPTS_WITHOUT_PROGRESS = 3


@dataclasses.dataclass
class ResumePlan:
    """Everything needed to continue an interrupted run: build once, run anywhere."""
    run: ComputeRun
    history: list
    prior_wall_s: float

    def execute(self):
        return self.run.run(resume_history=self.history, prior_wall_s=self.prior_wall_s)


def prepare_resume(
    corvin_home: Path,
    tenant_id: str,
    run_id: str,
    runner_fn: RunnerFn,
    *,
    audit_path: Path | None = None,
    audit_emit_fn: Callable[..., Any] | None = None,
    run_audit_emit: Callable[..., Any] | None = None,
) -> "ResumePlan | str":
    """Rebuild a ComputeRun from disk, ready to CONTINUE after its last recorded iteration.

    Returns a :class:`ResumePlan` — or a terminal state string (``"failed"``) when the run
    cannot be resumed (missing manifest, strategy no longer installed, too many attempts
    without progress). The recorded iterations seed the run (they are neither re-run nor
    overwritten and count against ``max_iterations``); ``max_wall_clock_s`` is cumulative.
    ``run_audit_emit`` is the worker's emitter for the run's own ``compute.*`` events.
    """
    store = RunStore(corvin_home, tenant_id)
    try:
        manifest = store.read_manifest(run_id)
    except (OSError, FileNotFoundError):
        return RUN_STATE_FAILED

    strategy_name = manifest.get("strategy") or "grid"
    if strategy_name not in strat_pkg.available_strategies():
        _emit_failure(store, run_id, audit_path, audit_emit_fn,
                      reason=f"strategy-not-installed:{strategy_name}",
                      tenant_id=tenant_id)
        return RUN_STATE_FAILED

    history = store.read_iterations(run_id)
    last_iter = max((h.iter for h in history), default=0)

    try:
        summary = store.read_summary(run_id) or {}
    except (OSError, FileNotFoundError):
        summary = {}
    # consecutive resumes with no new iteration in between
    attempts = (int(summary.get("resume_attempts") or 0) + 1
                if summary.get("resume_last_total") == len(history) else 1)
    if attempts > MAX_RESUME_ATTEMPTS_WITHOUT_PROGRESS:
        _emit_failure(store, run_id, audit_path, audit_emit_fn,
                      reason="resume-attempts-exceeded", tenant_id=tenant_id)
        return RUN_STATE_FAILED
    summary.update({"resume_attempts": attempts, "resume_last_total": len(history)})
    try:
        store.write_summary(run_id, summary)
    except OSError:
        pass

    # the resume itself is a fact on the chain: it RE-EXECUTES the tool
    for emit in (run_audit_emit,):
        if emit is None:
            continue
        try:
            emit("compute.run_recovering", run_id=run_id, tenant_id=tenant_id,
                 resume_from_iter=last_iter + 1, history_size=len(history))
        except Exception:  # noqa: BLE001
            log.exception("audit emit failed for compute.run_recovering")
    if audit_path is not None or audit_emit_fn is not None:
        try:
            _audit_emit(
                "compute.run_recovering",
                path=audit_path or Path("/dev/null"),
                run_id=run_id,
                tenant_id=tenant_id,
                resume_from_iter=last_iter + 1,
                history_size=len(history),
                write_event_fn=audit_emit_fn,
            )
        except Exception:  # noqa: BLE001
            log.exception("audit emit failed for compute.run_recovering")

    spec = ComputeRunSpec(
        tenant_id=tenant_id,
        tool_name=str(manifest.get("tool_name", "unknown")),
        param_grid=dict(manifest.get("param_grid", {})),
        loss_metric=str(manifest.get("loss_metric", "loss")),
        strategy_name=strategy_name,
        budget=Budget.from_dict(manifest.get("budget")),
        minimise=bool(manifest.get("minimise", True)),
        data_handle=manifest.get("data_handle"),
        seed=manifest.get("seed"),
        top_k_size=int(manifest.get("top_k_size", 5)),
        sensitive_fields=list(manifest.get("sensitive_fields") or []),
        datasources=list(manifest.get("datasources") or []),
    )
    run = ComputeRun(
        spec, corvin_home=corvin_home, runner_fn=runner_fn,
        strategy_factory=strat_pkg.load_strategy,
        run_id=run_id, audit_emit=run_audit_emit,
    )
    try:
        prior_wall_s = float(summary.get("total_wall_s") or 0.0)
    except (TypeError, ValueError):
        prior_wall_s = 0.0
    return ResumePlan(run=run, history=history, prior_wall_s=prior_wall_s)


def resume_run(
    corvin_home: Path,
    tenant_id: str,
    run_id: str,
    runner_fn: RunnerFn,
    *,
    audit_path: Path | None = None,
    audit_emit_fn: Callable[..., Any] | None = None,
    run_audit_emit: Callable[..., Any] | None = None,
) -> str:
    """Rebuild a run from disk and run it to its end in THIS thread; returns the final state.

    The worker does not use this (it registers the :class:`ResumePlan` as a regular run so
    it can be aborted / interrupted and honours ``max_concurrent_runs``); it stays the
    synchronous convenience for tools and tests."""
    plan = prepare_resume(corvin_home, tenant_id, run_id, runner_fn, audit_path=audit_path,
                          audit_emit_fn=audit_emit_fn, run_audit_emit=run_audit_emit)
    if isinstance(plan, str):
        return plan
    return plan.execute().state


def scan_orphaned(
    corvin_home: Path,
    tenant_id: str,
    *,
    older_than_s: float,
    now: float | None = None,
) -> list[str]:
    """Return run_ids that are non-terminal AND stale (no recent progress).

    Staleness is judged by the ``summary.json`` mtime — the last time the run
    wrote progress. A run untouched for ``older_than_s`` seconds is a candidate
    for reaping. The threshold is what separates a genuine orphan from a run a
    live worker is actively iterating, so a non-zero ``older_than_s`` is the
    structural guard against reaping an in-flight run. Read-only.
    """
    now = time.time() if now is None else now
    store = RunStore(corvin_home, tenant_id)
    out: list[str] = []
    for run_id in store.list_runs():
        try:
            summary = store.read_summary(run_id)
        except (OSError, FileNotFoundError):
            continue
        if summary.get("state") in TERMINAL_STATES:
            continue
        mtime = store.summary_mtime(run_id)
        if mtime is None or (now - mtime) < older_than_s:
            continue
        out.append(run_id)
    return out


def reap_orphaned(
    corvin_home: Path,
    tenant_id: str,
    *,
    older_than_s: float,
    now: float | None = None,
    audit_path: Path | None = None,
    audit_emit_fn: Callable[..., Any] | None = None,
) -> list[str]:
    """Finalize non-terminal runs that no worker will ever resume.

    The worker daemon RESUMES non-terminal runs on startup (``scan_resumable``
    + ``resume_run``). But a run whose worker is gone — and which no ``serve``
    daemon will pick up — sits at ``running``/``queued`` forever, keeps counting
    against compute quota, and misleads the console. Unlike crash recovery
    (which resumes and therefore needs a Forge ``runner_fn``), this finalizes
    such orphans as ``failed`` WITHOUT executing them, so it is safe to run when
    no worker / no runner is available.

    Orphans are selected by :func:`scan_orphaned` (non-terminal + stale beyond
    ``older_than_s``). Operator-invoked only (CLI ``corvin-compute reap``) —
    never auto-started from the bridge/adapter (L25 invariant: the bridge must
    not drive the compute worker).

    Returns the list of reaped run_ids.
    """
    store = RunStore(corvin_home, tenant_id)
    reaped: list[str] = []
    for run_id in scan_orphaned(corvin_home, tenant_id,
                                older_than_s=older_than_s, now=now):
        try:
            summary = store.read_summary(run_id)
        except (OSError, FileNotFoundError):
            continue
        summary.update({
            "state": RUN_STATE_FAILED,
            "convergence_reason": "reaped:orphaned-no-worker",
        })
        try:
            store.write_summary(run_id, summary)
        except OSError:
            continue
        reaped.append(run_id)
        if audit_path is not None or audit_emit_fn is not None:
            try:
                _audit_emit(
                    "compute.run_failed",
                    path=audit_path or Path("/dev/null"),
                    run_id=run_id,
                    tenant_id=tenant_id,
                    iter=0,
                    error_class="OrphanReaped",
                    error_message="stale-no-worker",
                    write_event_fn=audit_emit_fn,
                )
            except Exception:  # noqa: BLE001
                log.exception("audit emit failed for compute.run_failed (reap)")
    return reaped


def _emit_failure(
    store: RunStore, run_id: str, audit_path: Path | None,
    audit_emit_fn: Callable[..., Any] | None,
    *, reason: str, tenant_id: str,
) -> None:
    """Mark a run failed and write a terminal summary."""
    try:
        summary = store.read_summary(run_id)
    except (OSError, FileNotFoundError):
        summary = {}
    summary.update({
        "state": RUN_STATE_FAILED,
        "convergence_reason": f"recovery-failed:{reason}",
    })
    try:
        store.write_summary(run_id, summary)
    except OSError:
        pass
    if audit_path is not None or audit_emit_fn is not None:
        try:
            _audit_emit(
                "compute.run_failed",
                path=audit_path or Path("/dev/null"),
                run_id=run_id,
                tenant_id=tenant_id,
                iter=0,
                error_class="RecoveryFailed",
                error_message=reason[:200],
                write_event_fn=audit_emit_fn,
            )
        except Exception:  # noqa: BLE001
            log.exception("audit emit failed for compute.run_failed")
