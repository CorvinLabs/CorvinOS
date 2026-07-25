"""ADR-0222 k=5 — real {direct, tier} orchestration + F1 judging.

Covers the honesty invariant at the sample layer: every path that could book a
FABRICATED number must drop the sample instead. The k=4 hook is the cautionary
case these tests pin down — it read a non-existent ``result["usage"]`` key, so
every sample it could have produced carried ``tde_tokens=0``, which the gate
reads as "(direct - 0) / direct = 100% savings", the strongest possible pro-TDE
evidence produced by an absent measurement.

Run: python3 -m pytest tests/test_tde_measurement_k5_real_orchestration.py
"""
from __future__ import annotations

import asyncio
import inspect
import json
import sys
import types
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "operator" / "orchestration"))

from tde import tde_engine  # noqa: E402
from tde.tde_measurement import (  # noqa: E402
    PERSIST_OUTPUTS_ENV,
    VALID_BANDS,
    MeasurementRecorder,
    MeasurementSample,
    RealTdeOrchestrator,
    classify_band,
)


def _local_result(tokens: int | None, output: str) -> types.SimpleNamespace:
    """Stand-in for worker_ipc.LocalResult (output + usage)."""
    usage = None if tokens is None else {"total_tokens": tokens}
    return types.SimpleNamespace(output=output, usage=usage)


def _sample(**over: object) -> MeasurementSample:
    base = dict(
        task_id="run-1", task_band="moderate", timestamp=1.0,
        direct_tokens=5000, direct_output="direct answer",
        tier_tokens=4000, tier_output="tier answer", tier_loss=0.02,
        tde_tokens=3000, tde_output="tde answer", tde_loss=0.05,
    )
    base.update(over)
    return MeasurementSample(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# The direct baseline (the reference arm)
# ---------------------------------------------------------------------------

def test_direct_baseline_exists_and_is_async():
    assert inspect.iscoroutinefunction(tde_engine.whole_task_direct_baseline)
    params = inspect.signature(tde_engine.whole_task_direct_baseline).parameters
    assert "user_model" in params
    # Keyword-only, matching the tier baseline's contract.
    assert params["user_model"].kind is inspect.Parameter.KEYWORD_ONLY


def test_direct_baseline_pins_user_model_and_does_not_tier_resolve(monkeypatch):
    """The reference arm must run the USER's model, never a tier-resolved one.

    If it silently tier-resolved, direct and tier would collapse onto the same
    model and every measured savings figure would be ~0 for a reason that has
    nothing to do with TDE.
    """
    seen: dict[str, object] = {}

    async def _fake_core(statement, *, model, proc_holder=None, label=""):
        seen["model"] = model
        seen["label"] = label
        return _local_result(1234, "out")

    monkeypatch.setattr(tde_engine, "_whole_task_single_turn", _fake_core)
    asyncio.run(tde_engine.whole_task_direct_baseline(
        {"statement": "t"}, user_model="claude-opus-5"))
    assert seen["model"] == "claude-opus-5"
    assert "direct" in str(seen["label"])


def test_direct_and_tier_baselines_share_one_core(monkeypatch):
    """Both arms must go through the same execution core.

    Two separate code paths would let a prompt/parser difference masquerade as a
    model-tier difference in the evidence the gate consumes.
    """
    calls: list[str] = []

    async def _fake_core(statement, *, model, proc_holder=None, label=""):
        calls.append(label)
        return _local_result(10, "out")

    monkeypatch.setattr(tde_engine, "_whole_task_single_turn", _fake_core)
    monkeypatch.setattr(tde_engine, "_ensure_bridges_on_path", lambda: None)
    asyncio.run(tde_engine.whole_task_direct_baseline({"s": "t"}, user_model="m"))
    asyncio.run(tde_engine.whole_task_tier_baseline({"s": "t"}, user_model="m"))
    assert len(calls) == 2, "both baselines must route through the shared core"


# ---------------------------------------------------------------------------
# Token extraction — fail-closed, never a fabricated zero
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("result,expected", [
    (_local_result(4200, "x"), 4200),
    (_local_result(None, "x"), None),          # usage block absent
    (_local_result(0, "x"), None),             # zero is not a measurement
    (_local_result(-5, "x"), None),            # nonsense is not a measurement
    (types.SimpleNamespace(output="x", usage="not-a-dict"), None),
    (types.SimpleNamespace(output="x", usage={"total_tokens": "abc"}), None),
    (types.SimpleNamespace(output="x", usage={}), None),
    (None, None),
])
def test_tokens_of_is_fail_closed(result, expected):
    assert RealTdeOrchestrator._tokens_of(result) == expected


def test_output_of_stringifies_non_str():
    assert RealTdeOrchestrator._output_of(_local_result(1, "plain")) == "plain"
    dumped = RealTdeOrchestrator._output_of(
        types.SimpleNamespace(output={"k": "v"}, usage=None))
    assert json.loads(dumped) == {"k": "v"}
    assert RealTdeOrchestrator._output_of(
        types.SimpleNamespace(output=None, usage=None)) == ""


# ---------------------------------------------------------------------------
# Sample validation — the last fail-closed backstop
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("field", ["direct_tokens", "tier_tokens", "tde_tokens"])
def test_zero_tokens_rejected(field):
    """A zero token count is the k=4 defect's signature. It must not persist."""
    with pytest.raises(ValueError, match="must be > 0"):
        _sample(**{field: 0})


@pytest.mark.parametrize("field", ["direct_tokens", "tier_tokens", "tde_tokens"])
def test_negative_tokens_rejected(field):
    with pytest.raises(ValueError, match="must be > 0"):
        _sample(**{field: -1})


def test_unknown_band_rejected():
    """Literal annotations are not enforced at runtime, so this needs a real check.

    An unknown band would create a phantom evidence group that never reaches
    min_samples_per_band, quietly starving the verdict instead of failing loudly.
    """
    with pytest.raises(ValueError, match="task_band must be one of"):
        _sample(task_band="enormous")


@pytest.mark.parametrize("band", VALID_BANDS)
def test_all_valid_bands_accepted(band):
    assert _sample(task_band=band).task_band == band


@pytest.mark.parametrize("loss", [-0.01, 1.01, 42.0])
def test_out_of_range_losses_rejected(loss):
    with pytest.raises(ValueError, match=r"must be in \[0.0, 1.0\]"):
        _sample(tde_loss=loss)


def test_band_classification_defaults_to_middle():
    """Unknown classifier output must not land in trivial (TDE's best-looking
    band) or complex (its worst) — either would bias the evidence."""
    assert classify_band("trivial") == "trivial"
    assert classify_band("complex") == "complex"
    assert classify_band("moderate") == "moderate"
    assert classify_band(None) == "moderate"
    assert classify_band("some-new-classifier-label") == "moderate"


def test_simple_maps_to_the_trivial_band():
    """The classifier says "simple"; the gate's band is called "trivial".

    Unmapped, every simple task landed in the moderate band — the trivial band
    then stayed at n_measured=0 forever (permanently INSUFFICIENT_DATA) while the
    moderate band was diluted with cheaper tasks. See send_integration's
    `complexity == "simple"` test and loss_profile_tracker's documented
    simple/moderate/complex bucket.
    """
    assert classify_band("simple") == "trivial"


@pytest.mark.parametrize("raw,expected", [
    ("SIMPLE", "trivial"), ("  simple  ", "trivial"), ("Complex", "complex"),
    ("MODERATE", "moderate"), ("medium", "moderate"), ("hard", "complex"),
])
def test_band_classification_is_case_and_whitespace_tolerant(raw, expected):
    """A classifier is an LLM: casing and stray whitespace must not silently
    reroute a whole band."""
    assert classify_band(raw) == expected


def test_every_band_the_classifier_can_produce_is_a_valid_gate_band():
    """Whatever classify_band returns must be constructible as a sample —
    otherwise __post_init__ rejects real measurements at the last step."""
    for raw in ("simple", "moderate", "complex", "trivial", None, "weird"):
        assert classify_band(raw) in VALID_BANDS


# ---------------------------------------------------------------------------
# Orchestration — happy path and every refusal path
# ---------------------------------------------------------------------------

def _patch_arms(monkeypatch, *, direct=None, tier=None,
                tier_loss=2.0, tde_loss=5.0, direct_exc=None):
    """Wire fake baselines + judge into the orchestrator's late imports."""
    async def _fake_direct(statement, *, user_model=None, proc_holder=None):
        if direct_exc is not None:
            raise direct_exc
        return direct if direct is not None else _local_result(5000, "direct answer")

    async def _fake_tier(statement, **kw):
        return tier if tier is not None else _local_result(4000, "tier answer")

    judged: list[tuple] = []

    def _fake_judge(desc, reference, candidate, **kw):
        judged.append((desc, reference, candidate))
        # First call is tier-vs-direct, second is TDE-vs-direct.
        return tier_loss if len(judged) == 1 else tde_loss

    monkeypatch.setattr(tde_engine, "whole_task_direct_baseline", _fake_direct)
    monkeypatch.setattr(tde_engine, "whole_task_tier_baseline", _fake_tier)
    monkeypatch.setattr("tde.loss_judge.judge_loss_sync", _fake_judge)
    return judged


def _orchestrate(**over):
    kw = dict(task_id="run-1", task_text="do the thing",
              tde_tokens=3000, tde_output="tde answer",
              task_complexity="moderate", user_model="claude-opus-5")
    kw.update(over)
    return asyncio.run(RealTdeOrchestrator.orchestrate(**kw))


def test_orchestrate_happy_path_builds_measured_sample(monkeypatch):
    judged = _patch_arms(monkeypatch, tier_loss=2.0, tde_loss=5.0)
    sample = _orchestrate()

    assert sample is not None
    assert sample.task_id == "run-1"
    assert sample.task_band == "moderate"
    assert sample.direct_tokens == 5000
    assert sample.tier_tokens == 4000
    assert sample.tde_tokens == 3000
    # Judge speaks percent; the sample stores a fraction.
    assert sample.tier_loss == pytest.approx(0.02)
    assert sample.tde_loss == pytest.approx(0.05)
    assert sample.data_source == "measured"
    assert len(judged) == 2


def test_orchestrate_judges_both_arms_against_direct(monkeypatch):
    """Direct is THE reference: both comparisons must use it as answer A."""
    judged = _patch_arms(monkeypatch)
    _orchestrate(tde_output="tde answer")

    tier_call, tde_call = judged
    assert tier_call[1] == "direct answer", "tier must be judged vs direct"
    assert tier_call[2] == "tier answer"
    assert tde_call[1] == "direct answer", "TDE must be judged vs direct"
    assert tde_call[2] == "tde answer"


def test_orchestrate_judges_exactly_the_output_it_was_given(monkeypatch):
    """The orchestrator must not decorate the TDE answer.

    Badge stripping happens at the caller (chat_runtime captures the bare output
    before appending the UI badge); this pins that the orchestrator passes the
    value through untouched, so the two halves cannot both assume the other does it.
    """
    judged = _patch_arms(monkeypatch)
    _orchestrate(tde_output="exact text ⚙ not a badge")
    assert judged[1][2] == "exact text ⚙ not a badge"


@pytest.mark.parametrize("bad_tokens", [0, -1, None, "1200"])
def test_orchestrate_refuses_unmeasured_tde_tokens(monkeypatch, bad_tokens):
    """THE k=4 defect: an unwired token field must drop the sample.

    Also asserts the refusal is CHEAP — it happens before any baseline runs, so a
    doomed sample never spends two LLM turns.
    """
    spent: list[str] = []

    async def _never(*a, **kw):
        spent.append("baseline")
        return _local_result(1, "x")

    monkeypatch.setattr(tde_engine, "whole_task_direct_baseline", _never)
    monkeypatch.setattr(tde_engine, "whole_task_tier_baseline", _never)

    assert _orchestrate(tde_tokens=bad_tokens) is None
    assert spent == [], "must refuse before spending baseline turns"


@pytest.mark.parametrize("empty", ["", "   ", "\n"])
def test_orchestrate_refuses_empty_tde_output(monkeypatch, empty):
    _patch_arms(monkeypatch)
    assert _orchestrate(tde_output=empty) is None


def test_orchestrate_refuses_when_baseline_usage_missing(monkeypatch):
    """An uninstrumented baseline yields no denominator — no sample."""
    _patch_arms(monkeypatch, direct=_local_result(None, "direct answer"))
    assert _orchestrate() is None


def test_orchestrate_refuses_when_baseline_tokens_zero(monkeypatch):
    _patch_arms(monkeypatch, tier=_local_result(0, "tier answer"))
    assert _orchestrate() is None


def test_orchestrate_refuses_when_direct_output_empty(monkeypatch):
    """No reference text means nothing to judge against."""
    _patch_arms(monkeypatch, direct=_local_result(5000, "   "))
    assert _orchestrate() is None


def test_orchestrate_refuses_when_baseline_raises(monkeypatch):
    _patch_arms(monkeypatch, direct_exc=RuntimeError("claude CLI exit 1"))
    assert _orchestrate() is None


@pytest.mark.parametrize("tier_loss,tde_loss", [(None, 5.0), (2.0, None), (None, None)])
def test_orchestrate_refuses_when_judge_has_no_verdict(monkeypatch, tier_loss, tde_loss):
    """judge_loss_sync returns None when it cannot score.

    Substituting 0.0 (or a lexical fallback) would book a fabricated quality
    score — precisely the defect ADR-0222 F1 was written to close.
    """
    _patch_arms(monkeypatch, tier_loss=tier_loss, tde_loss=tde_loss)
    assert _orchestrate() is None


def test_orchestrate_propagates_cancellation(monkeypatch):
    """Cancellation is control flow, not a measurement failure — it must not be
    swallowed into a silent None."""
    _patch_arms(monkeypatch, direct_exc=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        _orchestrate()


def test_orchestrate_records_the_configured_judge_model(monkeypatch):
    _patch_arms(monkeypatch)
    monkeypatch.setenv("CORVIN_TDE_JUDGE_MODEL", "claude-opus-5")
    assert _orchestrate().quality_judge_model == "claude-opus-5"


# ---------------------------------------------------------------------------
# Persistence — data minimisation + a lossless round-trip
# ---------------------------------------------------------------------------

def test_outputs_redacted_by_default(monkeypatch):
    """The gate reads tokens+losses only, so raw answers must not hit disk."""
    monkeypatch.delenv(PERSIST_OUTPUTS_ENV, raising=False)
    data = _sample(direct_output="user's private answer").to_persistable_dict()

    assert data["direct_output"] == "<redacted>"
    assert "private" not in json.dumps(data)
    # Lengths survive, so a suspicious loss score is still debuggable.
    assert data["direct_output_chars"] == len("user's private answer")
    # The figures the gate consumes are untouched.
    assert data["direct_tokens"] == 5000
    assert data["tde_loss"] == 0.05


def test_outputs_persisted_only_under_explicit_optin(monkeypatch):
    monkeypatch.setenv(PERSIST_OUTPUTS_ENV, "1")
    data = _sample(direct_output="verbatim").to_persistable_dict()
    assert data["direct_output"] == "verbatim"
    assert "direct_output_chars" not in data


def test_redacted_log_round_trips(monkeypatch, tmp_path):
    """The DEFAULT (redacted) log format must be re-loadable.

    The redaction adds *_chars keys that are not constructor args, so a loader
    that passed them straight through would raise TypeError on every line and
    lose the entire log — while only logging a warning.
    """
    monkeypatch.setenv("TDE_MEASUREMENT_ENABLED", "1")
    monkeypatch.delenv(PERSIST_OUTPUTS_ENV, raising=False)
    log = tmp_path / "measurement.jsonl"

    MeasurementRecorder.reset_instance()
    rec = MeasurementRecorder.get_instance(str(log))
    asyncio.run(rec.record_sample(_sample(task_id="a")))
    asyncio.run(rec.record_sample(_sample(task_id="b", task_band="trivial")))

    fresh = MeasurementRecorder(str(log))
    fresh.load_from_log()
    assert [s.task_id for s in fresh.samples] == ["a", "b"]
    assert fresh.samples[0].direct_tokens == 5000
    MeasurementRecorder.reset_instance()


def test_load_from_log_is_idempotent(monkeypatch, tmp_path):
    """Repeated loads must not inflate n_measured.

    n_measured is the gate's sample-size guard; double-counting is exactly how
    thin evidence sneaks past min_samples_per_band.
    """
    monkeypatch.setenv("TDE_MEASUREMENT_ENABLED", "1")
    log = tmp_path / "measurement.jsonl"

    MeasurementRecorder.reset_instance()
    rec = MeasurementRecorder.get_instance(str(log))
    asyncio.run(rec.record_sample(_sample(task_id="a")))

    rec.load_from_log()
    first = len(rec.samples)
    rec.load_from_log()
    rec.load_from_log()
    assert len(rec.samples) == first == 1

    evidence = rec.get_aggregated_evidence()
    assert [e.n_measured for e in evidence] == [1]
    assert all(e.data_source == "measured" for e in evidence)
    MeasurementRecorder.reset_instance()
