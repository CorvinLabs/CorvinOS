"""ADR-0215 Fix 4: tde_engine._summarize() must never fabricate token
savings, and must correctly compute the real, measured latency comparison
it now reports instead.

Context: a concurrent session added a `token_savings_pct` field to the
`engine_progress` UI event, sourced via `summary.get('token_savings_pct',
0)` — but `_summarize()` never set that key, so the field was structurally
always 0, indistinguishable in the UI from a real "0% savings"
measurement. That session's own follow-up commit (fcb6aaf) removed the
field with a TODO ("TDE-Engine must calculate actual savings"). This test
file guards the honest replacement implemented here.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "operator" / "orchestration"))

from tde.tde_engine import StepResult, _summarize  # noqa: E402


def _sr(step_num, *, delegated, duration_ms, success=True):
    return StepResult(
        step_num=step_num, action="read_file", success=success,
        duration_ms=duration_ms, was_delegated=delegated,
    )


def test_token_savings_pct_is_always_none_never_fabricated():
    results = [
        _sr(1, delegated=True, duration_ms=100),
        _sr(2, delegated=False, duration_ms=200),
    ]
    summary = _summarize(results)
    assert summary["token_savings_pct"] is None
    assert summary["token_usage_instrumented"] is False


def test_latency_delta_pct_is_genuinely_computed():
    results = [
        _sr(1, delegated=True, duration_ms=50),
        _sr(2, delegated=True, duration_ms=50),
        _sr(3, delegated=False, duration_ms=100),
        _sr(4, delegated=False, duration_ms=100),
    ]
    summary = _summarize(results)
    assert summary["avg_delegated_duration_ms"] == 50
    assert summary["avg_local_duration_ms"] == 100
    # delegated is 50% faster than local here
    assert summary["latency_delta_pct"] == 50.0


def test_latency_fields_none_when_no_data_of_that_kind():
    results = [_sr(1, delegated=True, duration_ms=50)]
    summary = _summarize(results)
    assert summary["avg_local_duration_ms"] is None
    assert summary["latency_delta_pct"] is None


def test_failed_steps_excluded_from_latency_average():
    results = [
        _sr(1, delegated=True, duration_ms=50, success=True),
        _sr(2, delegated=True, duration_ms=99999, success=False),
    ]
    summary = _summarize(results)
    # the failed step's absurd duration must not pollute the average
    assert summary["avg_delegated_duration_ms"] == 50


def test_summary_still_has_original_fields():
    results = [_sr(1, delegated=True, duration_ms=10)]
    summary = _summarize(results)
    for key in ("step_count", "succeeded", "failed", "delegated", "local", "total_duration_ms"):
        assert key in summary
