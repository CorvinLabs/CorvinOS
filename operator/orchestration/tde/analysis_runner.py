"""ADR-0210/0214: Real InitialAnalysis LM call.

Executes the ADR-0210 Phase-1 unified analysis (classify + extract + plan in
ONE call) as a real one-shot LLM invocation via the claude CLI
(helper_model.SITE_INITIAL_ANALYSIS, Haiku by default).

This is the piece ADR-0210 left unintegrated: make_task_analysis_prompt()
and parse_task_analysis_response() existed, but nothing ever called an LM
in between. run_initial_analysis() closes that gap.

Contract:
- No anthropic import (cost contract — CLI subprocess only).
- Raises AnalysisUnavailable when the CLI stack is missing (callers decide
  whether to fall back to a heuristic plan; nothing here fakes an analysis).
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

try:
    from initial_analysis import (
        InitialAnalysisRequest,
        make_task_analysis_prompt,
        parse_task_analysis_response,
    )
except ImportError:  # pragma: no cover
    from ..initial_analysis import (  # type: ignore
        InitialAnalysisRequest,
        make_task_analysis_prompt,
        parse_task_analysis_response,
    )

_logger = logging.getLogger(__name__)

# 90s flaked in live testing (Haiku one-shot occasionally >90s under load).
_ANALYSIS_TIMEOUT_S = 180


class AnalysisUnavailable(RuntimeError):
    """The LM analysis stack (claude CLI) is not available or failed."""


def _bridges_shared_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "bridges" / "shared"


def _run_lm_call(prompt: str, timeout_s: int, proc_holder: Optional[Any] = None) -> str:
    shared = _bridges_shared_dir()
    if shared.is_dir() and str(shared) not in sys.path:
        sys.path.insert(0, str(shared))
    try:
        import helper_model  # noqa: PLC0415
    except Exception as exc:  # pragma: no cover
        raise AnalysisUnavailable(f"helper_model unavailable: {exc}") from exc

    cmd = [
        helper_model.resolve_claude_bin(), "-p", prompt,
        "--max-turns", "1",
        "--output-format", "text",
        "--disallowedTools", "*",
        *helper_model.claude_args(helper_model.SITE_INITIAL_ANALYSIS),
    ]
    try:
        # Neutral cwd + process-group kill (same hygiene as the worker
        # one-shots — the analysis call previously inherited the
        # orchestrator's cwd and loaded repo CLAUDE.md context).
        from .worker_ipc import run_one_shot  # noqa: PLC0415 — avoid import cycle

        rc, stdout, stderr = run_one_shot(cmd, timeout_s, proc_holder=proc_holder)
    except FileNotFoundError as exc:
        raise AnalysisUnavailable("claude CLI not found") from exc
    except subprocess.TimeoutExpired as exc:
        raise AnalysisUnavailable(f"analysis timeout after {timeout_s}s") from exc
    except OSError as exc:
        # E2BIG et al.: a >128KB chat message exceeds Linux MAX_ARG_STRLEN for
        # the single `-p` argv element — Popen raises OSError("Argument list
        # too long"), which previously escaped unmapped and crashed the turn
        # instead of degrading like every other analysis failure (adversarial
        # review 2026-07-24).
        raise AnalysisUnavailable(f"analysis spawn failed: {exc}") from exc

    if rc != 0:
        raise AnalysisUnavailable(
            f"analysis exit {rc}: {stderr.strip()[:300]}"
        )
    return stdout.strip()


def run_initial_analysis_sync(
    task: str,
    context: Optional[dict[str, Any]] = None,
    *,
    timeout_s: int = _ANALYSIS_TIMEOUT_S,
    proc_holder: Optional[Any] = None,
) -> InitialAnalysisRequest:
    """One REAL LM call: classify + extract entities + plan (ADR-0210 Phase 1).

    ``proc_holder`` (a ``tde.worker_ipc.ProcHolder``), when given, lets an
    ``asyncio.to_thread`` caller kill the underlying subprocess if the turn
    is cancelled mid-analysis (round-4 finding: this call previously had no
    such holder, unlike the console's ADR-0213 context-sync call).

    Raises:
        AnalysisUnavailable: CLI missing/failed/timeout.
        ValueError: LM response was not a valid analysis JSON.
    """
    prompt = make_task_analysis_prompt(task, context or {})
    raw = _run_lm_call(prompt, timeout_s, proc_holder=proc_holder)
    analysis = parse_task_analysis_response(raw)
    analysis.cache_key = hashlib.sha256(task.encode()).hexdigest()[:16]
    _logger.info(
        "InitialAnalysis: type=%s complexity=%s confidence=%.2f steps=%d",
        analysis.classification.task_type,
        analysis.classification.complexity,
        analysis.classification.confidence,
        len(analysis.global_plan.steps),
    )
    return analysis


async def run_initial_analysis(
    task: str,
    context: Optional[dict[str, Any]] = None,
    *,
    timeout_s: int = _ANALYSIS_TIMEOUT_S,
    proc_holder: Optional[Any] = None,
) -> InitialAnalysisRequest:
    """Async wrapper for run_initial_analysis_sync (subprocess off-loop)."""
    return await asyncio.to_thread(
        run_initial_analysis_sync, task, context,
        timeout_s=timeout_s, proc_holder=proc_holder,
    )
