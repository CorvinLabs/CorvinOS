"""HACCoordinator — manages sub-manager tree with Backprop Gate (ADR-0028)."""
from __future__ import annotations

import asyncio
import dataclasses
import logging
import time
from pathlib import Path
from typing import Any, Callable

from .manifest import HACManifest, HACStore, SubManagerSpec
from .attribution import compute_root_loss, compute_attribution, check_convergence

log = logging.getLogger(__name__)


class HACCoordinator:
    """Orchestrates multiple sub-managers with composite loss and backprop gate.

    Runs as an asyncio Task inside WorkerServer. Each sub-manager runs as
    a nested asyncio task (using the pipeline coordinator pattern).
    """

    def __init__(
        self,
        *,
        hac_id: str,
        manifest: HACManifest,
        corvin_home: Path,
        runner_fn: Callable,
        audit_emit: Callable,
        gate_queue: asyncio.Queue,
    ):
        self.hac_id = hac_id
        self.manifest = manifest
        self.corvin_home = Path(corvin_home)
        self.runner_fn = runner_fn
        self.audit_emit = audit_emit or (lambda *a, **kw: None)
        self.gate_queue = gate_queue

        self._state = "running"
        self._round = 0
        self._root_loss_history: list[float] = []
        self._manager_results: dict[str, dict] = {}  # manager_id -> last result
        self._manager_budgets: dict[str, dict] = {}  # manager_id -> budget dict
        self._store = HACStore(corvin_home, manifest.tenant_id, hac_id)

        # Distribute initial budget across sub-managers
        for sm in manifest.sub_managers:
            total_iters = int(manifest.budget.get("max_iterations", 100))
            alloc = max(sm.min_iterations, int(total_iters * sm.budget_fraction))
            self._manager_budgets[sm.manager_id] = {
                "max_iterations": alloc,
                "max_wall_clock_s": manifest.budget.get("max_wall_clock_s", 3600),
            }

    async def run(self) -> None:
        self._emit("compute.hac_started", {
            "hac_id": self.hac_id,
            "manager_count": len(self.manifest.sub_managers),
            "backprop_gate": self.manifest.backprop_gate,
            "budget": self.manifest.budget,
        })
        self._store.write_manifest(self.manifest)

        # Initial run: all sub-managers
        managers_to_run = [sm.manager_id for sm in self.manifest.sub_managers]

        for round_n in range(self.manifest.max_backprop_rounds + 1):
            self._round = round_n
            self._emit("compute.hac_round_started", {
                "hac_id": self.hac_id, "round": round_n, "managers_to_run": managers_to_run,
            })

            # Run specified sub-managers in parallel
            await self._run_managers(managers_to_run)

            # Compute root loss
            sub_losses = {
                mid: res.get("best_loss")
                for mid, res in self._manager_results.items()
            }
            root_loss = compute_root_loss(
                sub_losses,
                self.manifest.loss_weights.weights,
                self.manifest.loss_weights.mode,
            )
            self._root_loss_history.append(root_loss)

            attributions = compute_attribution(
                sub_losses,
                self.manifest.loss_weights.weights,
                self.manifest.loss_weights.mode,
            )

            self._emit("compute.hac_root_loss_computed", {
                "hac_id": self.hac_id, "round": round_n, "root_loss": root_loss,
                "sub_losses": {mid: v for mid, v in sub_losses.items() if v is not None},
            })

            self._store.write_summary(
                state="running", round_n=round_n, root_loss=root_loss,
                manager_states={mid: res.get("state", "unknown")
                                for mid, res in self._manager_results.items()},
                attributions=attributions,
            )

            # Check convergence
            if check_convergence(
                self._root_loss_history,
                self.manifest.convergence_epsilon,
                self.manifest.convergence_window,
            ):
                self._state = "converged"
                break

            if round_n >= self.manifest.max_backprop_rounds:
                self._state = "budget"
                break

            # Open backprop gate
            if self.manifest.backprop_gate:
                self._state = "gate_open"
                self._emit("compute.backprop_gate_opened", {
                    "hac_id": self.hac_id, "round": round_n,
                    "attributions": attributions,
                    "budget_remaining": {
                        mid: b for mid, b in self._manager_budgets.items()
                    },
                })
                gate_result = await self._handle_backprop_gate(attributions)
                if gate_result == "abort":
                    self._state = "aborted"
                    break
                managers_to_run = gate_result  # list of manager_ids to re-run
                self._state = "running"
            else:
                # Auto-select highest attribution manager(s) to re-run
                if attributions:
                    highest = max(attributions, key=attributions.get)
                    managers_to_run = [highest]
                else:
                    # All sub-managers failed — no attribution possible
                    self._state = "failed"
                    break

        self._emit("compute.hac_terminal", {
            "hac_id": self.hac_id, "state": self._state,
            "rounds_completed": self._round,
            "final_root_loss": self._root_loss_history[-1] if self._root_loss_history else None,
            "convergence_reason": self._state,
        })
        self._store.write_summary(
            state=self._state, round_n=self._round,
            root_loss=self._root_loss_history[-1] if self._root_loss_history else None,
            manager_states={mid: res.get("state", "unknown")
                            for mid, res in self._manager_results.items()},
            attributions={},
            # Real per-manager best-loss + the round-loss history so the console
            # detail view renders actual data instead of an always-empty stage scan.
            sub_manager_losses={mid: res.get("best_loss")
                                for mid, res in self._manager_results.items()
                                if res.get("best_loss") is not None},
            root_loss_history=list(self._root_loss_history),
        )

    async def _run_managers(self, manager_ids: list[str]) -> None:
        """Run specified sub-managers in parallel."""
        tasks = []
        for manager_id in manager_ids:
            sm = next((m for m in self.manifest.sub_managers if m.manager_id == manager_id), None)
            if sm is None:
                continue
            tasks.append(asyncio.create_task(
                asyncio.to_thread(self._exec_manager, sm),
                name=f"hac_{self.hac_id}_{manager_id}",
            ))
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for manager_id, result in zip(manager_ids, results):
                if isinstance(result, Exception):
                    # gather(return_exceptions=True) hands back the exception as a
                    # value — there is no active exc context here, so log.exception
                    # would print a useless "NoneType: None". Log the exception
                    # object itself (with type) so the real cause is recoverable.
                    log.error(
                        "sub-manager %s failed: %s: %s",
                        manager_id, type(result).__name__, result,
                    )
                    self._manager_results[manager_id] = {"state": "failed", "best_loss": None}
                else:
                    self._manager_results[manager_id] = result

    def _exec_manager(self, sm: SubManagerSpec) -> dict:
        """Execute one sub-manager synchronously in a thread."""
        from ..driver import ComputeRun, ComputeRunSpec
        from ..budget import Budget
        from ..state import new_run_id
        from .. import strategies as strat_pkg

        budget_dict = self._manager_budgets.get(sm.manager_id, {
            "max_iterations": 50, "max_wall_clock_s": 1800,
        })
        budget = Budget.from_dict(budget_dict)

        # For simplicity in Phase 1: run the first stage of the sub-manager
        # as a flat ComputeRun (multi-stage sub-managers would use PipelineCoordinator)
        if not sm.stages:
            return {"state": "failed", "best_loss": None}

        first_stage = sm.stages[0]
        run_id = new_run_id()

        # Resolve the sub-manager strategy against what is actually registered.
        # SubManagerSpec.strategy defaults to "bayesian", but that strategy only
        # auto-registers when sklearn+numpy are installed (see strategies/__init__).
        # On a minimal install (no sklearn) load_strategy("bayesian") raised
        # UnknownStrategy inside the worker thread, which surfaced as "sub-manager
        # X failed" for EVERY sub-manager — HAC mode was 100% broken out of the
        # box. Fall back to the always-present "grid" walker (param_grid input is
        # grid-shaped anyway) so a run degrades gracefully instead of failing;
        # an explicitly-available strategy is still honoured unchanged.
        strategy_name = sm.strategy
        try:
            if strategy_name not in strat_pkg.available_strategies():
                log.warning(
                    "HAC sub-manager %s requested strategy %r which is not "
                    "installed (available: %s) — falling back to 'grid'",
                    sm.manager_id, strategy_name, strat_pkg.available_strategies(),
                )
                strategy_name = "grid"
        except Exception:  # noqa: BLE001 — never let resolution itself fail the run
            strategy_name = "grid"

        spec = ComputeRunSpec(
            tenant_id=self.manifest.tenant_id,
            tool_name=str(first_stage.get("tool_name", "")),
            param_grid=dict(first_stage.get("param_grid", {})),
            loss_metric="loss",
            strategy_name=strategy_name,
            budget=budget,
            minimise=True,
            data_handle=None,
            seed=None,
            top_k_size=5,
            sensitive_fields=[],
        )
        run = ComputeRun(
            spec,
            corvin_home=self.corvin_home,
            runner_fn=self.runner_fn,
            strategy_factory=strat_pkg.load_strategy,
            run_id=run_id,
            audit_emit=self.audit_emit,
        )
        rec = run.run()
        # RunRecord has no best_params field (only best_loss/best_iter) — the
        # public run surface deliberately exposes iteration params only via
        # RunStore, not on the aggregate record. Look up the winning
        # iteration's real params there instead of a non-existent attribute
        # (this raised AttributeError on every HAC sub-manager run — no HAC
        # job had ever completed successfully).
        best_params: dict[str, Any] = {}
        if rec.best_iter is not None:
            for it in run.store.read_iterations(run_id):
                if it.iter == rec.best_iter:
                    best_params = dict(it.params)
                    break
        return {
            "state": rec.state,
            "best_loss": rec.best_loss,
            "best_params": best_params,
            "total_iterations": rec.total_iterations,
            "run_id": run_id,
        }

    async def _handle_backprop_gate(self, attributions: dict) -> list[str] | str:
        """Park at Backprop Gate. Return list of manager_ids to re-run, or 'abort'."""
        timeout = self.manifest.backprop_gate_timeout_s

        while True:
            try:
                raw_action = await asyncio.wait_for(self.gate_queue.get(), timeout=timeout)
            except asyncio.TimeoutError:
                # Auto-resume: re-run the highest-attribution manager
                if attributions:
                    highest = max(attributions, key=attributions.get)
                    return [highest]
                return [sm.manager_id for sm in self.manifest.sub_managers[:1]]

            action_type = raw_action.get("action_type", "")
            payload = raw_action.get("payload", {})

            if action_type == "resume":
                # Re-run the highest-attribution managers or those specified
                re_run = payload.get("re_run", [])
                if not re_run and attributions:
                    highest = max(attributions, key=attributions.get)
                    re_run = [highest]
                return re_run or [sm.manager_id for sm in self.manifest.sub_managers]

            elif action_type == "abort":
                return "abort"

            elif action_type == "reallocate_budget":
                from_mid = payload.get("from_manager")
                to_mid = payload.get("to_manager")
                fraction = float(payload.get("fraction", 0.5))
                fraction = min(max(fraction, 0.0), self.manifest.max_transfer_fraction)
                if (
                    from_mid and to_mid
                    and from_mid in self._manager_budgets
                    and to_mid in self._manager_budgets
                ):
                    from_budget = self._manager_budgets[from_mid]
                    transfer = int(from_budget.get("max_iterations", 0) * fraction)
                    from_budget["max_iterations"] = max(0, from_budget["max_iterations"] - transfer)
                    self._manager_budgets[to_mid]["max_iterations"] += transfer
                    self._emit("compute.hac_budget_reallocated", {
                        "hac_id": self.hac_id, "from_manager": from_mid,
                        "to_manager": to_mid, "fraction": fraction, "trigger": "user",
                    })

            elif action_type == "forge_noted":
                # LLM forged a tool externally — just audit it
                self._emit("compute.pipeline_tool_forged", {
                    "pipeline_id": self.hac_id,
                    "gate_index": self._round,
                    "tool_name": payload.get("tool_name", ""),
                    "forge_run_id": payload.get("forge_run_id", ""),
                })

    def get_status_dict(self) -> dict:
        return {
            "hac_id": self.hac_id,
            "state": self._state,
            "round": self._round,
            "root_loss_history": self._root_loss_history,
            "manager_states": {
                mid: res.get("state", "unknown")
                for mid, res in self._manager_results.items()
            },
            "best_losses": {
                mid: res.get("best_loss")
                for mid, res in self._manager_results.items()
            },
        }

    def get_result_dict(self) -> dict:
        return {
            "hac_id": self.hac_id,
            "state": self._state,
            "rounds_completed": self._round,
            "final_root_loss": self._root_loss_history[-1] if self._root_loss_history else None,
            "root_loss_history": self._root_loss_history,
            "managers": {
                mid: {
                    "best_loss": res.get("best_loss"),
                    "best_params": res.get("best_params", {}),
                    "state": res.get("state"),
                }
                for mid, res in self._manager_results.items()
            },
        }

    def request_abort(self) -> None:
        try:
            self.gate_queue.put_nowait({"action_type": "abort", "payload": {}})
        except Exception:
            pass
        self._state = "aborted"

    def _emit(self, event: str, details: dict) -> None:
        try:
            self.audit_emit(event, "INFO", details)
        except Exception:
            pass
