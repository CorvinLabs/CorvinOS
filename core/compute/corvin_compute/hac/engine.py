"""HACEngine — ComputeEngine for ADR-0028 Hierarchical Adaptive Compute."""
from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from typing import Any, Callable

from ..engine_protocol import (
    ComputeEngine,
    ComputeSpec,
    ComputeStatus,
    ComputeResult,
    GateAction,
    UnknownJobId,
)
from .manifest import HACManifest, SubManagerSpec, LossWeights, new_hac_id
from .coordinator import HACCoordinator
from .attribution import compute_root_loss, compute_attribution


class HACEngine:
    engine_id = "hac"
    display_name = "Hierarchical Adaptive Compute (ADR-0028)"
    job_id_prefix = "hac_"
    supports_gates = True

    def __init__(
        self,
        *,
        corvin_home: Path,
        runner_fn: Callable,
        audit_emit: Callable | None = None,
        loop: asyncio.AbstractEventLoop | None = None,
    ):
        self._corvin_home = Path(corvin_home)
        self._runner_fn = runner_fn
        self._audit_emit = audit_emit or (lambda *a, **kw: None)
        self._loop = loop
        self._coordinators: dict[str, HACCoordinator] = {}
        self._gate_queues: dict[str, asyncio.Queue] = {}
        self._lock = threading.Lock()

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def submit(self, spec: ComputeSpec) -> str:
        hac_id = new_hac_id()
        extra = spec.extra
        sub_managers = [SubManagerSpec.from_dict(sm) for sm in extra.get("sub_managers", [])]
        loss_weights_dict = extra.get("loss_weights", {})
        if not loss_weights_dict.get("weights") and sub_managers:
            n = len(sub_managers)
            loss_weights_dict = {"weights": {sm.manager_id: 1.0 / n for sm in sub_managers}}
        loss_weights = LossWeights.from_dict(loss_weights_dict)

        manifest = HACManifest(
            hac_id=hac_id,
            tenant_id=spec.tenant_id,
            sub_managers=sub_managers,
            loss_weights=loss_weights,
            budget=spec.budget,
            backprop_gate=bool(extra.get("backprop_gate", True)),
            backprop_gate_timeout_s=float(extra.get("backprop_gate_timeout_s", 7200.0)),
            max_backprop_rounds=int(extra.get("max_backprop_rounds", 5)),
            convergence_epsilon=float(extra.get("convergence_epsilon", 0.005)),
            convergence_window=int(extra.get("convergence_window", 2)),
            fluid_reallocation=bool(extra.get("fluid_reallocation", True)),
            max_transfer_fraction=float(extra.get("max_transfer_fraction", 0.5)),
        )
        gate_q: asyncio.Queue = asyncio.Queue()
        coordinator = HACCoordinator(
            hac_id=hac_id,
            manifest=manifest,
            corvin_home=self._corvin_home,
            runner_fn=self._runner_fn,
            audit_emit=self._audit_emit,
            gate_queue=gate_q,
        )
        with self._lock:
            self._coordinators[hac_id] = coordinator
            self._gate_queues[hac_id] = gate_q
        if self._loop is not None:
            asyncio.run_coroutine_threadsafe(coordinator.run(), self._loop)
        return hac_id

    def status(self, job_id: str) -> ComputeStatus:
        coord = self._get(job_id)
        d = coord.get_status_dict()
        return ComputeStatus(
            job_id=job_id,
            engine_id="hac",
            state=d["state"],
            progress={"round": d["round"], "root_loss_history": d["root_loss_history"]},
            detail={"manager_states": d["manager_states"], "best_losses": d["best_losses"]},
        )

    def result(self, job_id: str, wait_s: float = 30.0) -> ComputeResult:
        coord = self._get(job_id)
        deadline = time.monotonic() + wait_s
        while coord._state not in ("converged", "failed", "aborted", "budget"):
            if time.monotonic() >= deadline:
                break
            time.sleep(0.25)
        d = coord.get_result_dict()
        return ComputeResult(job_id=job_id, engine_id="hac", state=d["state"], result=d)

    def gate_action(self, job_id: str, action: GateAction) -> None:
        q = self._gate_queues.get(job_id)
        if q is None:
            raise UnknownJobId(f"no hac job: {job_id!r}")
        payload = {"action_type": action.action_type, "payload": action.payload}
        if self._loop is not None:
            self._loop.call_soon_threadsafe(q.put_nowait, payload)
        else:
            q.put_nowait(payload)

    def abort(self, job_id: str) -> None:
        coord = self._coordinators.get(job_id)
        if coord is None:
            raise UnknownJobId(f"no hac job: {job_id!r}")
        coord.request_abort()

    def _get(self, job_id: str) -> HACCoordinator:
        coord = self._coordinators.get(job_id)
        if coord is None:
            raise UnknownJobId(f"no hac job: {job_id!r}")
        return coord


def register_hac_engine(
    corvin_home: Path,
    runner_fn: Callable,
    *,
    audit_emit: Callable | None = None,
    registry=None,
) -> HACEngine:
    from .. import engine_registry as _reg

    r = registry or _reg._default_registry
    engine = HACEngine(corvin_home=corvin_home, runner_fn=runner_fn, audit_emit=audit_emit)
    r.register(engine)
    return engine
