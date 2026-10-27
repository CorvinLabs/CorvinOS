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
import contextvars
import hashlib
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

# Relative FIRST, bare name only as the fallback.
#
# There are two modules called initial_analysis: this package's sibling
# (corvin_operator/orchestration/initial_analysis.py, which defines the three
# names below) and corvin_operator/initial_analysis.py, a 50-line dataclass-only
# file. With <repo>/corvin_operator on sys.path — which several TDE test modules
# put there — the bare `from initial_analysis import ...` resolved to the
# dataclass-only one and raised "cannot import name
# 'make_task_analysis_prompt'"; the except-branch then raised "attempted
# relative import beyond top-level package" and the whole TDE suite was a
# collection error. The relative import is unambiguous, so it goes first.
try:
    from ..initial_analysis import (  # type: ignore
        InitialAnalysisRequest,
        make_task_analysis_prompt,
        parse_task_analysis_response,
    )
except ImportError:  # pragma: no cover - flat sys.path layouts
    from initial_analysis import (
        InitialAnalysisRequest,
        make_task_analysis_prompt,
        parse_task_analysis_response,
    )

_logger = logging.getLogger(__name__)

# ── shared `claude -p` prompt guard (ADR-0648 / adversarial review R4) ─────
# The claude CLI expands `/`, `!` and `#` at byte 0 and `@<path>` ANYWHERE in a
# prompt — client-side, BEFORE the model runs, so no tool policy, permission
# mode or `--disallowedTools` restricts them. Every payload this module hands
# to the CLI therefore goes through the ONE shared neutraliser. The fallback is
# a RAISING stub, never a pass-through: an unimportable guard refuses the
# spawn, it never downgrades it to an unguarded one.
try:  # pragma: no cover - import shape
    import os as _pg_os, sys as _pg_sys
    _pg_d = _pg_os.path.dirname(_pg_os.path.abspath(__file__))
    while _pg_d != _pg_os.path.dirname(_pg_d):
        _pg_c = _pg_os.path.join(_pg_d, "corvin_operator", "bridges", "shared")
        if _pg_os.path.isdir(_pg_c):
            if _pg_c not in _pg_sys.path:
                _pg_sys.path.insert(0, _pg_c)
            break
        _pg_d = _pg_os.path.dirname(_pg_d)
    from prompt_guard import guard_prompt_head as _guard_prompt_head  # type: ignore
except Exception:  # pragma: no cover - guard unavailable => refuse, never bypass
    def _guard_prompt_head(_text):  # type: ignore[misc]
        raise RuntimeError(
            "shared claude-CLI prompt guard unavailable "
            "(operator/bridges/shared/prompt_guard.py) - refusing to build an "
            "unguarded `claude -p` payload"
        )

# 90s flaked in live testing (Haiku one-shot occasionally >90s under load).

_ANALYSIS_TIMEOUT_S = 180


class AnalysisUnavailable(RuntimeError):
    """The LM analysis stack (claude CLI) is not available or failed."""


# ADR-2306 decision 3 (T-0319): the planner is a real LLM call whose tokens count toward the cost of a delegated run.
# ``_run_lm_call`` returns plain text (tests patch it), so the usage of its last call travels in a context variable
# that ``run_initial_analysis_sync`` reads in the same thread and attaches to the analysis as ``planner_usage``.
_LAST_USAGE: "contextvars.ContextVar[Optional[dict]]" = contextvars.ContextVar("tde_analysis_last_usage", default=None)


def _split_envelope(stdout: str, model: str = "") -> "tuple[str, Optional[dict], Optional[str]]":
    """(text, usage, error) from a ``claude -p --output-format json`` reply; a plain-text reply passes through unchanged.

    ``model`` is the model this call was asked to run on: the CLI's envelope carries no model field, so without it the
    usage block would be unattributable (found by the live test, 2026-10-27)."""
    try:
        from .worker_ipc import parse_cli_envelope  # noqa: PLC0415 — avoid import cycle
    except ImportError:  # a stubbed worker_ipc (tests) without the parser: behave like the text format
        return stdout, None, None
    return parse_cli_envelope(stdout, model=model)


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
        # R4: whole-payload guard - the analysis prompt carries the user's
        # chat turn. POSITIONAL argv. Reply contract unaffected (stdout).
        helper_model.resolve_claude_bin(), "-p", _guard_prompt_head(prompt),
        "--max-turns", "1",
        "--output-format", "json",
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
    try:
        model_tag = helper_model.resolve_helper_model(helper_model.SITE_INITIAL_ANALYSIS) or ""
    except Exception:  # noqa: BLE001 — attribution is best-effort, the analysis itself must not fail on it
        model_tag = ""
    text, usage, env_error = _split_envelope(stdout, model=model_tag)
    if env_error:
        # The CLI answers a soft failure (max turns, API error) as an is_error envelope with exit 0; the error
        # text must never be parsed as an analysis (same rule as the worker path, worker_ipc.parse_cli_envelope).
        raise AnalysisUnavailable(f"analysis error: {env_error}")
    _LAST_USAGE.set(usage)
    return text.strip()


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
    _LAST_USAGE.set(None)  # a stale value must never be attributed to a call that reported none
    raw = _run_lm_call(prompt, timeout_s, proc_holder=proc_holder)
    planner_usage = _LAST_USAGE.get()
    analysis = parse_task_analysis_response(raw)
    analysis.cache_key = hashlib.sha256(task.encode()).hexdigest()[:16]
    # Measured tokens of THIS planner call (four components, ``worker_ipc.parse_cli_envelope`` shape) or None.
    analysis.planner_usage = planner_usage
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
