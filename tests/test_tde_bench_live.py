"""ADR-0215 Phase 3 LIVE validation — real LM calls through tde_bench.

Same opt-in convention as test_tde_e2e_live.py: CLAUDE_LIVE_E2E=1 + a
`claude` CLI on PATH. Deliberately small (a 2-task, budget-capped slice of
DEFAULT_CORPUS, not the full 13-task corpus) — this proves the harness's
real production entry point (SendIntegration.select_engine_and_execute,
not a mock) actually works end-to-end with real subprocesses; the full
corpus is designed for a nightly, budget-owned cron run (see
operator/orchestration/tde/bench.py module docstring), not for every
CI/test invocation.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "operator" / "orchestration"))
sys.path.insert(0, str(Path(__file__).parent.parent / "operator" / "bridges" / "shared"))

live = pytest.mark.skipif(
    os.environ.get("CLAUDE_LIVE_E2E", "") != "1"
    or (
        shutil.which("claude") is None
        and not os.path.isfile(os.path.expanduser("~/.local/bin/claude"))
    ),
    reason="live tde_bench validation needs CLAUDE_LIVE_E2E=1 and the claude CLI",
)

pytestmark = [live, pytest.mark.live]

from tde import bench  # noqa: E402


def test_live_bench_real_and_fictional_task_through_real_pipeline():
    """Runs 2 real corpus tasks (one 'real', one 'fictional_edge_case')
    through the real SendIntegration.select_engine_and_execute path with
    real `claude -p` subprocesses, claude_code engine only, budget-capped
    to keep this fast/cheap while still proving the harness end-to-end."""
    small_corpus = [
        t for t in bench.DEFAULT_CORPUS
        if t.task_id in ("real_01_simple_function", "edge_01_empty_task")
    ]
    assert len(small_corpus) == 2

    import asyncio
    report = asyncio.run(bench.run_default_suite(
        engines=("claude_code",), max_real_calls=4, corpus=small_corpus,
    ))

    assert report["calls_spent"] > 0
    # The BENCH HARNESS still reports only estimated_tokens, so its own flag is
    # False — even though the ENGINE instruments real per-step tokens since
    # ADR-0219 R1 (aggregating them into the bench report is the R2 follow-up).
    assert report["token_usage_instrumented"] is False  # bench harness only

    real_result = next(r for r in report["results"] if r.task_id == "real_01_simple_function")
    assert real_result.engine == "claude_code"
    # Real wall-clock time must be measured whether the task SUCCEEDED or
    # not — a live run surfaced a real prompt-robustness gap (the
    # InitialAnalysis helper model occasionally answers the task directly
    # in prose instead of returning the required structured JSON, which
    # parse_task_analysis_response() correctly rejects) and, at the time,
    # a harness bug where the failure path hardcoded duration_ms=0.0
    # instead of measuring the real (wasted) subprocess time. Both are
    # legitimate real-world outcomes this assertion must tolerate — it only
    # asserts the MEASUREMENT is honest, not that the task succeeded.
    assert real_result.duration_ms > 0, (
        "real subprocess call must take measurable wall-clock time "
        "(whether the task itself succeeded or not)"
    )

    # The empty-task edge case must not CRASH the harness — success is not
    # required (an empty prompt may legitimately fail InitialAnalysis), but
    # a clean BenchResult (not an unhandled exception) is.
    empty_result = next(r for r in report["results"] if r.task_id == "edge_01_empty_task")
    assert isinstance(empty_result.success, bool)


def test_live_bench_snapshot_reaches_the_real_audit_chain():
    """Confirms _emit_snapshot's tde.bench_snapshot event actually lands on
    the real hash-chained audit log when the real audit backend is
    available (not just 'does not raise', which the mocked unit test in
    test_tde_bench.py already covers)."""
    import json as _json

    audit_shared = Path(__file__).resolve().parent.parent / "operator" / "bridges" / "shared"
    if str(audit_shared) not in sys.path:
        sys.path.insert(0, str(audit_shared))
    import audit as _audit_mod  # type: ignore

    path = _audit_mod.audit_path()
    before = 0
    if path.exists():
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            before = sum(1 for line in fh if '"tde.bench_snapshot"' in line)

    bench._emit_snapshot({
        "tasks_run": 1, "calls_spent": 1, "truncated": False,
        "per_engine": {"claude_code": {"avg_duration_ms": 100.0}},
    })

    after = 0
    if path.exists():
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            after = sum(1 for line in fh if '"tde.bench_snapshot"' in line)
    assert after == before + 1, "tde.bench_snapshot did not land on the real audit chain"
