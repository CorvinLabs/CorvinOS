"""FlatEngine — wraps the existing WorkerClient (ADR-0029, implements FlatEngine)."""
from __future__ import annotations

from pathlib import Path

from ..client import WorkerClient, is_socket_reachable
from ..engine_protocol import (
    ComputeEngine,
    ComputeResult,
    ComputeSpec,
    ComputeStatus,
    EngineDoesNotSupportGates,
    GateAction,
    UnknownJobId,
)


class FlatEngine:
    engine_id = "flat"
    display_name = "Flat Compute Engine (ADR-0013 / ADR-0026)"
    job_id_prefix = "compute_"
    supports_gates = False

    def __init__(self, socket_path: Path, *, timeout_s: float = 30.0) -> None:
        self._client = WorkerClient(socket_path, timeout_s=timeout_s)
        self._socket_path = socket_path

    def is_reachable(self) -> bool:
        return is_socket_reachable(self._socket_path)

    def submit(self, spec: ComputeSpec) -> str:
        params = {
            "tool_name": spec.tool_name,
            "param_grid": spec.param_grid,
            "loss_metric": spec.loss_metric,
            "strategy": spec.strategy,
            "budget": spec.budget,
            "minimise": spec.minimise,
            "data_handle": spec.data_handle,
            "seed": spec.seed,
            "top_k_size": spec.top_k_size,
            "sensitive_fields": spec.sensitive_fields,
            "tenant_id": spec.tenant_id,
        }
        resp = self._client.submit_run(**params)
        return resp["compute_handle"]

    def status(self, job_id: str) -> ComputeStatus:
        raw = self._client.get_status(job_id)
        return ComputeStatus(
            job_id=job_id,
            engine_id="flat",
            state=raw.get("state", "unknown"),
            progress={
                "iterations_done": raw.get("iterations_done", 0),
                "iterations_budget": raw.get("iterations_budget", 0),
                "started_at": raw.get("started_at"),
                "last_iteration_at": raw.get("last_iteration_at"),
                "eta_s": raw.get("eta_s"),
            },
            detail={
                "best_loss": raw.get("best_loss"),
                "top_k": raw.get("top_k", []),
            },
        )

    def result(self, job_id: str, wait_s: float = 30.0) -> ComputeResult:
        raw = self._client.get_result(job_id, wait_s=wait_s)
        return ComputeResult(
            job_id=job_id,
            engine_id="flat",
            state=raw.get("state", "unknown"),
            result={
                "best_params": raw.get("best_params", {}),
                "best_loss": raw.get("best_loss"),
                "total_iterations": raw.get("total_iterations", 0),
                "total_wall_s": raw.get("total_wall_s", 0.0),
                "convergence_reason": raw.get("convergence_reason", ""),
                "artifact_path": raw.get("artifact_path", ""),
            },
        )

    def gate_action(self, job_id: str, action: GateAction) -> None:
        raise EngineDoesNotSupportGates(
            "FlatEngine has no gates — use compute_abort to stop"
        )

    def abort(self, job_id: str) -> None:
        self._client.abort_run(job_id)


def register_flat_engine(
    socket_path: Path,
    *,
    timeout_s: float = 30.0,
    registry=None,
) -> FlatEngine:
    from .. import engine_registry as _reg

    r = registry or _reg._default_registry
    engine = FlatEngine(socket_path, timeout_s=timeout_s)
    r.register(engine)
    return engine
