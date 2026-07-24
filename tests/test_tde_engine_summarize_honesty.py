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

import asyncio
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "operator" / "orchestration"))
sys.path.insert(0, str(_REPO / "operator"))

from tde.tde_engine import StepResult, TieredDelegationEngine, _summarize  # noqa: E402
from tde.worker_ipc import MockWorkerIPC, parse_cli_envelope, parse_worker_output  # noqa: E402
from tde.loss_profile_tracker import clear_session_tracker  # noqa: E402
from initial_analysis import (  # noqa: E402
    Classification,
    Entities,
    GlobalPlan,
    InitialAnalysisRequest,
    Step,
)

# This file's execute() calls all use this dedicated session_key rather than
# the "default" one several other TDE test files implicitly share — the
# session tracker is a process-wide singleton (loss_profile_tracker.py), so a
# shared key lets these tests' loss/exploration evidence leak into unrelated
# suites' exploration-path assertions depending on collection order
# (reproduced: this file + test_tde_agentic_quota.py + test_tde_phase2_executor.py
# run together flipped an unrelated exploration-path test).
_SESSION_KEY = "test_tde_engine_summarize_honesty"


def _sr(step_num, *, delegated, duration_ms, success=True):
    return StepResult(
        step_num=step_num, action="read_file", success=success,
        duration_ms=duration_ms, was_delegated=delegated,
    )


def _sr_tok(step_num, total_tokens, *, model="haiku", cost=None, delegated=True):
    return StepResult(
        step_num=step_num, action="read_file", success=True,
        duration_ms=100, was_delegated=delegated,
        token_usage={"total_tokens": total_tokens, "model": model, "cost_usd": cost},
    )


def test_token_savings_pct_is_always_none_never_fabricated():
    results = [
        _sr(1, delegated=True, duration_ms=100),
        _sr(2, delegated=False, duration_ms=200),
    ]
    summary = _summarize(results)
    assert summary["token_savings_pct"] is None
    assert summary["token_usage_instrumented"] is False


# ── ADR-0218 Phase 0: real token instrumentation ────────────────────────────

def test_token_usage_summed_and_flag_flips_when_usage_present():
    summary = _summarize([_sr_tok(1, 1580, cost=0.0021), _sr_tok(2, 800, cost=0.001)])
    assert summary["token_usage_instrumented"] is True
    assert summary["total_tokens"] == 2380
    assert summary["instrumented_step_count"] == 2
    assert round(summary["cost_usd"], 4) == 0.0031
    # A savings PERCENTAGE still needs a counterfactual (Phase 1), never faked.
    assert summary["token_savings_pct"] is None


def test_tokens_broken_down_by_model():
    summary = _summarize([
        _sr_tok(1, 1000, model="claude-haiku-4-5"),
        _sr_tok(2, 500, model="claude-haiku-4-5"),
        _sr_tok(3, 4000, model="claude-sonnet-5"),
    ])
    assert summary["tokens_by_model"] == {"claude-haiku-4-5": 1500, "claude-sonnet-5": 4000}
    assert summary["total_tokens"] == 5500


def test_absent_usage_is_omitted_never_counted_as_zero():
    # One instrumented delegated step + one local step with no usage: the local
    # step must not dilute the total to a fake 0, and the flag still flips True.
    summary = _summarize([_sr_tok(1, 1200), _sr(2, delegated=False, duration_ms=90)])
    assert summary["token_usage_instrumented"] is True
    assert summary["total_tokens"] == 1200
    assert summary["instrumented_step_count"] == 1


def test_cost_none_when_no_step_reported_cost():
    summary = _summarize([_sr_tok(1, 1000, cost=None)])
    assert summary["cost_usd"] is None
    assert summary["total_tokens"] == 1000


def test_cli_envelope_extracts_result_and_usage():
    env = (
        '{"type":"result","result":"{\\"output\\": \\"done\\"}",'
        '"usage":{"input_tokens":1200,"output_tokens":80,'
        '"cache_read_input_tokens":300,"cache_creation_input_tokens":0},'
        '"total_cost_usd":0.0021,"model":"claude-haiku-4-5"}'
    )
    text, usage, error = parse_cli_envelope(env, model="fallback")
    assert parse_worker_output(text) == "done"
    assert usage["total_tokens"] == 1580  # 1200+80+300+0
    assert usage["cost_usd"] == 0.0021
    assert usage["model"] == "claude-haiku-4-5"
    assert error is None


def test_cli_envelope_failsoft_on_plain_text_and_missing_result():
    # Old --output-format text reply (not an envelope) → passthrough, no usage.
    assert parse_cli_envelope("just text") == ("just text", None, None)
    # JSON without a result field → treated as not-an-envelope, no usage.
    assert parse_cli_envelope('{"foo":1}')[1] is None
    # A non-string result is still returned as a string (never crashes).
    text, usage, error = parse_cli_envelope('{"result":{"a":1},"usage":{"input_tokens":5}}')
    assert isinstance(text, str) and usage["total_tokens"] == 5 and error is None


def test_cli_envelope_flags_is_error_but_still_returns_usage():
    # is_error=true with rc=0 (soft failure) must surface the subtype so the
    # caller marks the step failed — but the spent tokens still count.
    env = (
        '{"type":"result","subtype":"error_max_turns","is_error":true,'
        '"result":"I could not finish in one turn.",'
        '"usage":{"input_tokens":10,"output_tokens":5},"total_cost_usd":0.001}'
    )
    text, usage, error = parse_cli_envelope(env)
    assert error == "error_max_turns"
    assert usage["total_tokens"] == 15  # tokens were spent even on the error
    assert "could not finish" in text


def test_cli_envelope_error_without_result_does_not_leak_json():
    # Finding 2: a result-LESS error envelope must be recognised as an envelope
    # (via is_error/subtype/type markers) and NOT dumped verbatim as the output.
    env = ('{"type":"result","subtype":"error_during_execution","is_error":true,'
           '"usage":{"input_tokens":3,"output_tokens":0}}')
    text, usage, error = parse_cli_envelope(env)
    assert error == "error_during_execution"
    assert text == ""                      # no result field → empty, not the JSON
    assert usage["total_tokens"] == 3
    # parse_worker_output on "" must not resurrect the envelope
    assert parse_worker_output(text) == ""


def test_cli_envelope_non_success_subtype_is_an_error_even_without_is_error():
    # Mirrors claude_code.py: subtype not in ("success", None) is an error.
    env = '{"type":"result","subtype":"error_max_turns","result":"partial"}'
    _text, _usage, error = parse_cli_envelope(env)
    assert error == "error_max_turns"


def test_summarize_reports_tokens_by_kind():
    # Finding 3: price tiers kept separate, not only the blended total.
    sr = StepResult(step_num=1, action="x", success=True, duration_ms=10,
                    was_delegated=True, token_usage={
                        "input_tokens": 10, "output_tokens": 20,
                        "cache_read_input_tokens": 300, "cache_creation_input_tokens": 40,
                        "total_tokens": 370, "model": "haiku"})
    s = _summarize([sr])
    assert s["tokens_by_kind"] == {
        "input_tokens": 10, "output_tokens": 20,
        "cache_read_input_tokens": 300, "cache_creation_input_tokens": 40}
    assert s["total_tokens"] == 370


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


# ── ADR-0216 badge fields: quota_used_today/quota_limit + task_type/complexity ──
# These are NOT set by _summarize() itself (they come from
# TieredDelegationEngine.execute(), sourced from the same quota chokepoint
# _enforce_tde_compute_quota just charged and from the turn's classification),
# so these tests drive the real engine rather than _summarize() directly —
# mirrors tests/test_tde_agentic_quota.py's fixture.

def _analysis(task_type: str = "analysis", complexity: str = "simple") -> InitialAnalysisRequest:
    return InitialAnalysisRequest(
        classification=Classification(task_type, complexity, "claude", 0.9),
        entities=Entities(),
        global_plan=GlobalPlan(
            steps=[Step(step=1, action="analyze_data")],
            estimated_duration_s=10,
            estimated_tokens=100,
        ),
    )


async def _local_exec(step, statement, **kw):
    return f"local-{step.step}"


@pytest.fixture()
def quota_env(monkeypatch, tmp_path):
    """Isolated corvin_home + free tier, license loading neutralised (mirrors
    test_tde_agentic_quota.py's fixture) — needed so the new quota_used_today/
    quota_limit summary fields reflect a real, isolated counter file rather
    than the operator's live one. Also resets/clears the dedicated
    _SESSION_KEY loss-profile tracker so this file neither reads nor leaves
    behind cross-test exploration-decision evidence."""
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    monkeypatch.setenv("VOICE_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    import license.validator as _v  # noqa: PLC0415
    monkeypatch.setattr(_v, "load_license_from_env", lambda *a, **k: None)
    _v._set_active_license(None)  # free tier
    clear_session_tracker(session_key=_SESSION_KEY)
    yield tmp_path
    _v._set_active_license(None)
    clear_session_tracker(session_key=_SESSION_KEY)


def test_execute_summary_carries_task_type_and_complexity(quota_env):
    engine = TieredDelegationEngine(real_ipc=True, local_step_executor=_local_exec)
    result = asyncio.run(engine.execute(
        _analysis(task_type="code_generation", complexity="complex"),
        {"statement": {}}, worker_ipc=MockWorkerIPC(), use_semantic_judge=False,
        session_key=_SESSION_KEY,
    ))
    assert result["success"] is True
    summary = result["summary"]
    assert summary["task_type"] == "code_generation"
    assert summary["complexity"] == "complex"


def test_execute_summary_reports_quota_on_finite_free_tier(quota_env):
    """Free tier: quota_limit is the finite 10/day shared pool, and
    quota_used_today reflects the unit this very run just charged — read via
    the SAME chokepoint that incremented it, not a new/broader call path."""
    engine = TieredDelegationEngine(real_ipc=True, local_step_executor=_local_exec)
    result = asyncio.run(engine.execute(
        _analysis(), {"statement": {}}, worker_ipc=MockWorkerIPC(), use_semantic_judge=False,
        session_key=_SESSION_KEY,
    ))
    assert result["success"] is True
    summary = result["summary"]
    assert summary["quota_used_today"] == 1
    assert summary["quota_limit"] == 10


def test_execute_summary_quota_limit_is_none_on_unlimited_tier(quota_env, monkeypatch):
    """Member tier (unlimited compute pool): quota_limit must surface as
    `None`, never a fabricated cap — the badge renders 'unlimited' (omits the
    '/N' suffix) only when this is genuinely None, per the ADR-0215 honesty
    contract this file guards."""
    import license.validator as _v  # noqa: PLC0415
    monkeypatch.setattr(_v, "get_limit", lambda feature: None)
    engine = TieredDelegationEngine(real_ipc=True, local_step_executor=_local_exec)
    result = asyncio.run(engine.execute(
        _analysis(), {"statement": {}}, worker_ipc=MockWorkerIPC(), use_semantic_judge=False,
        session_key=_SESSION_KEY,
    ))
    assert result["success"] is True
    summary = result["summary"]
    assert summary["quota_limit"] is None
    assert summary["quota_used_today"] == 1


def test_execute_summary_quota_fields_none_when_unmetered(quota_env):
    """A stub-executor-only unit-test config (no real_ipc, non-default
    executor) never charges the pool — quota_used_today/quota_limit must
    stay None rather than report stale/fabricated numbers."""
    engine = TieredDelegationEngine(local_step_executor=_local_exec)
    result = asyncio.run(engine.execute(
        _analysis(), {"statement": {}}, worker_ipc=MockWorkerIPC(),
        session_key=_SESSION_KEY,
    ))
    assert result["success"] is True
    summary = result["summary"]
    assert summary["quota_used_today"] is None
    assert summary["quota_limit"] is None
