"""ADR-0214: Real Agentic Compute Engines for the EngineRegistry.

Replaces the Phase-2 placeholders with executable engines:

- TieredDelegationEngine — the TDE itself: parallel batches via
  AdaptiveDelegationExecutor, three-gate delegation, WorkerIPC backends.
- ClaudeCodeLocalEngine  — sequential local execution with full context
  (the "no delegation" baseline; every step runs in-process).
- AcsEngineBridge        — bridges to the existing ACS runtime
  (operator/bridges/shared/acs_engine_adapter.run_acs_workflow). When the
  ACS stack is unavailable it returns an explicit error result — it never
  fakes success.

All engines implement: ``await execute(analysis, context, **kwargs) -> dict``
with the shape ``{"engine", "success", "results"| "error", ...}``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Callable, Optional

try:
    from initial_analysis import InitialAnalysisRequest, Step
except ImportError:  # pragma: no cover
    from ..initial_analysis import InitialAnalysisRequest, Step  # type: ignore

from .adaptive_delegation_executor import (
    AdaptiveDelegationExecutor,
    BudgetEnvelope,
    ExecutionError,
    StepResult,
)
from .l34_delegation_gate import L34DelegationGate
from .loss_profile_tracker import get_session_tracker
from .worker_ipc import get_worker_ipc

_logger = logging.getLogger(__name__)

_LOCAL_STEP_TIMEOUT_S = 120


def _bridges_shared_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "bridges" / "shared"


def _ensure_bridges_on_path() -> None:
    shared = _bridges_shared_dir()
    if shared.is_dir() and str(shared) not in sys.path:
        sys.path.insert(0, str(shared))


async def default_local_step_executor(
    step: Step, statement: dict[str, Any], *, proc_holder: Optional[Any] = None,
) -> Any:
    """Default LOCAL step execution: tool-less one-shot LLM with FULL context.

    HONEST BOUNDARY NOTE (round-2 review): in the default configuration this
    "local" executor calls the same cloud model through the same claude CLI
    as the delegated workers — the L34 gate therefore enforces a
    CONTEXT/SANITIZATION boundary (what a delegated worker may see), not a
    network boundary. It becomes a true network boundary once delegation
    targets remote A2A instances (Phase 3). Embedders that need a hard
    boundary inject a genuinely local ``local_step_executor`` (e.g. Hermes).

    ``proc_holder`` (a ``tde.worker_ipc.ProcHolder``), when given, is
    populated with the live subprocess so a cancelling caller — a parallel
    batch in ``AdaptiveDelegationExecutor.execute()`` unwinding on client
    disconnect — can kill it instead of leaving it running to its own
    timeout (round-4 follow-up).
    """
    _ensure_bridges_on_path()
    import helper_model  # noqa: PLC0415

    step_desc = f" — {step.description}" if step.description else ""
    prompt = (
        "You are executing ONE step of a plan locally with full context.\n"
        "Respond in English. Ignore any repository, project or user context "
        "of the machine you run on; your ONLY task is the step below.\n"
        f"Step: {step.step}. [{step.action}]{step_desc}\n"
        f"Context:\n{json.dumps(statement, default=str, indent=2)[:20000]}\n\n"
        'Execute the step and return ONLY a JSON object on one line: {"output": <result>}\n'
    )

    def _run() -> Any:
        from .worker_ipc import (  # noqa: PLC0415 — avoid import cycle
            parse_worker_output,
            run_one_shot,
        )

        cmd = [
            helper_model.resolve_claude_bin(), "-p", prompt,
            "--max-turns", "1",
            "--output-format", "text",
            "--disallowedTools", "*",
            *helper_model.claude_args(helper_model.SITE_TDE_WORKER),
        ]
        rc, stdout, stderr = run_one_shot(cmd, _LOCAL_STEP_TIMEOUT_S, proc_holder=proc_holder)
        if rc != 0:
            raise RuntimeError(f"local step exit {rc}: {stderr.strip()[:300]}")
        return parse_worker_output(stdout)

    return await asyncio.to_thread(_run)


def _summarize(results: list[StepResult]) -> dict[str, Any]:
    return {
        "step_count": len(results),
        "succeeded": sum(1 for r in results if r.success),
        "failed": sum(1 for r in results if not r.success),
        "delegated": sum(1 for r in results if r.was_delegated),
        "local": sum(1 for r in results if not r.was_delegated),
        "total_duration_ms": sum(r.duration_ms for r in results),
    }


class TieredDelegationEngine:
    """ADR-0214 TDE: parallel, three-gate, loss-aware execution."""

    name = "tiered_delegation"

    def __init__(
        self,
        *,
        l34_classifier: Optional[Any] = None,
        local_step_executor: Optional[Callable[[Step, dict[str, Any]], Any]] = None,
        real_ipc: bool = False,
        max_classification: str = "INTERNAL",
    ):
        self.l34_classifier = l34_classifier
        self.local_step_executor = local_step_executor or default_local_step_executor
        self.real_ipc = real_ipc
        self.max_classification = max_classification

    async def execute(
        self,
        plan: Any,
        context: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Execute an InitialAnalysisRequest (or bare GlobalPlan) via TDE."""
        analysis: Optional[InitialAnalysisRequest] = None
        if isinstance(plan, InitialAnalysisRequest):
            analysis = plan
            global_plan = plan.global_plan
        elif hasattr(plan, "steps"):
            global_plan = plan
        else:
            return {"engine": self.name, "success": False,
                    "error": "TDE requires an InitialAnalysisRequest or GlobalPlan"}

        statement = context.get("statement") or {}
        if not isinstance(statement, dict):
            statement = {"statement": statement}

        budget_tokens = kwargs.get("budget_tokens") or max(
            global_plan.estimated_tokens * 3, 30_000
        )
        # ADR-0215 F4: propagate the caller's session_key so this tracker is
        # the SAME instance SendIntegration's RobustEngineDetector already
        # uses for this session, not a different (or the old global-default)
        # one — see loss_profile_tracker.py module docstring.
        tracker = get_session_tracker(session_key=str(kwargs.get("session_key") or "default"))
        if tracker.current_model_id == "default":
            try:
                _ensure_bridges_on_path()
                import helper_model  # noqa: PLC0415

                worker_model = helper_model.resolve_helper_model(helper_model.SITE_TDE_WORKER)
                if worker_model:
                    tracker.set_model(worker_model)
            except Exception:  # noqa: BLE001 — model tagging is best-effort
                pass
        try:
            executor = AdaptiveDelegationExecutor(
                global_plan,
                L34DelegationGate(l34_classifier=self.l34_classifier),
                tracker,
                worker_ipc=kwargs.get("worker_ipc") or get_worker_ipc(real=self.real_ipc),
                budget=BudgetEnvelope(max_tokens=int(budget_tokens)),
                complexity=(analysis.classification.complexity if analysis else "moderate"),
                max_classification=self.max_classification,
                use_semantic_judge=kwargs.get("use_semantic_judge", self.real_ipc),
                run_id=str(kwargs.get("run_id") or ""),
            )
        except ExecutionError as exc:
            # LM-emitted plans are routinely slightly malformed (0-based step
            # numbers, gaps) — that must yield an explicit error result, not
            # an unhandled crash of the whole send() turn (round-2 finding).
            return {"engine": self.name, "success": False,
                    "error": f"invalid plan: {exc}"}

        start = time.time()
        results = await executor.execute(statement, analysis, self.local_step_executor)
        summary = _summarize(results)
        summary["wall_time_ms"] = int((time.time() - start) * 1000)

        return {
            "engine": self.name,
            "success": all(r.success for r in results) and bool(results),
            "results": results,
            "summary": summary,
        }


class ClaudeCodeLocalEngine:
    """Sequential local execution with full context (no delegation)."""

    name = "claude_code"

    def __init__(
        self,
        local_step_executor: Optional[Callable[[Step, dict[str, Any]], Any]] = None,
    ):
        self.local_step_executor = local_step_executor or default_local_step_executor

    async def execute(
        self,
        plan: Any,
        context: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        if isinstance(plan, InitialAnalysisRequest):
            global_plan = plan.global_plan
        elif hasattr(plan, "steps"):
            global_plan = plan
        else:
            return {"engine": self.name, "success": False,
                    "error": "claude_code engine requires an InitialAnalysisRequest or GlobalPlan"}

        statement = context.get("statement") or {}
        if not isinstance(statement, dict):
            statement = {"statement": statement}

        results: list[StepResult] = []
        start = time.time()
        for step in sorted(global_plan.steps, key=lambda s: s.step):
            t0 = time.time()
            try:
                output = await self.local_step_executor(step, statement)
                results.append(StepResult(
                    step_num=step.step, action=step.action, success=True,
                    output=output, duration_ms=int((time.time() - t0) * 1000),
                ))
            except Exception as e:
                results.append(StepResult(
                    step_num=step.step, action=step.action, success=False,
                    error=str(e), duration_ms=int((time.time() - t0) * 1000),
                ))
                break  # sequential engine: fail-fast

        summary = _summarize(results)
        summary["wall_time_ms"] = int((time.time() - start) * 1000)
        return {
            "engine": self.name,
            "success": all(r.success for r in results) and bool(results),
            "results": results,
            "summary": summary,
        }


class AcsEngineBridge:
    """Bridge to the existing ACS Manager/Worker runtime (ADR-0104).

    Delegates the ORIGINAL task text to run_acs_workflow. Requires the
    bridges stack; returns an explicit error when unavailable.
    """

    name = "acs"

    async def execute(
        self,
        plan: Any,
        context: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        task_text = kwargs.get("task_text") or context.get("task_text") or ""
        if not task_text:
            return {"engine": self.name, "success": False,
                    "error": "ACS bridge needs task_text (original task)"}

        _ensure_bridges_on_path()
        try:
            import acs_engine_adapter  # noqa: PLC0415
        except Exception as exc:
            return {"engine": self.name, "success": False,
                    "error": f"ACS runtime unavailable: {exc}"}

        def _run() -> dict[str, Any]:
            return acs_engine_adapter.run_acs_workflow(task_text, **kwargs.get("acs_kwargs", {}))

        try:
            result = await asyncio.to_thread(_run)
            return {"engine": self.name, "success": True, "results": result}
        except Exception as exc:
            return {"engine": self.name, "success": False, "error": str(exc)}
