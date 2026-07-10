"""PipelineCoordinator — runs a pipeline as an asyncio Task (ADR-0027).

The coordinator manages sequential stage execution with optional steering
gates between stages. At each gate the LLM (or operator) can:

  * resume  — continue to the next stage as planned
  * abort   — terminate the pipeline
  * add_stage — insert a new stage after the current position
  * steer   — override next stage's param_grid / strategy / budget
  * forge_noted — record that a forge-tool was created at this gate

If the gate times out (``steering_gate_timeout_s``) the pipeline auto-resumes
to avoid blocking indefinitely.

The coordinator is intentionally free of any ``anthropic`` import; LLM
interaction happens outside this class via gate_queue messages.
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
import re
import time
from pathlib import Path
from typing import Any, Callable

from .manifest import PipelineManifest, PipelineStore, StageSpec

log = logging.getLogger(__name__)


class PipelineCoordinator:
    """Manages sequential stage execution with forge gates.

    Runs as a background asyncio Task inside the WorkerServer.
    Gate actions are delivered via ``gate_queue`` (an ``asyncio.Queue``
    holding plain dicts ``{action_type, payload}``).
    """

    def __init__(
        self,
        *,
        pipeline_id: str,
        manifest: PipelineManifest,
        corvin_home: Path,
        runner_fn: Callable,
        audit_emit: Callable,
        gate_queue: asyncio.Queue,
    ) -> None:
        self.pipeline_id = pipeline_id
        self.manifest = manifest
        self.corvin_home = Path(corvin_home)
        self.runner_fn = runner_fn
        self.audit_emit = audit_emit or (lambda *a, **kw: None)
        self.gate_queue = gate_queue

        self._stages: list[StageSpec] = list(manifest.stages)
        self._current_idx: int = 0
        # stage_id -> {state, best_loss, best_params, artifact_dir, run_id}
        self._completed: dict[str, dict] = {}
        self._state: str = "running"
        self._store = PipelineStore(corvin_home, manifest.tenant_id, pipeline_id)
        self._gate_index: int = 0

    # -- public entry point -------------------------------------------------------

    async def run(self) -> None:
        """Main coroutine — called via ``asyncio.create_task()``."""
        self._emit("compute.pipeline_started", {
            "pipeline_id": self.pipeline_id,
            "stage_count": len(self._stages),
            "steering_gate": self.manifest.steering_gate,
            "budget": self.manifest.budget,
        })
        self._store.write_manifest(self.manifest)

        while self._current_idx < len(self._stages):
            stage = self._stages[self._current_idx]

            # Resolve $refs in stage inputs before execution
            resolved_stage = self._resolve_refs(stage)

            # Execute stage in a thread so the event loop stays responsive
            try:
                result = await asyncio.to_thread(self._exec_stage, resolved_stage)
            except Exception as exc:
                log.exception("stage %s failed: %s", stage.stage_id, exc)
                self._state = "failed"
                self._store.write_summary(
                    state="failed",
                    current_stage_id=stage.stage_id,
                    completed_stages=list(self._completed.keys()),
                    best_losses={
                        k: v.get("best_loss") for k, v in self._completed.items()
                    },
                )
                self._emit("compute.pipeline_terminal", {
                    "pipeline_id": self.pipeline_id,
                    "state": "failed",
                    "stages_completed": len(self._completed),
                    "failed_stage_id": stage.stage_id,
                    "error": str(exc),
                })
                return

            self._completed[stage.stage_id] = result

            self._emit("compute.stage_completed", {
                "pipeline_id": self.pipeline_id,
                "stage_id": stage.stage_id,
                "best_loss": result.get("best_loss"),
                "total_iterations": result.get("total_iterations", 0),
                "tool_name": stage.tool_name,
            })

            # Open a gate between stages (but not after the very last stage)
            is_last = self._current_idx == len(self._stages) - 1
            if self.manifest.steering_gate and not is_last:
                self._state = "gate_open"
                next_stage_id = (
                    self._stages[self._current_idx + 1].stage_id
                    if self._current_idx + 1 < len(self._stages)
                    else None
                )
                self._store.write_summary(
                    state="gate_open",
                    current_stage_id=stage.stage_id,
                    completed_stages=list(self._completed.keys()),
                    best_losses={
                        k: v.get("best_loss") for k, v in self._completed.items()
                    },
                )
                self._emit("compute.pipeline_gate_opened", {
                    "pipeline_id": self.pipeline_id,
                    "gate_index": self._gate_index,
                    "next_stage_id": next_stage_id,
                    "budget_remaining": self.manifest.budget,
                })

                gate_ok = await self._handle_gate()
                if not gate_ok:
                    return  # aborted at gate
                self._gate_index += 1

            self._current_idx += 1

        # All stages done
        self._state = "converged"
        self._store.write_summary(
            state="converged",
            current_stage_id=None,
            completed_stages=list(self._completed.keys()),
            best_losses={k: v.get("best_loss") for k, v in self._completed.items()},
        )
        self._emit("compute.pipeline_terminal", {
            "pipeline_id": self.pipeline_id,
            "state": "converged",
            "stages_completed": len(self._completed),
            "total_wall_s": 0.0,
        })

    # -- gate handling ------------------------------------------------------------

    async def _handle_gate(self) -> bool:
        """Park at gate until an action arrives or the timeout fires.

        Returns True to continue, False to abort.
        """
        timeout = self.manifest.steering_gate_timeout_s

        while True:
            try:
                raw_action = await asyncio.wait_for(
                    self.gate_queue.get(), timeout=timeout
                )
            except asyncio.TimeoutError:
                log.info(
                    "pipeline %s gate %d timed out after %.0fs — auto-resuming",
                    self.pipeline_id,
                    self._gate_index,
                    timeout,
                )
                self._state = "running"
                return True

            action_type = raw_action.get("action_type", "")
            payload = raw_action.get("payload", {})

            if action_type == "resume":
                self._state = "running"
                return True

            elif action_type == "abort":
                self._state = "aborted"
                self._store.write_summary(
                    state="aborted",
                    current_stage_id=None,
                    completed_stages=list(self._completed.keys()),
                    best_losses={
                        k: v.get("best_loss") for k, v in self._completed.items()
                    },
                )
                self._emit("compute.pipeline_terminal", {
                    "pipeline_id": self.pipeline_id,
                    "state": "aborted",
                    "stages_completed": len(self._completed),
                    "gate_index": self._gate_index,
                })
                return False

            elif action_type == "add_stage":
                stage_dict = payload.get("stage", {})
                if stage_dict:
                    new_stage = StageSpec.from_dict(stage_dict)
                    insert_pos = self._current_idx + 1
                    self._stages.insert(insert_pos, new_stage)
                    log.info(
                        "pipeline %s: added stage %r at position %d",
                        self.pipeline_id,
                        new_stage.stage_id,
                        insert_pos,
                    )

            elif action_type == "steer":
                overrides = payload.get("overrides", {})
                next_idx = self._current_idx + 1
                if next_idx < len(self._stages):
                    stage = self._stages[next_idx]
                    if "param_grid" in overrides:
                        stage.param_grid = dict(overrides["param_grid"])
                    if "strategy" in overrides:
                        stage.strategy = str(overrides["strategy"])
                    if "budget" in overrides:
                        stage.budget = dict(overrides["budget"])
                    log.info(
                        "pipeline %s: steered stage %r with keys %s",
                        self.pipeline_id,
                        stage.stage_id,
                        list(overrides.keys()),
                    )

            elif action_type == "forge_noted":
                tool_name = payload.get("tool_name", "unknown")
                self.manifest.forged_tools.append({
                    "gate_index": self._gate_index,
                    "tool_name": tool_name,
                })
                self._emit("compute.pipeline_tool_forged", {
                    "pipeline_id": self.pipeline_id,
                    "gate_index": self._gate_index,
                    "tool_name": tool_name,
                    "forge_run_id": payload.get("forge_run_id", ""),
                })

            else:
                log.warning(
                    "pipeline %s: unknown gate action %r — ignoring",
                    self.pipeline_id,
                    action_type,
                )
            # Continue waiting for a terminal gate action (resume / abort)

    # -- stage execution ----------------------------------------------------------

    def _exec_stage(self, stage: StageSpec) -> dict:
        """Execute one stage (blocking, called via asyncio.to_thread)."""
        # Lazy imports to avoid circular dependency at module level
        from ..driver import ComputeRun, ComputeRunSpec
        from ..budget import Budget
        from ..state import new_run_id
        from .. import strategies as strat_pkg

        artifacts_dir = self._store.ensure_stage_dir(stage.stage_id)

        # Merge pipeline-level budget with stage-level overrides
        combined_budget_dict = dict(self.manifest.budget)
        combined_budget_dict.update(stage.budget)
        budget = Budget.from_dict(combined_budget_dict)

        # Pass resolved input paths as prefixed extra params
        param_grid = dict(stage.param_grid)
        for input_key, input_val in stage.inputs.items():
            param_grid[f"_input_{input_key}"] = input_val

        run_id = new_run_id()
        spec = ComputeRunSpec(
            tenant_id=self.manifest.tenant_id,
            tool_name=stage.tool_name,
            param_grid=param_grid,
            loss_metric="loss",
            strategy_name=stage.strategy,
            budget=budget,
            minimise=True,
            data_handle=None,
            seed=None,
            top_k_size=5,
            sensitive_fields=list(stage.sensitive_fields),
        )

        run = ComputeRun(
            spec,
            corvin_home=self.corvin_home,
            runner_fn=self.runner_fn,
            strategy_factory=strat_pkg.load_strategy,
            run_id=run_id,
            audit_emit=self.audit_emit,
        )
        record = run.run()

        summary = record.summary or {}
        best_params_raw = summary.get("top_k", [{}])
        # The driver NEVER writes real params into the summary — top_k entries are
        # {iter, loss, param_fingerprint}, and there is no summary["best_params"].
        # Resolving $sid.best_params from the summary therefore yielded {} or a
        # fingerprint dict, so a downstream stage steering on the prior stage's
        # winning parameters got garbage. HAC's _exec_manager was fixed (a64a50d)
        # to read the winning iteration's REAL params from the RunStore; the
        # pipeline coordinator had the identical defect. Look up the winning
        # iteration's actual params the same way.
        best_params: dict[str, Any] = {}
        if record.best_iter is not None:
            try:
                for it in run.store.read_iterations(run_id):
                    if it.iter == record.best_iter:
                        best_params = dict(it.params)
                        break
            except Exception:  # noqa: BLE001 — never fail a stage on param lookup
                best_params = {}

        return {
            "state": record.state,
            "best_loss": record.best_loss,
            "best_params": best_params,
            "total_iterations": record.total_iterations,
            "artifact_dir": str(artifacts_dir),
            "run_id": run_id,
            "top_k": best_params_raw,
        }

    # -- $ref resolution ----------------------------------------------------------

    def _resolve_refs(self, stage: StageSpec) -> StageSpec:
        """Return a copy of *stage* with ``$ref`` values in inputs resolved."""
        resolved_inputs = {}
        for key, value in stage.inputs.items():
            if isinstance(value, str) and value.startswith("$"):
                resolved_inputs[key] = self._resolve_one_ref(value)
            else:
                resolved_inputs[key] = value
        return dataclasses.replace(stage, inputs=resolved_inputs)

    def _resolve_one_ref(self, ref: str) -> Any:
        """Resolve a single ``$stage_id.field`` reference.

        Supported paths:
          ``$sid.artifacts/<filename>`` → absolute path string
          ``$sid.best_params``          → dict
          ``$sid.best_loss``            → float | None
          ``$sid.top_k``               → list
        """
        m = re.match(r"^\$([A-Za-z0-9_-]+)\.(.+)$", ref)
        if not m:
            return ref  # not a recognised ref syntax
        sid, path = m.group(1), m.group(2)
        completed = self._completed.get(sid)
        if completed is None:
            log.warning(
                "pipeline %s: ref to incomplete stage %r in %r — returning raw",
                self.pipeline_id,
                sid,
                ref,
            )
            return ref
        if path.startswith("artifacts/"):
            fname = path[len("artifacts/"):]
            return str(self._store.stage_artifacts_dir(sid) / fname)
        if path == "best_params":
            return completed.get("best_params", {})
        if path == "best_loss":
            return completed.get("best_loss")
        if path == "top_k":
            return completed.get("top_k", [])
        log.warning(
            "pipeline %s: unknown ref path %r in %r — returning raw",
            self.pipeline_id,
            path,
            ref,
        )
        return ref

    # -- status / result helpers --------------------------------------------------

    def get_status_dict(self) -> dict:
        return {
            "pipeline_id": self.pipeline_id,
            "state": self._state,
            "current_stage_idx": self._current_idx,
            "current_stage_id": (
                self._stages[self._current_idx].stage_id
                if self._current_idx < len(self._stages)
                else None
            ),
            "completed_stages": list(self._completed.keys()),
            "best_losses": {
                k: v.get("best_loss") for k, v in self._completed.items()
            },
            "gate_index": self._gate_index,
            "total_stages": len(self._stages),
        }

    def get_result_dict(self) -> dict:
        return {
            "pipeline_id": self.pipeline_id,
            "state": self._state,
            "stages_completed": len(self._completed),
            "stages": {
                sid: {
                    "best_loss": v.get("best_loss"),
                    "best_params": v.get("best_params", {}),
                    "total_iterations": v.get("total_iterations", 0),
                    "artifact_dir": v.get("artifact_dir", ""),
                    "run_id": v.get("run_id", ""),
                }
                for sid, v in self._completed.items()
            },
            "forged_tools": list(self.manifest.forged_tools),
        }

    def request_abort(self) -> None:
        """Thread-safe abort request — enqueues an abort action."""
        try:
            self.gate_queue.put_nowait({"action_type": "abort", "payload": {}})
        except asyncio.QueueFull:
            pass
        self._state = "aborted"

    # -- audit helper -------------------------------------------------------------

    def _emit(self, event: str, details: dict) -> None:
        try:
            self.audit_emit(event, "INFO", details)
        except Exception:  # noqa: BLE001
            pass
