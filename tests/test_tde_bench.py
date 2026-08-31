"""ADR-0215 Phase 3: tde_bench harness — unit tests (mocked, no real LM
calls/cost). Live-LM validation lives in test_tde_bench_live.py (skipped
by default, same convention as test_tde_e2e_live.py)."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest import mock

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "operator" / "orchestration"))

from tde import bench  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_audit_chain(monkeypatch, tmp_path):
    """Redirect the audit chain to tmp for EVERY test in this file.

    Adversarial review 2026-07-24 (verified empirically): _emit_snapshot →
    tde_audit.emit resolves the REAL backend, so each pytest run appended
    permanent tde.bench_snapshot events to the live hash-chained
    audit.jsonl — unremovable test noise in a GDPR Art. 30 record, and on a
    pinned-service host it lands in the production chain."""
    monkeypatch.setenv("VOICE_AUDIT_PATH", str(tmp_path / "audit.jsonl"))


def _fake_analysis(estimated_tokens=1234, steps=1):
    analysis = mock.MagicMock()
    analysis.global_plan.estimated_tokens = estimated_tokens
    analysis.global_plan.steps = [mock.MagicMock()] * steps
    analysis.classification.task_type = "code_generation"
    analysis.classification.complexity = "simple"
    return analysis


def test_default_corpus_has_both_categories():
    categories = {t.category for t in bench.DEFAULT_CORPUS}
    assert "real" in categories
    assert "fictional_edge_case" in categories


def test_default_corpus_has_no_duplicate_task_ids():
    ids = [t.task_id for t in bench.DEFAULT_CORPUS]
    assert len(ids) == len(set(ids))


def test_default_corpus_includes_empty_and_whitespace_edge_cases():
    ids = {t.task_id for t in bench.DEFAULT_CORPUS}
    assert "edge_01_empty_task" in ids
    assert "edge_02_only_whitespace" in ids


def test_default_target_registered():
    targets = bench.registered_targets()
    assert any(t.name == "tde_default_corpus" for t in targets)


def test_run_task_success_path(monkeypatch):
    task = bench.BenchTask(task_id="t1", prompt="do something")
    analysis = _fake_analysis()

    fake_integration = mock.AsyncMock()
    fake_integration.select_engine_and_execute = mock.AsyncMock(
        return_value=("claude_code", {"success": True})
    )

    result = asyncio.run(bench.run_task(
        task, "claude_code", integration=fake_integration, analysis=analysis,
    ))
    assert result.success is True
    assert result.engine == "claude_code"
    assert result.estimated_tokens == 1234
    assert result.duration_ms >= 0


def test_run_task_never_raises_on_backend_exception():
    task = bench.BenchTask(task_id="t2", prompt="do something")
    analysis = _fake_analysis()

    fake_integration = mock.AsyncMock()
    fake_integration.select_engine_and_execute = mock.AsyncMock(
        side_effect=RuntimeError("subprocess exploded")
    )

    result = asyncio.run(bench.run_task(
        task, "tiered_delegation", integration=fake_integration, analysis=analysis,
    ))
    assert result.success is False
    assert "subprocess exploded" in result.error


def test_summarize_computes_per_engine_stats():
    results = [
        bench.BenchResult("t1", "claude_code", True, 100.0, 500),
        bench.BenchResult("t2", "claude_code", True, 200.0, 700),
        bench.BenchResult("t3", "claude_code", False, 50.0, None, error="boom"),
        bench.BenchResult("t1", "tiered_delegation", True, 60.0, 500),
        bench.BenchResult("t2", "tiered_delegation", True, 80.0, 700),
    ]
    report = bench._summarize(results, truncated=False, calls_spent=5,
                              max_real_calls=20, corpus_size=3)
    cc = report["per_engine"]["claude_code"]
    tde = report["per_engine"]["tiered_delegation"]

    assert cc["n"] == 3
    assert cc["success_count"] == 2
    assert cc["success_rate"] == pytest.approx(2 / 3, abs=1e-3)
    assert cc["avg_duration_ms"] == pytest.approx(150.0)  # only successes count
    assert tde["avg_duration_ms"] == pytest.approx(70.0)
    assert report["token_usage_instrumented"] is False


def test_run_default_suite_respects_max_real_calls(monkeypatch):
    # Force every InitialAnalysis + execute call to be a cheap mock, then
    # verify the budget ceiling is genuinely enforced (not advisory).
    analysis = _fake_analysis()

    async def _fake_analysis_async(task, holder):
        return analysis

    monkeypatch.setattr(bench, "run_initial_analysis_sync_async", _fake_analysis_async)

    fake_integration_cls = mock.MagicMock()
    fake_integration = mock.AsyncMock()
    fake_integration.select_engine_and_execute = mock.AsyncMock(
        return_value=("claude_code", {"success": True})
    )
    monkeypatch.setattr(bench, "SendIntegration", lambda **kw: fake_integration)

    report = asyncio.run(bench.run_default_suite(
        engines=("claude_code", "tiered_delegation"), max_real_calls=5,
    ))
    assert report["calls_spent"] <= 5
    assert report["truncated"] is True  # corpus is 13 tasks x 2 engines >> 5


def test_run_default_suite_full_corpus_under_generous_budget(monkeypatch):
    analysis = _fake_analysis()

    async def _fake_analysis_async(task, holder):
        return analysis

    monkeypatch.setattr(bench, "run_initial_analysis_sync_async", _fake_analysis_async)

    fake_integration = mock.AsyncMock()
    fake_integration.select_engine_and_execute = mock.AsyncMock(
        return_value=("claude_code", {"success": True})
    )
    monkeypatch.setattr(bench, "SendIntegration", lambda **kw: fake_integration)

    n_tasks = len(bench.DEFAULT_CORPUS)
    report = asyncio.run(bench.run_default_suite(
        engines=("claude_code",), max_real_calls=n_tasks * 3,
    ))
    assert report["truncated"] is False
    assert report["tasks_run"] == n_tasks


def test_emit_snapshot_does_not_raise_without_audit_backend():
    report = {
        "tasks_run": 3, "calls_spent": 6, "truncated": False,
        "per_engine": {
            "claude_code": {"avg_duration_ms": 100.0},
            "tiered_delegation": {"avg_duration_ms": 60.0},
        },
    }
    bench._emit_snapshot(report)  # must not raise even if audit backend unavailable
