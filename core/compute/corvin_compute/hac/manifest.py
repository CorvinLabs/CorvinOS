"""HAC manifest dataclasses (ADR-0028)."""
from __future__ import annotations

import dataclasses
import json
import os
import secrets
import string
import time
from pathlib import Path
from typing import Any

from ..pipeline.manifest import contained_path, validate_component_id

_HAC_ALPHABET = string.ascii_letters + string.digits + "_-"


def new_hac_id() -> str:
    return "hac_" + "".join(secrets.choice(_HAC_ALPHABET) for _ in range(22))


@dataclasses.dataclass
class SubManagerSpec:
    manager_id: str
    stages: list[dict]   # StageSpec dicts (gets parsed by pipeline coordinator)
    budget_fraction: float = 0.33
    min_iterations: int = 10
    strategy: str = "bayesian"
    steering_gate: bool = False  # sub-managers run without intermediate gates by default

    @classmethod
    def from_dict(cls, d: dict) -> SubManagerSpec:
        return cls(
            manager_id=validate_component_id(d["manager_id"], kind="manager_id"),
            stages=list(d.get("stages", [])),
            budget_fraction=float(d.get("budget_fraction", 0.33)),
            min_iterations=int(d.get("min_iterations", 10)),
            strategy=str(d.get("strategy", "bayesian")),
            steering_gate=bool(d.get("steering_gate", False)),
        )


def require_unique_manager_ids(sub_managers: "list[SubManagerSpec]") -> None:
    """Refuse a HAC run that names one ``manager_id`` twice.

    Results, budgets, loss weights and the on-disk directory are all keyed by
    ``manager_id``: two sub-managers sharing one id run two ComputeRuns whose
    results overwrite each other, and only one of them was reachable by an
    abort (the other kept executing tools after ``request_abort``).
    """
    # Case-insensitive (review R3-BR-5): ``m`` and ``M`` passed this check and are
    # one directory on a case-insensitive filesystem.
    seen: set[str] = set()
    dupes: list[str] = []
    for sm in sub_managers:
        key = str(sm.manager_id).casefold()
        if key in seen and sm.manager_id not in dupes:
            dupes.append(sm.manager_id)
        seen.add(key)
        stage_keys: set[str] = set()
        for stage in (getattr(sm, "stages", None) or []):
            sid = str(stage.get("stage_id", "") if isinstance(stage, dict)
                      else getattr(stage, "stage_id", "")).casefold()
            if not sid:  # a HAC stage need not carry an id
                continue
            if sid in stage_keys:
                raise ValueError(
                    f"duplicate stage_id in sub-manager {sm.manager_id!r} (case-insensitive)")
            stage_keys.add(sid)
    if dupes:
        raise ValueError(f"duplicate manager_id in sub_managers: {sorted(dupes)}")


@dataclasses.dataclass
class LossWeights:
    weights: dict[str, float]   # manager_id -> weight
    field: str = "primary_loss"  # which field in LossProfile to use
    mode: str = "weighted_sum"   # "weighted_sum" | "pareto" | "cascade"

    @classmethod
    def from_dict(cls, d: dict) -> LossWeights:
        return cls(
            weights=dict(d.get("weights", {})),
            field=str(d.get("field", "primary_loss")),
            mode=str(d.get("mode", "weighted_sum")),
        )


@dataclasses.dataclass
class HACManifest:
    hac_id: str
    tenant_id: str
    sub_managers: list[SubManagerSpec]
    loss_weights: LossWeights
    budget: dict  # total budget for the HAC run
    backprop_gate: bool = True
    backprop_gate_timeout_s: float = 7200.0
    max_backprop_rounds: int = 5
    convergence_epsilon: float = 0.005
    convergence_window: int = 2
    fluid_reallocation: bool = True
    max_transfer_fraction: float = 0.5


class HACStore:
    """On-disk state for a HAC run."""

    def __init__(self, corvin_home: Path, tenant_id: str, hac_id: str):
        self.root = Path(corvin_home) / "tenants" / tenant_id / "compute" / "hac" / hac_id

    def write_manifest(self, manifest: HACManifest) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        data = {
            "hac_id": manifest.hac_id,
            "tenant_id": manifest.tenant_id,
            "manager_ids": [m.manager_id for m in manifest.sub_managers],
            # The console reads these keys (routes/compute.py list_hac_runs / hac_detail): without
            # "sub_managers" every real run listed manager_count 0 and the detail view had no managers.
            "sub_managers": [
                {"manager_id": m.manager_id, "budget_fraction": m.budget_fraction,
                 "min_iterations": m.min_iterations, "strategy": m.strategy,
                 "stage_ids": [str(s.get("stage_id", "")) for s in m.stages]}
                for m in manifest.sub_managers
            ],
            "loss_weights": dataclasses.asdict(manifest.loss_weights),
            "budget": manifest.budget,
            "backprop_gate": manifest.backprop_gate,
            "max_backprop_rounds": manifest.max_backprop_rounds,
            "fluid_reallocation": manifest.fluid_reallocation,
            "max_transfer_fraction": manifest.max_transfer_fraction,
            "started_at": time.time(),
        }
        path = self.root / "manifest.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, path)
        os.chmod(path, 0o600)

    def write_summary(self, *, state: str, round_n: int, root_loss: float | None,
                      manager_states: dict, attributions: dict,
                      sub_manager_losses: dict | None = None,
                      root_loss_history: list | None = None,
                      error: str | None = None) -> None:
        data = {
            "error": error,
            "state": state, "round": round_n, "root_loss": root_loss,
            "manager_states": manager_states, "attributions": attributions,
            # Persisted so the console HAC detail view can render real per-manager
            # best-loss values and the round-loss history chart. Without these the
            # detail route fell back to an always-empty stage scan (managers were
            # falsely reported "complete" and no loss chart ever had data).
            "sub_manager_losses": sub_manager_losses or {},
            "root_loss_history": root_loss_history or [],
        }
        path = self.root / "hac_summary.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, path)
        os.chmod(path, 0o600)

    def read_summary(self) -> dict:
        p = self.root / "hac_summary.json"
        if not p.exists():
            return {}
        return json.loads(p.read_text(encoding="utf-8"))

    def manager_dir(self, manager_id: str) -> Path:
        validate_component_id(manager_id, kind="manager_id")
        d = contained_path(self.root, "sub_managers", manager_id)
        d.mkdir(parents=True, exist_ok=True)
        os.chmod(d, 0o700)
        return d
