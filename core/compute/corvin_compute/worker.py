"""Worker server (ADR-0013 Phase 13.4).

A long-running asyncio process bound to a Unix socket. Each tenant
runs its own worker; cross-tenant isolation is achieved by separate
socket paths + the per-worker tenant binding.

The worker reuses Forge's :func:`runner.run_tool` verbatim — no second
sandbox. The driver loop (sequential in 13.2; parallel in 13.7) is the
unit of work; the worker is the orchestrator over multiple concurrent
runs.
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
import os
import sys
import threading
from pathlib import Path
from typing import Any, Callable, Mapping

from .budget import Budget, RUN_STATE_QUEUED, RUN_STATE_RUNNING, TERMINAL_STATES
from .driver import ComputeRun, ComputeRunSpec, RunnerFn
from .state import RunStore, new_run_id, validate_run_id
from . import strategies as strat_pkg
from .transport import recv_frame, send_frame, TransportError

# ADR-0029 — import engine types lazily to avoid circular imports at module level
_PIPELINE_JOB_PREFIX = "pipeline_"
_HAC_JOB_PREFIX = "hac_"
_ABATCH_JOB_PREFIX = "abatch_"  # ADR-0099 — Anthropic Batch Compute
_MULTI_ENGINE_PREFIXES = (_PIPELINE_JOB_PREFIX, _HAC_JOB_PREFIX, _ABATCH_JOB_PREFIX)


log = logging.getLogger(__name__)


# ── L25 → background-completion notification bridge ─────────────────────────
# A compute run is detached and durable but poll-only; when the submit carried a
# messenger origin (client injects it from CORVIN_CHANNEL_ID), notify the user on
# completion via the shared completion_notify backbone. register() at submit
# persists the origin to disk (survives a worker restart); mark_done() at the
# terminal state is a no-op when nothing was registered, so poll-only runs are
# unchanged.
_MESSENGER_CHANNELS = frozenset(
    {"discord", "telegram", "whatsapp", "slack", "signal", "email", "teams"}
)


def _load_completion_notify():
    """Import the bridge-side completion_notify (best-effort). Adds the bridge
    shared dir to sys.path — compute lives in core/, the backbone in operator/."""
    try:
        shared = Path(__file__).resolve().parents[3] / "operator" / "bridges" / "shared"
        if shared.exists() and str(shared) not in sys.path:
            sys.path.insert(0, str(shared))
        import completion_notify as _cn  # type: ignore
        return _cn
    except Exception:  # noqa: BLE001
        return None


class WorkerError(RuntimeError):
    """Base class for typed worker errors that round-trip to the client."""


class TenantMismatch(WorkerError):
    """Request's ``tenant_id`` does not match the worker's tenant."""


class UnknownHandle(WorkerError):
    pass


class StrategyNotAllowed(WorkerError):
    pass


class BudgetExhausted(WorkerError):
    pass


# Map exception types to their wire-error class names. Anything outside
# the curated map degrades to "InternalError" so internal implementation
# detail leakage is bounded.
_ERROR_CLASS_MAP: dict[type, str] = {
    TenantMismatch: "TenantMismatch",
    UnknownHandle: "UnknownHandle",
    StrategyNotAllowed: "StrategyNotAllowed",
    BudgetExhausted: "BudgetExhausted",
    ValueError: "InvalidArgument",
    KeyError: "InvalidArgument",
    TransportError: "TransportError",
}


@dataclasses.dataclass
class _RunEntry:
    run: Any  # ComputeRun for flat; coordinator object for pipeline/hac
    task: asyncio.Task | None
    state: str  # current rolling state (queued / running / terminal)
    result_event: asyncio.Event
    engine_id: str = "flat"  # ADR-0029: which engine owns this job


class WorkerServer:
    """asyncio Unix-socket server bound to one tenant."""

    def __init__(
        self,
        *,
        tenant_id: str,
        corvin_home: Path,
        socket_path: Path,
        max_concurrent_runs: int = 2,
        runner_fn: RunnerFn | None = None,
        strategies_allowed: list[str] | None = None,
        audit_emit: Callable[..., None] | None = None,
        extra_engines: list[Any] | None = None,
    ) -> None:
        self.tenant_id = tenant_id
        self.corvin_home = Path(corvin_home)
        self.socket_path = Path(socket_path)
        self.max_concurrent_runs = max(1, int(max_concurrent_runs))
        self.runner_fn: RunnerFn = runner_fn or _default_runner_fn
        self.strategies_allowed = list(strategies_allowed) if strategies_allowed \
            else None  # None = permissive (every loaded strategy admitted)
        self.audit_emit = audit_emit or (lambda *a, **kw: None)

        self._runs: dict[str, _RunEntry] = {}
        self._runs_lock = asyncio.Lock()
        self._queue: list[str] = []  # FIFO of queued run_ids
        self._server: asyncio.AbstractServer | None = None
        self._stopped = asyncio.Event()
        self._store = RunStore(self.corvin_home, tenant_id)

        # ADR-0029 — pluggable engines (pipeline, hac, custom)
        # Keyed by engine_id; injected at construction or via register_engine().
        self._extra_engines: dict[str, Any] = {}
        for eng in (extra_engines or []):
            self._extra_engines[eng.engine_id] = eng

    # -- public lifecycle --------------------------------------------------------

    def register_engine(self, engine: Any) -> None:
        """Register an ADR-0029 ComputeEngine at runtime (before serve_forever)."""
        self._extra_engines[engine.engine_id] = engine

    async def serve_forever(self) -> None:
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.socket_path.parent, 0o700)
        except OSError:
            pass
        if self.socket_path.exists():
            try:
                self.socket_path.unlink()
            except OSError:
                pass
        # ADR-0029 — wire the running event loop into any engine that needs it
        _loop = asyncio.get_running_loop()
        for eng in self._extra_engines.values():
            if hasattr(eng, "set_loop"):
                eng.set_loop(_loop)
        # ADR-0013 Phase 13.9 — recover non-terminal runs from disk
        # BEFORE accepting new submissions. Best-effort: a recovery
        # failure logs but does NOT block serving.
        try:
            await asyncio.to_thread(self._recover_pending)
        except Exception:  # noqa: BLE001
            log.exception("recovery sweep crashed (continuing anyway)")
        self._server = await asyncio.start_unix_server(
            self._handle_client, path=str(self.socket_path),
        )
        # Tighten socket to owner-only AFTER bind.
        try:
            os.chmod(self.socket_path, 0o600)
        except OSError:
            pass
        log.info("worker listening at %s (tenant=%s)", self.socket_path, self.tenant_id)
        try:
            async with self._server:
                await self._server.serve_forever()
        except asyncio.CancelledError:
            pass

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            try:
                await self._server.wait_closed()
            except Exception:  # noqa: BLE001
                pass
        # Cancel in-flight tasks
        async with self._runs_lock:
            for entry in self._runs.values():
                if entry.task and not entry.task.done():
                    entry.run.request_abort()
        # Best-effort socket cleanup
        try:
            if self.socket_path.exists():
                self.socket_path.unlink()
        except OSError:
            pass
        self._stopped.set()

    # -- request dispatch --------------------------------------------------------

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
    ) -> None:
        try:
            while True:
                try:
                    frame = await recv_frame(reader)
                except TransportError as exc:
                    await self._send_error(writer, "TransportError", str(exc))
                    return
                except asyncio.IncompleteReadError:
                    return
                if frame is None:
                    return
                op = frame.get("op")
                params = frame.get("params") or {}
                try:
                    result = await self._dispatch(op, params)
                    await send_frame(writer, {"ok": True, "result": result})
                except Exception as exc:  # noqa: BLE001
                    cls = _ERROR_CLASS_MAP.get(type(exc), "InternalError")
                    await self._send_error(writer, cls, str(exc))
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass

    async def _send_error(self, writer: asyncio.StreamWriter,
                          error_class: str, message: str) -> None:
        try:
            await send_frame(writer, {
                "ok": False, "error_class": error_class, "error": message,
            })
        except Exception:  # noqa: BLE001
            pass

    async def _dispatch(self, op: str | None, params: Mapping[str, Any]) -> Any:
        if op == "submit_run":
            return await self._op_submit_run(params)
        if op == "get_status":
            return await self._op_get_status(params)
        if op == "get_result":
            return await self._op_get_result(params)
        if op == "abort_run":
            return await self._op_abort_run(params)
        if op == "list_runs":
            return await self._op_list_runs(params)
        if op == "gate_action":
            return await self._op_gate_action(params)  # ADR-0029
        if op == "ping":
            return {"pong": True, "tenant_id": self.tenant_id}
        raise ValueError(f"unknown op: {op!r}")

    # -- ops ---------------------------------------------------------------------

    async def _op_gate_action(self, params: Mapping[str, Any]) -> dict:
        """ADR-0029 — route a GateAction to the correct engine coordinator."""
        handle = params.get("compute_handle", "")
        if not isinstance(handle, str) or not handle:
            raise ValueError("compute_handle missing")
        action_type = str(params.get("action_type", ""))
        payload = dict(params.get("payload") or {})

        # Find the owning engine by job_id prefix
        engine = None
        for eng in self._extra_engines.values():
            if handle.startswith(eng.job_id_prefix):
                engine = eng
                break
        if engine is None:
            raise UnknownHandle(f"no engine found for job_id {handle!r}")
        if not engine.supports_gates:
            raise ValueError(f"engine {engine.engine_id!r} does not support gates")

        from .engine_protocol import GateAction
        engine.gate_action(handle, GateAction(action_type=action_type, payload=payload))
        return {"ok": True, "job_id": handle, "action_type": action_type}

    async def _op_submit_run(self, params: Mapping[str, Any]) -> dict:
        # tenant binding check (defence in depth — the socket itself is
        # tenant-scoped, but explicit verify catches misrouted clients).
        req_tid = params.get("tenant_id")
        if req_tid and req_tid != self.tenant_id:
            raise TenantMismatch(
                f"worker serves tenant {self.tenant_id!r}, "
                f"request asked for {req_tid!r}",
            )

        # ADR-0029 — dispatch non-flat engines before the flat path
        engine_name = str(params.get("engine") or "flat")
        if engine_name != "flat":
            engine = self._extra_engines.get(engine_name)
            if engine is None:
                raise ValueError(f"unknown engine {engine_name!r}")
            from .engine_protocol import ComputeSpec
            spec = ComputeSpec(
                engine=engine_name,
                tenant_id=self.tenant_id,
                budget=dict(params.get("budget") or {}),
                param_grid=dict(params.get("param_grid") or {}),
                minimise=True if params.get("minimise") is None else bool(params["minimise"]),
                extra=dict(params.get("extra") or {}),
            )
            job_id = engine.submit(spec)
            async with self._runs_lock:
                entry = _RunEntry(run=None, task=None, state=RUN_STATE_RUNNING,
                                  result_event=asyncio.Event(), engine_id=engine_name)
                self._runs[job_id] = entry
            return {"compute_handle": job_id, "state": RUN_STATE_RUNNING, "engine": engine_name}

        strategy_name = params.get("strategy") or "grid"
        if self.strategies_allowed is not None \
                and strategy_name not in self.strategies_allowed:
            raise StrategyNotAllowed(
                f"strategy {strategy_name!r} not in allowed list "
                f"{self.strategies_allowed!r}",
            )

        budget = Budget.from_dict(params.get("budget"))

        spec = ComputeRunSpec(
            tenant_id=self.tenant_id,
            tool_name=str(params["tool_name"]),
            param_grid=dict(params["param_grid"]),
            loss_metric=str(params.get("loss_metric", "loss")),
            strategy_name=strategy_name,
            budget=budget,
            minimise=bool(params.get("minimise", True)),
            data_handle=params.get("data_handle"),
            seed=params.get("seed"),
            top_k_size=int(params.get("top_k_size", 5)),
            sensitive_fields=list(params.get("sensitive_fields") or []),
            datasources=list(params.get("datasources") or []),
        )

        run_id = new_run_id()
        run = ComputeRun(
            spec, corvin_home=self.corvin_home,
            runner_fn=self.runner_fn,
            strategy_factory=strat_pkg.load_strategy,
            run_id=run_id, audit_emit=self.audit_emit,
        )
        entry = _RunEntry(run=run, task=None, state=RUN_STATE_QUEUED,
                          result_event=asyncio.Event())
        async with self._runs_lock:
            self._runs[run_id] = entry
            # max_concurrent_runs budgets FLAT ComputeRun worker tasks only.
            # Non-flat engine jobs (pipeline/HAC) manage their own internal
            # concurrency and never occupy a flat _exec_run task — counting their
            # perpetually-RUNNING placeholder entries here let two non-flat submits
            # permanently starve every flat run (they never transition out of
            # RUNNING). Count only flat entries against the flat budget.
            running_count = sum(1 for e in self._runs.values()
                                if e.state == RUN_STATE_RUNNING
                                and e.engine_id == "flat")
            if running_count < self.max_concurrent_runs:
                entry.state = RUN_STATE_RUNNING
                entry.task = asyncio.create_task(self._exec_run(run_id))
            else:
                self._queue.append(run_id)

        # If the submit carried a messenger origin, register a pending
        # completion so the user is notified when this detached run finishes.
        self._register_compute_notify(run_id, params, str(params.get("tool_name", "")))

        return {
            "compute_handle": run_id,
            "state": entry.state,
            "accepted_at": run.spec.budget.max_wall_clock_s,  # cheap placeholder
        }

    async def _op_get_status(self, params: Mapping[str, Any]) -> dict:
        handle = self._require_handle(params)
        entry = self._runs.get(handle)
        # ADR-0029 — route to the right engine for non-flat jobs
        if entry is not None and entry.engine_id != "flat":
            engine = self._extra_engines.get(entry.engine_id)
            if engine:
                st = engine.status(handle)
                return {"compute_handle": handle, "engine": entry.engine_id,
                        "state": st.state, **st.progress, **st.detail}
        if entry is None:
            return self._status_from_disk(handle)
        try:
            summary = self._store.read_summary(handle)
        except (OSError, FileNotFoundError):
            summary = {}
        return self._status_payload(entry, summary)

    async def _op_get_result(self, params: Mapping[str, Any]) -> dict:
        handle = self._require_handle(params)
        wait_s = float(params.get("wait_s", 0.0))
        entry = self._runs.get(handle)
        # ADR-0029 — route to the right engine for non-flat jobs
        if entry is not None and entry.engine_id != "flat":
            engine = self._extra_engines.get(entry.engine_id)
            if engine:
                res = engine.result(handle, wait_s=wait_s)
                return {"compute_handle": handle, "engine": entry.engine_id,
                        "state": res.state, **res.result}
        if entry is None:
            return self._result_from_disk(handle)
        if wait_s > 0 and entry.state not in TERMINAL_STATES:
            try:
                await asyncio.wait_for(entry.result_event.wait(),
                                       timeout=min(wait_s, 30.0))
            except asyncio.TimeoutError:
                pass
        return self._result_from_disk(handle)

    async def _op_abort_run(self, params: Mapping[str, Any]) -> dict:
        handle = self._require_handle(params)
        entry = self._runs.get(handle)
        if entry is None:
            raise UnknownHandle(f"no such run: {handle}")
        # Audit gap closure: a run-terminating mutation must hit the chain,
        # like compute.run_started/run_terminal. Metadata only. The emit is
        # best-effort and MUST NOT block the abort — wrap it so an audit
        # failure (e.g. unregistered field) never prevents the run from
        # being aborted. (Review FINDING 1.)
        try:
            self.audit_emit("compute.run_aborted", run_id=handle,
                            engine_id=entry.engine_id,
                            iterations_done=self._iter_count(handle))
        except Exception:  # noqa: BLE001 — observability is best-effort
            pass
        # ADR-0029 — non-flat engines have their own abort path
        if entry.engine_id != "flat":
            engine = self._extra_engines.get(entry.engine_id)
            if engine:
                engine.abort(handle)
                return {"state": "aborting", "iterations_done": 0}
        if entry.run is None:
            raise UnknownHandle(f"no such run: {handle}")
        entry.run.request_abort()
        return {"state": "aborting", "iterations_done": self._iter_count(handle)}

    async def _op_list_runs(self, params: Mapping[str, Any]) -> dict:
        return {"runs": self._store.list_runs()}

    # -- helpers -----------------------------------------------------------------

    def _require_handle(self, params: Mapping[str, Any]) -> str:
        handle = params.get("compute_handle")
        if not isinstance(handle, str):
            raise ValueError("compute_handle missing or not a string")
        # ADR-0029 — accept flat (compute_), pipeline, and hac prefixes
        for prefix in _MULTI_ENGINE_PREFIXES:
            if handle.startswith(prefix):
                return handle  # non-flat job_ids are validated by their engine
        try:
            return validate_run_id(handle)
        except ValueError as exc:
            raise UnknownHandle(str(exc)) from None

    def _iter_count(self, run_id: str) -> int:
        try:
            summary = self._store.read_summary(run_id)
            return int(summary.get("total_iterations", 0))
        except (OSError, FileNotFoundError, KeyError, ValueError):
            return 0

    def _status_payload(self, entry: _RunEntry, summary: dict) -> dict:
        return {
            "compute_handle":    entry.run.run_id,
            "state":             entry.state,
            "iterations_done":   int(summary.get("total_iterations", 0)),
            "iterations_budget": entry.run.spec.budget.max_iterations,
            "best_loss":         summary.get("best_loss"),
            "top_k":             list(summary.get("top_k", [])),
            "stall_count":       0,
            "started_at":        summary.get("started_at"),
            "last_iteration_at": summary.get("last_iteration_at"),
            "eta_s":             None,
        }

    def _status_from_disk(self, run_id: str) -> dict:
        if not self._store.exists(run_id):
            raise UnknownHandle(f"no such run: {run_id}")
        summary = self._store.read_summary(run_id)
        manifest = self._store.read_manifest(run_id)
        return {
            "compute_handle":    run_id,
            "state":             summary.get("state", "unknown"),
            "iterations_done":   int(summary.get("total_iterations", 0)),
            "iterations_budget": int(manifest.get("budget", {})
                                     .get("max_iterations", 0)),
            "best_loss":         summary.get("best_loss"),
            "top_k":             list(summary.get("top_k", [])),
            "stall_count":       0,
            "started_at":        summary.get("started_at"),
            "last_iteration_at": summary.get("last_iteration_at"),
            "eta_s":             None,
        }

    def _result_from_disk(self, run_id: str) -> dict:
        if not self._store.exists(run_id):
            raise UnknownHandle(f"no such run: {run_id}")
        summary = self._store.read_summary(run_id)
        manifest = self._store.read_manifest(run_id)
        best_iter_n = summary.get("best_iter")
        best_params: dict = {}
        if best_iter_n is not None:
            for it in self._store.read_iterations(run_id):
                if it.iter == best_iter_n:
                    best_params = dict(it.params)
                    break
        return {
            "state":              summary.get("state", "unknown"),
            "best_params":        best_params,
            "best_loss":          summary.get("best_loss"),
            "total_iterations":   int(summary.get("total_iterations", 0)),
            "total_wall_s":       float(summary.get("total_wall_s", 0.0)),
            "convergence_reason": summary.get("convergence_reason", ""),
            "artifact_path":      str((self.corvin_home / "tenants"
                                       / manifest.get("tenant_id", "_default")
                                       / "compute" / "runs" / run_id)),
            "error":              None,
        }

    # -- worker exec --------------------------------------------------------------

    async def _exec_run(self, run_id: str) -> None:
        """Execute one run end-to-end. Driver work runs in a thread so
        the asyncio loop stays responsive to status polls."""
        entry = self._runs.get(run_id)
        if entry is None or entry.run is None:
            return
        try:
            rec = await asyncio.to_thread(entry.run.run)
            entry.state = rec.state
        except Exception:  # noqa: BLE001
            log.exception("exec_run crashed")
            entry.state = "failed"
        finally:
            entry.result_event.set()
            self._notify_compute_done(run_id, entry.state)
            await self._dequeue_next()

    def _register_compute_notify(self, run_id: str, params: "Mapping[str, Any]",
                                 tool_name: str) -> None:
        """Register a pending completion notification if the submit carried a
        messenger origin (``notify`` = {channel, chat_id, sender}). No-op
        otherwise, so poll-only compute runs are unchanged."""
        notify = params.get("notify")
        if not isinstance(notify, dict):
            return
        channel = str(notify.get("channel") or "")
        chat_id = notify.get("chat_id")
        if channel not in _MESSENGER_CHANNELS or not chat_id:
            return
        # A non-empty sender is REQUIRED. purge_user (GDPR Art. 17) matches
        # records on sender, so an empty sender would leave an un-erasable
        # notification record. The origin (channel/chat_id/sender) SHOULD be
        # bound to the authenticated messenger principal by the caller — never
        # trusted verbatim as a delivery target for another user.
        sender = str(notify.get("sender") or "").strip()
        if not sender:
            log.debug(
                "compute completion notify skipped: empty sender "
                "(origin must be bound to an authenticated principal)"
            )
            return
        cn = _load_completion_notify()
        if cn is None:
            return
        try:
            cn.register(
                run_id, channel=channel, chat_id=chat_id,
                sender=sender,
                tenant_id=self.tenant_id,
                label=f"compute {tool_name}".strip(),
            )
        except Exception:  # noqa: BLE001
            log.debug("compute completion register failed", exc_info=True)

    def _notify_compute_done(self, run_id: str, state: str) -> None:
        """Mark the run's completion ready to deliver. Unconditional +
        idempotent: mark_done is a no-op when no notification was registered,
        and reads the on-disk record (so it still fires after a worker restart
        that lost the in-memory entry)."""
        cn = _load_completion_notify()
        if cn is None:
            return
        # Compute terminal states: converged/stalled/budget_exhausted are
        # successful stops; failed/aborted are not.
        ok = str(state) not in ("failed", "aborted", "error", "crashed")
        text = f"Compute run `{run_id[:8]}` finished — state: {state}."
        try:
            summary = self._store.read_summary(run_id)
            best = summary.get("best_loss", summary.get("best")) if isinstance(summary, dict) else None
            if best is not None:
                text += f" best_loss={best}"
        except Exception:  # noqa: BLE001
            pass
        try:
            cn.mark_done(run_id, text=text, ok=ok)
        except Exception:  # noqa: BLE001
            log.debug("compute completion mark_done failed", exc_info=True)

    async def _dequeue_next(self) -> None:
        async with self._runs_lock:
            if not self._queue:
                return
            # Flat-only slot accounting (see _op_submit_run): non-flat engine
            # jobs must not count against the flat worker-task budget.
            running_count = sum(1 for e in self._runs.values()
                                if e.state == RUN_STATE_RUNNING
                                and e.engine_id == "flat")
            while self._queue and running_count < self.max_concurrent_runs:
                rid = self._queue.pop(0)
                e = self._runs.get(rid)
                if e is None or e.state != RUN_STATE_QUEUED:
                    continue
                e.state = RUN_STATE_RUNNING
                e.task = asyncio.create_task(self._exec_run(rid))
                running_count += 1


    def _recover_pending(self) -> None:
        """Phase 13.9 — scan disk for non-terminal runs and resume.

        Runs synchronously in a worker thread; the asyncio loop is
        free during the scan. Each recovered run becomes a regular
        background task via the same dispatch path as a fresh submit.
        """
        from . import recovery as _recovery
        resumable = _recovery.scan_resumable(self.corvin_home,
                                             self.tenant_id)
        if not resumable:
            return
        log.info("recovery: %d non-terminal runs found", len(resumable))
        for run_id in resumable:
            try:
                _state = _recovery.resume_run(
                    self.corvin_home, self.tenant_id, run_id,
                    runner_fn=self.runner_fn,
                )
                # A run interrupted by a restart resumes HERE, not via _exec_run,
                # so fire the completion notification on this path too. Idempotent
                # (no-op when no origin was registered or already delivered), so
                # a restart-surviving compute run still reaches the user.
                self._notify_compute_done(run_id, str(_state or "converged"))
            except Exception:  # noqa: BLE001
                log.exception("recovery for run_id=%s failed", run_id)


def _default_runner_fn(tool_name: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """Fallback runner — refuses every call.

    The real Forge runner is injected by the operator-side CLI bootstrap
    (Phase 13.5 wires this end-to-end). Tests inject a stub explicitly.
    """
    raise RuntimeError(
        "WorkerServer was started without a runner_fn — refuse to spawn "
        "tools. Inject Forge's runner.run_tool() at construction time.",
    )
