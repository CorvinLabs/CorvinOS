"""ComputeEngine protocol and shared data types (ADR-0029)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class ComputeSpec:
    engine: str
    tenant_id: str
    budget: dict
    tool_name: str | None = None
    param_grid: dict | None = None
    loss_metric: str = "loss"
    strategy: str = "grid"
    minimise: bool = True
    data_handle: str | None = None
    seed: int | None = None
    top_k_size: int = 5
    sensitive_fields: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)


@dataclass
class ComputeStatus:
    job_id: str
    engine_id: str
    state: str  # "running" | "gate_open" | "terminal" | "failed" | "queued"
    progress: dict  # engine-specific: iterations, round, stage_id, etc.
    detail: dict  # engine-specific: top_k, attributions, best_loss, etc.


@dataclass
class ComputeResult:
    job_id: str
    engine_id: str
    state: str
    result: dict  # engine-specific
    audit_ref: str = ""


@dataclass
class GateAction:
    action_type: str  # "resume" | "abort" | "add_stage" | "steer" | "forge_noted" | "reallocate_budget" | "re_run_manager"
    payload: dict = field(default_factory=dict)


class EngineDoesNotSupportGates(RuntimeError):
    pass


class UnknownJobId(KeyError):
    pass


@runtime_checkable
class ComputeEngine(Protocol):
    engine_id: str
    display_name: str
    job_id_prefix: str
    supports_gates: bool

    def submit(self, spec: ComputeSpec) -> str: ...
    def status(self, job_id: str) -> ComputeStatus: ...
    def result(self, job_id: str, wait_s: float = 30.0) -> ComputeResult: ...
    def gate_action(self, job_id: str, action: GateAction) -> None: ...
    def abort(self, job_id: str) -> None: ...
