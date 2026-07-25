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
import os
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


def _tde_reference_model() -> str:
    """ADR-0222 F1: the model the SHADOW REFERENCE runs on, to fix the
    Haiku-vs-Haiku counterfactual. Default empty = OFF (shadow uses the worker
    model, legacy behaviour). Set CORVIN_TDE_REFERENCE_MODEL to a STRONGER model
    (ideally the user's tier, e.g. claude-sonnet-5) to measure the real drop:
    'did the cheap worker match the strong reference?'. This costs a stronger
    call per shadow (bounded by the 5-25% sample) — the honest price of the
    measurement; if it can't be afforded, that is itself the answer (ADR-0222)."""
    import os as _os  # noqa: PLC0415
    return _os.environ.get("CORVIN_TDE_REFERENCE_MODEL", "").strip()


def _tde_explore_models() -> list[str]:
    """ADR-0222 F4: candidate WORKER arms to explore, comma-separated in
    CORVIN_TDE_EXPLORE_MODELS (e.g. 'claude-haiku-4-5,qwen3'). Default empty =
    OFF (no exploration). Each shadow measurement runs ONE rotating candidate on
    the step and logs a measured (action, candidate) loss entry, so the log
    accrues real multi-arm evidence instead of only the worker's arm. Only takes
    effect when CORVIN_TDE_REFERENCE_MODEL is also set (candidates are judged
    against that strong yardstick)."""
    import os as _os  # noqa: PLC0415
    raw = _os.environ.get("CORVIN_TDE_EXPLORE_MODELS", "").strip()
    return [m.strip() for m in raw.split(",") if m.strip()]


async def default_local_step_executor(
    step: Step, statement: dict[str, Any], *, proc_holder: Optional[Any] = None,
    model_override: Optional[str] = None,
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

    # ADR-0222 F1: a model_override (used by the shadow REFERENCE run) pins a
    # stronger model directly; otherwise the normal worker-model resolution.
    if model_override:
        model_args = ["--model", model_override]
        model_tag = model_override
    else:
        model_args = helper_model.claude_args(helper_model.SITE_TDE_WORKER)
        model_tag = ""
        for i, a in enumerate(model_args):
            if a == "--model" and i + 1 < len(model_args):
                model_tag = model_args[i + 1]
                break

    def _run() -> Any:
        from .worker_ipc import (  # noqa: PLC0415 — avoid import cycle
            LocalResult,
            parse_cli_envelope,
            parse_worker_output,
            run_one_shot,
        )

        cmd = [
            helper_model.resolve_claude_bin(), "-p", prompt,
            "--max-turns", "1",
            # ADR-0219 R1: json (was text) so the LOCAL/non-delegated baseline
            # also captures real token usage — without it the delegated-vs-local
            # break-even the whole ADR-0218 vision needs is half-blind. Same
            # parse_cli_envelope + is_error handling as the delegated worker.
            "--output-format", "json",
            "--disallowedTools", "*",
            *model_args,
        ]
        rc, stdout, stderr = run_one_shot(cmd, _LOCAL_STEP_TIMEOUT_S, proc_holder=proc_holder)
        if rc != 0:
            raise RuntimeError(f"local step exit {rc}: {stderr.strip()[:300]}")
        result_text, usage, env_error = parse_cli_envelope(stdout, model=model_tag)
        if env_error:
            # A soft failure (is_error / non-success subtype at rc=0) — same
            # honesty contract as the delegated path: never treat the error
            # text as a successful result.
            raise RuntimeError(f"local step error: {env_error}")
        return LocalResult(parse_worker_output(result_text), usage)

    return await asyncio.to_thread(_run)


def _license_corvin_home() -> Path:
    """Resolve corvin_home the same way the other quota chokepoints do
    (forge.paths → env → ~/.corvin), so TDE charges the SAME counter file
    as ACS / compute runs."""
    _op_root = Path(__file__).resolve().parents[2]  # operator/
    for _p in (str(_op_root), str(_op_root / "forge")):
        if _p not in sys.path:
            sys.path.insert(0, _p)
    try:
        from forge import paths as _fp  # type: ignore  # noqa: PLC0415
        return _fp.corvin_home()
    except ImportError:
        env = os.environ.get("CORVIN_HOME")
        return Path(env) if env else Path.home() / ".corvin"


def _enforce_tde_compute_quota(
    run_id: str,
) -> tuple[Optional[dict[str, Any]], Optional[dict[str, Any]]]:
    """Charge one unit of the shared daily agentic-compute pool for this run.

    TDE shares ONE pool (``compute_units_per_day``, counter file
    ``global/license/compute_quota.json``) with ACS workflows and compute
    (grid-search) runs, so a free-tier operator gets 10 agentic turns/day
    across ALL engines combined (maintainer decision 2026-07-24). Fail
    contract mirrors acs_engine_adapter._enforce_acs_compute_quota:
    missing/shadowed license module → deny (fail-closed, a removed module
    must not buy unmetered compute); over-quota → deny; transient I/O is
    handled inside increment_and_check per LIC-2.

    Returns a ``(denied, quota_info)`` pair: ``denied`` is an error-result
    dict to surface (``quota_info`` is None alongside it), or None to
    proceed. On success ``quota_info`` is ``{"quota_used_today": int,
    "quota_limit": int | None}`` — read via the SAME compute_quota /
    license.validator chokepoint that just charged the unit, no new call
    path — for the badge to render "N/10 heute" (limit None = unlimited,
    omitted in the UI rather than shown as a fabricated number).
    """
    _op_root = str(Path(__file__).resolve().parents[2])
    try:
        if _op_root not in sys.path:
            sys.path.insert(0, _op_root)
        from license.compute_quota import get_today_count as _cq_count  # type: ignore  # noqa: PLC0415
        from license.compute_quota import increment_and_check as _cq_inc  # type: ignore  # noqa: PLC0415
        from license.limits import LicenseLimitError as _CQErr  # type: ignore  # noqa: PLC0415
        from license.validator import get_limit as _cq_limit  # type: ignore  # noqa: PLC0415
        from license.validator import load_license_from_env as _load_lic  # type: ignore  # noqa: PLC0415
        # Load the license so limits reflect the real tier, not FREE_TIER
        # defaults (idempotent via _LICENSE_INITIALIZED, same as ACS).
        _load_lic()
    except ImportError:
        return ({"engine": "tiered_delegation", "success": False,
                "reason": "enforcement_unavailable",
                "error": "compute quota enforcement unavailable (fail-closed)"}, None)
    try:
        _cq_inc(_license_corvin_home(), channel="tde",
                chat_key=f"tde:{run_id or 'run'}")
    except _CQErr as exc:
        return ({"engine": "tiered_delegation", "success": False,
                "reason": "quota_exhausted",
                "error": f"compute_units_per_day exceeded: {exc}"}, None)
    quota_info = {
        "quota_used_today": _cq_count(_license_corvin_home()),
        "quota_limit": _cq_limit("compute_units_per_day"),
    }
    return None, quota_info


def _refund_tde_compute_unit() -> None:
    """Give back the unit charged for a run that never executed a step
    (invalid plan rejected at executor construction). Best-effort."""
    try:
        from license.compute_quota import refund_one as _refund  # type: ignore  # noqa: PLC0415
        _refund(_license_corvin_home())
    except Exception:  # noqa: BLE001 — refund is best-effort by contract
        pass


def _summarize(results: list[StepResult]) -> dict[str, Any]:
    # ADR-0215 honesty fix (2026-07-24): a concurrent session added a
    # `token_savings_pct` UI field sourced via `summary.get('token_savings_pct',
    # 0)` in chat_runtime.py — but this function never set that key, so the
    # field was structurally always 0, silently displayed as a real metric.
    # ADR-0218 Phase 0 (2026-07-24) then added real per-step token usage:
    # DELEGATED workers now run --output-format json and their usage is captured
    # (worker_ipc.parse_cli_envelope) and aggregated below. LOCAL steps still use
    # --output-format text and carry no usage, so they are simply omitted from
    # the token totals. `token_savings_pct` stays `None` regardless — a savings
    # PERCENTAGE needs a counterfactual baseline (Phase-1 measurement), not a
    # single run, and must never be a silently-defaulted 0 that reads as a real
    # "0% savings". Latency below is the other genuinely-measured signal.
    delegated_durations = [r.duration_ms for r in results if r.was_delegated and r.success]
    local_durations = [r.duration_ms for r in results if not r.was_delegated and r.success]
    avg_delegated = (
        sum(delegated_durations) / len(delegated_durations) if delegated_durations else None
    )
    avg_local = sum(local_durations) / len(local_durations) if local_durations else None
    latency_delta_pct = None
    if avg_delegated is not None and avg_local:
        latency_delta_pct = round(100.0 * (avg_local - avg_delegated) / avg_local, 1)

    # ADR-0218 Phase 0: aggregate REAL per-step token usage now that workers run
    # with --output-format json (worker_ipc.parse_cli_envelope). Only delegated
    # steps that actually returned a usage block count; steps without one (local
    # execution, mock IPC, or a fail-soft text envelope) are simply absent, never
    # fabricated as zero. `token_usage_instrumented` flips True the moment ANY
    # real usage was captured, so the frontend can tell a measured run from a
    # not-yet-instrumented one instead of reading a silent default.
    def _usage_of(r: "StepResult") -> "Optional[dict]":
        u = getattr(r, "token_usage", None)
        return u if isinstance(u, dict) and u else None

    usages = [u for r in results if (u := _usage_of(r)) is not None]
    # ADR-0219 R1: split the token totals by delegated vs local. A single blended
    # total_tokens would corrupt the very break-even the ADR-0218 vision needs —
    # the whole question is "does delegating a step cost fewer tokens than doing
    # it locally?", which is unanswerable if both are summed into one number.
    # Now that the local executor is also instrumented (--output-format json),
    # both sides are real and comparable.
    tokens_delegated = sum(int((_usage_of(r) or {}).get("total_tokens", 0) or 0)
                           for r in results if r.was_delegated and _usage_of(r))
    tokens_local = sum(int((_usage_of(r) or {}).get("total_tokens", 0) or 0)
                       for r in results if not r.was_delegated and _usage_of(r))
    tokens_total = tokens_delegated + tokens_local
    tokens_by_model: dict[str, int] = {}
    # Keep the price tiers SEPARATE, not just the blended total_tokens scalar
    # (adversarial-review finding 3): cache-read bills ~0.1x, cache-creation
    # ~1.25x, output ~5x input — so a single sum is a biased gradient. Phase-1
    # analysis weights these; cost_usd is the authoritative price.
    _kinds = ("input_tokens", "output_tokens",
              "cache_read_input_tokens", "cache_creation_input_tokens")
    tokens_by_kind = {k: 0 for k in _kinds}
    cost_total = 0.0
    have_cost = False
    for u in usages:
        m = str(u.get("model") or "unknown")
        tokens_by_model[m] = tokens_by_model.get(m, 0) + int(u.get("total_tokens", 0) or 0)
        for k in _kinds:
            tokens_by_kind[k] += int(u.get(k, 0) or 0)
        c = u.get("cost_usd")
        if isinstance(c, (int, float)):
            cost_total += float(c)
            have_cost = True

    return {
        "step_count": len(results),
        "succeeded": sum(1 for r in results if r.success),
        "failed": sum(1 for r in results if not r.success),
        "delegated": sum(1 for r in results if r.was_delegated),
        "local": sum(1 for r in results if not r.was_delegated),
        "total_duration_ms": sum(r.duration_ms for r in results),
        # Real, measured (not estimated) — see comment above.
        "avg_delegated_duration_ms": avg_delegated,
        "avg_local_duration_ms": avg_local,
        "latency_delta_pct": latency_delta_pct,
        # ADR-0218 Phase 0 — real token instrumentation.
        "token_usage_instrumented": bool(usages),
        "instrumented_step_count": len(usages),
        "total_tokens": tokens_total if usages else None,
        # R1 delegated-vs-local split — the break-even signal.
        "tokens_delegated": tokens_delegated if usages else None,
        "tokens_local": tokens_local if usages else None,
        "tokens_by_model": tokens_by_model if usages else None,
        "tokens_by_kind": tokens_by_kind if usages else None,
        "cost_usd": round(cost_total, 6) if have_cost else None,
        # Still None: a savings PERCENTAGE needs a counterfactual (what the
        # non-delegated baseline would have cost), which is the Phase-1
        # measurement, not a single run. Present for frontend back-compat.
        "token_savings_pct": None,
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

        # Agentic-compute quota (shared ADR-0094 pool, maintainer decision
        # 2026-07-24). Metered whenever this run can reach a real LLM: real
        # worker IPC, or the default local executor (which spawns the claude
        # CLI even with real_ipc=False). Unit-test configs (injected stub
        # executor + mock IPC) consume no real compute and stay unmetered.
        metered = self.real_ipc or self.local_step_executor is default_local_step_executor
        quota_info: Optional[dict[str, Any]] = None
        if metered:
            denied, quota_info = _enforce_tde_compute_quota(str(kwargs.get("run_id") or ""))
            if denied is not None:
                return denied

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
                tenant_id=str(kwargs.get("tenant_id") or ""),
            )
        except ExecutionError as exc:
            # LM-emitted plans are routinely slightly malformed (0-based step
            # numbers, gaps) — that must yield an explicit error result, not
            # an unhandled crash of the whole send() turn (round-2 finding).
            if metered:
                # No step ran — give the charged pool unit back.
                _refund_tde_compute_unit()
            return {"engine": self.name, "success": False,
                    "error": f"invalid plan: {exc}"}

        start = time.time()
        try:
            # ADR-0222 F1: when a reference model is configured, the shadow runs
            # its comparison on that stronger model (cheap-vs-strong measurement).
            # Only meaningful with the DEFAULT executor (a custom injected one owns
            # its own model); OFF by default so behaviour is unchanged.
            _ref_model = _tde_reference_model()
            _reference_executor = None
            _explore_executors: Optional[list[tuple[str, Any]]] = None
            if _ref_model and self.local_step_executor is default_local_step_executor:
                import functools as _functools  # noqa: PLC0415
                _reference_executor = _functools.partial(
                    default_local_step_executor, model_override=_ref_model)
                # ADR-0222 F4: build one candidate executor per explore-model, each
                # pinned to that model. Only when a reference is in play (candidates
                # need a strong yardstick) and the default executor is in use.
                _explore = _tde_explore_models()
                if _explore:
                    _explore_executors = [
                        (m, _functools.partial(default_local_step_executor,
                                               model_override=m))
                        for m in _explore
                    ]
            results = await executor.execute(
                statement, analysis, self.local_step_executor,
                reference_executor_fn=_reference_executor,
                explore_executor_fns=_explore_executors)
        except ExecutionError as exc:
            # _group_parallel_batches' defense-in-depth "unschedulable steps"
            # raise fires during grouping, before any step ran — previously
            # only the constructor's ExecutionError was caught, so this path
            # crashed the turn instead of returning an error result
            # (review 2026-07-24).
            if metered:
                _refund_tde_compute_unit()
            return {"engine": self.name, "success": False,
                    "error": f"unschedulable plan: {exc}"}
        summary = _summarize(results)
        summary["wall_time_ms"] = int((time.time() - start) * 1000)
        # Badge concept fields (docs/claude-ref/tde-graph-concept.md): sourced
        # from the analysis this run already classified with, and from the
        # SAME quota chokepoint _enforce_tde_compute_quota just charged —
        # never a fresh/broader call path. quota_* stay None when this run
        # was unmetered (no unit charged, nothing to report).
        summary["task_type"] = analysis.classification.task_type if analysis else None
        summary["complexity"] = analysis.classification.complexity if analysis else "moderate"
        summary["quota_used_today"] = quota_info["quota_used_today"] if quota_info else None
        summary["quota_limit"] = quota_info["quota_limit"] if quota_info else None

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
        analysis: Optional[InitialAnalysisRequest] = None
        if isinstance(plan, InitialAnalysisRequest):
            analysis = plan
            global_plan = plan.global_plan
        elif hasattr(plan, "steps"):
            global_plan = plan
        else:
            return {"engine": self.name, "success": False,
                    "error": "claude_code engine requires an InitialAnalysisRequest or GlobalPlan"}

        # ADR-0216/0217 quota chokepoint (2026-07-24 adversarial review,
        # CRITICAL): this engine is reached when the L34 prescan FORCES
        # claude_code away from a TDE run (send_integration), or when the
        # detector falls back to it. It runs the SAME real per-step claude-CLI
        # calls as TDE's local executor, so it MUST charge the shared pool too
        # — otherwise a free-tier user embedding any CONFIDENTIAL token (an
        # e-mail address) in every message routes every turn through this
        # engine and consumes unlimited agentic compute past the 10/day cap.
        # Same metered contract as TieredDelegationEngine.execute: charge only
        # when the run reaches a real LLM (default local executor), never for
        # injected stub executors in unit tests.
        metered = self.local_step_executor is default_local_step_executor
        quota_info: Optional[dict[str, Any]] = None
        if metered:
            denied, quota_info = _enforce_tde_compute_quota(str(kwargs.get("run_id") or ""))
            if denied is not None:
                # Attribute the denial to this engine, not TDE.
                denied = dict(denied)
                denied["engine"] = self.name
                return denied

        statement = context.get("statement") or {}
        if not isinstance(statement, dict):
            statement = {"statement": statement}

        # An empty plan runs no LLM step — give the charged unit back (refund
        # symmetry with TieredDelegationEngine, 2026-07-24 refutation).
        if not global_plan.steps:
            if metered:
                _refund_tde_compute_unit()
            return {"engine": self.name, "success": False,
                    "error": "empty plan: no steps to execute", "results": [],
                    "summary": {"step_count": 0}}

        results: list[StepResult] = []
        start = time.time()
        for step in sorted(global_plan.steps, key=lambda s: s.step):
            t0 = time.time()
            try:
                from .worker_ipc import unwrap_local_result  # noqa: PLC0415
                output, usage = unwrap_local_result(
                    await self.local_step_executor(step, statement))
                results.append(StepResult(
                    step_num=step.step, action=step.action, success=True,
                    output=output, duration_ms=int((time.time() - t0) * 1000),
                    token_usage=usage,
                ))
            except Exception as e:
                results.append(StepResult(
                    step_num=step.step, action=step.action, success=False,
                    error=str(e), duration_ms=int((time.time() - t0) * 1000),
                ))
                break  # sequential engine: fail-fast

        summary = _summarize(results)
        summary["wall_time_ms"] = int((time.time() - start) * 1000)
        # Surface the quota state charged above so the console badge renders
        # "N/limit" for a forced-claude_code turn just like a TDE turn. Also
        # carry task_type/complexity from the analysis (2026-07-24 round-5
        # review — parity with TieredDelegationEngine so the badge's
        # classification line is not blank on an L34-forced turn).
        summary["quota_used_today"] = quota_info["quota_used_today"] if quota_info else None
        summary["quota_limit"] = quota_info["quota_limit"] if quota_info else None
        summary["task_type"] = analysis.classification.task_type if analysis else None
        summary["complexity"] = analysis.classification.complexity if analysis else None
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
