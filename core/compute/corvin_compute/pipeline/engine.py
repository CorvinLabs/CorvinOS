"""PipelineEngine — ComputeEngine implementation for ADR-0027.

Implements the ``ComputeEngine`` Protocol from ``engine_protocol.py`` using
``PipelineCoordinator`` asyncio tasks running inside the WorkerServer's event
loop.

Thread-safety contract
----------------------
* ``submit()`` and ``abort()`` may be called from any thread.
* ``gate_action()`` is safe to call from any thread — it uses
  ``loop.call_soon_threadsafe`` to deliver the action into the queue.
* The ``_coordinators`` / ``_gate_queues`` dicts are protected by
  ``_lock`` for registration; individual coordinator state is only
  mutated from within the event loop (via ``asyncio.to_thread``).

No ``anthropic`` import anywhere in this file.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable

from ..engine_protocol import (
    ComputeEngine,
    ComputeResult,
    ComputeSpec,
    ComputeStatus,
    GateAction,
    UnknownJobId,
)
from .coordinator import PipelineCoordinator
from .manifest import PipelineManifest, StageSpec, new_pipeline_id, require_unique_stage_ids


class PipelineEngine:
    """ComputeEngine that runs ADR-0027 pipelines via PipelineCoordinator tasks.

    The engine must be given an asyncio event loop (``set_loop``) before
    ``submit`` is called; the WorkerServer calls this after it binds its loop.
    """

    engine_id = "pipeline"
    display_name = "Adaptive Compute Pipelines (ADR-0027)"
    job_id_prefix = "pipeline_"
    supports_gates = True

    def __init__(
        self,
        *,
        corvin_home: Path,
        runner_fn: Callable,
        audit_emit: Callable | None = None,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        self._corvin_home = Path(corvin_home)
        self._runner_fn = runner_fn
        self._audit_emit = audit_emit or (lambda *a, **kw: None)
        self._loop: asyncio.AbstractEventLoop | None = loop

        self._coordinators: dict[str, PipelineCoordinator] = {}
        self._gate_queues: dict[str, asyncio.Queue] = {}
        # job id -> the concurrent Future of its coordinator coroutine, so a
        # stopping worker can wait for the terminal record (see wait_stopped).
        self._futures: dict[str, Any] = {}
        self._lock = threading.Lock()

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Bind the engine to *loop* — called by WorkerServer after its loop starts."""
        self._loop = loop

    # -- ComputeEngine protocol ---------------------------------------------------

    def submit(self, spec: ComputeSpec) -> str:
        """Create a pipeline from *spec* and schedule it as an asyncio Task.

        ``spec.extra`` fields consumed:
          * ``stages``                — list of StageSpec dicts (required)
          * ``steering_gate``         — bool, default True
          * ``steering_gate_timeout_s`` — float, default 3600.0
        """
        if self._loop is None:
            raise RuntimeError(
                "PipelineEngine has no event loop — call set_loop() first"
            )

        pipeline_id = new_pipeline_id()
        stage_dicts = spec.extra.get("stages", [])
        stages = [StageSpec.from_dict(s) for s in stage_dicts]
        # R3-BR-5: refused HERE, before an id is handed out - a pipeline whose stages
        # share an id would overwrite its own results and artifact directories.
        require_unique_stage_ids(stages)
        manifest = PipelineManifest(
            pipeline_id=pipeline_id,
            tenant_id=spec.tenant_id,
            stages=stages,
            steering_gate=bool(spec.extra.get("steering_gate", True)),
            steering_gate_timeout_s=float(
                spec.extra.get("steering_gate_timeout_s", 3600.0)
            ),
            budget=dict(spec.budget) if spec.budget else {},
        )

        # asyncio.Queue must be created inside the event loop for safety when
        # using loop.call_soon_threadsafe later.  We create it here (same
        # thread as submit) but the engine may be used from a worker thread;
        # the queue itself is only put_nowait'd via call_soon_threadsafe so
        # there is no race condition.
        gate_q: asyncio.Queue = asyncio.Queue()

        coordinator = PipelineCoordinator(
            pipeline_id=pipeline_id,
            manifest=manifest,
            corvin_home=self._corvin_home,
            runner_fn=self._runner_fn,
            audit_emit=self._audit_emit,
            gate_queue=gate_q,
        )

        with self._lock:
            self._coordinators[pipeline_id] = coordinator
            self._gate_queues[pipeline_id] = gate_q

        # Schedule the coordinator coroutine in the engine's event loop
        fut = asyncio.run_coroutine_threadsafe(coordinator.run(), self._loop)
        with self._lock:
            self._futures[pipeline_id] = fut
        return pipeline_id

    def status(self, job_id: str) -> ComputeStatus:
        coord = self._get_coordinator(job_id)
        d = coord.get_status_dict()
        return ComputeStatus(
            job_id=job_id,
            engine_id="pipeline",
            state=d["state"],
            progress={
                "current_stage_idx": d["current_stage_idx"],
                "current_stage_id": d["current_stage_id"],
                "total_stages": d["total_stages"],
                "gate_index": d["gate_index"],
            },
            detail={
                "completed_stages": d["completed_stages"],
                "best_losses": d["best_losses"],
            },
        )

    def result(self, job_id: str, wait_s: float = 30.0) -> ComputeResult:
        coord = self._get_coordinator(job_id)
        terminal_states = frozenset({"converged", "failed", "aborted"})
        deadline = time.monotonic() + wait_s
        while coord._state not in terminal_states:
            if time.monotonic() >= deadline:
                break
            time.sleep(0.2)
        d = coord.get_result_dict()
        return ComputeResult(
            job_id=job_id,
            engine_id="pipeline",
            state=d["state"],
            result=d,
            audit_ref="",
        )

    def gate_action(self, job_id: str, action: GateAction) -> None:
        """Deliver a gate action to the coordinator.

        Thread-safe: uses ``loop.call_soon_threadsafe`` so the action is
        enqueued inside the event loop without data races.
        """
        q = self._gate_queues.get(job_id)
        if q is None:
            raise UnknownJobId(f"no pipeline job: {job_id!r}")
        action_dict = {"action_type": action.action_type, "payload": action.payload}
        if self._loop is not None and self._loop.is_running():
            self._loop.call_soon_threadsafe(q.put_nowait, action_dict)
        else:
            # Fallback: direct put_nowait (only safe if caller is on the loop
            # thread or the loop has not started yet)
            q.put_nowait(action_dict)

    def abort(self, job_id: str) -> None:
        coord = self._coordinators.get(job_id)
        if coord is None:
            raise UnknownJobId(f"no pipeline job: {job_id!r}")
        coord.request_abort()

    def interrupt_all(self) -> int:
        """Worker shutdown: stop every non-terminal pipeline. Returns the count."""
        with self._lock:
            coords = list(self._coordinators.values())
        n = 0
        for coord in coords:
            if coord._state not in ("converged", "failed", "aborted"):
                coord.request_interrupt()
                n += 1
        return n

    async def wait_stopped(self, timeout_s: float) -> int:
        """Worker shutdown, after ``interrupt_all``: make every pipeline terminal.

        ``interrupt_all`` only sets flags. The coordinator coroutine writes the
        terminal record once its running tool iteration returns - and a worker
        that stopped without waiting was torn down first: in memory ``aborted``,
        on disk an empty summary, in the chain no ``compute.pipeline_terminal``.
        This waits up to *timeout_s* for the coroutines; a pipeline still not
        terminal then gets its ``aborted`` record written synchronously. Returns
        how many needed that fallback. Must be awaited on the engine's loop.
        """
        return await _drain(self, timeout_s)

    # -- internals ----------------------------------------------------------------

    def _get_coordinator(self, job_id: str) -> PipelineCoordinator:
        coord = self._coordinators.get(job_id)
        if coord is None:
            raise UnknownJobId(f"no pipeline job: {job_id!r}")
        return coord


async def _drain(engine: Any, timeout_s: float) -> int:
    """Shared shutdown drain for the pipeline and HAC engines (same fields)."""
    with engine._lock:
        futures = dict(engine._futures)
        coords = dict(engine._coordinators)
    pending = [asyncio.wrap_future(f) for f in futures.values() if not f.done()]
    if pending:
        # asyncio.wait never raises on timeout and does not cancel the waited
        # coroutines; a coordinator still running keeps its own cancel handler.
        await asyncio.wait(pending, timeout=max(0.0, float(timeout_s)))
    forced = 0
    for coord in coords.values():
        if coord.is_terminal():
            continue
        try:
            coord.finish_aborted()
            forced += 1
        except Exception:  # noqa: BLE001 — shutdown must proceed
            logging.getLogger(__name__).exception(
                "terminal record for %s not written at shutdown",
                getattr(coord, "pipeline_id", None) or getattr(coord, "hac_id", "?"))
    return forced


# -- convenience factory ---------------------------------------------------------

def register_pipeline_engine(
    corvin_home: Path,
    runner_fn: Callable,
    *,
    audit_emit: Callable | None = None,
    registry=None,
) -> PipelineEngine:
    """Instantiate and register a PipelineEngine in *registry*.

    Uses the module-level default registry when *registry* is None.
    """
    from .. import engine_registry as _reg

    r = registry or _reg._default_registry
    engine = PipelineEngine(
        corvin_home=corvin_home,
        runner_fn=runner_fn,
        audit_emit=audit_emit,
    )
    r.register(engine)
    return engine
