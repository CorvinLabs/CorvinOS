"""
Stream A: A2 -> A3 wiring + Phase 2 scoring (escalation penalty, trend).

Rewritten 2026-09-27 (adversarial review): the previous tests asserted
``trend in ["improving", "stable", "degrading"]`` (always true) against a scorer
that fabricated its success/escalation counts and wrote a stub audit ref.
These assert the formula on MEASURED counts, the trend direction, the
not-measured path, and a real record on the tenant's core audit chain.
References: ADR-2086 (Phase 2), ADR-2075 (Message Contract), ADR-0081 (Event Schema)
"""
from __future__ import annotations

import json
from dataclasses import dataclass, FrozenInstanceError

import pytest

from core.learning.confidence_scorer_a3 import ConfidenceScorer
from core.paths import tenant_audit_chain


@dataclass(frozen=True)
class CountedOutcome:
    """A2 output carrying measured counts (what A3 needs)."""
    skill_id: str = "os.delegation_router"
    outcome_count: int = 10
    success_count: int = 8
    failed_count: int = 1
    timed_out_count: int = 1
    avg_confidence: float = 0.85
    audit_ref: str = "audit_abc123"


@dataclass(frozen=True)
class BareOutcome:
    """A2's current OutcomeRecord shape: no success/failure counts."""
    skill_id: str = "os.delegation_router"
    outcome_count: int = 10
    avg_confidence: float = 0.85
    audit_ref: str = "audit_abc123"


@pytest.fixture(autouse=True)
def _tenant(monkeypatch):
    monkeypatch.setenv("CORVIN_TENANT_ID", "_test")
    monkeypatch.delenv("VOICE_AUDIT_PATH", raising=False)


def _chain(tid="_test"):
    p = tenant_audit_chain(tid)
    return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()] if p.exists() else []


def test_escalation_penalty_on_measured_counts():
    score = ConfidenceScorer(tenant_id="_test").score(CountedOutcome())
    # success_rate 0.8 - escalation_rate 0.2 * 0.1
    assert score.confidence_delta == pytest.approx(0.78)
    assert score.success_count == 8 and score.escalation_count == 2


def test_record_without_counts_is_not_scored():
    assert ConfidenceScorer(tenant_id="_test").score(BareOutcome()) is None


def test_inconsistent_counts_are_rejected():
    bad = CountedOutcome(outcome_count=5, success_count=5, failed_count=3, timed_out_count=0)
    assert ConfidenceScorer(tenant_id="_test").score(bad) is None


def test_trend_improving_when_recent_deltas_rise():
    scorer = ConfidenceScorer(tenant_id="_test", window_size=10)
    for i in range(10):
        score = scorer.score(CountedOutcome(success_count=i, failed_count=0,
                                            timed_out_count=10 - i))
    assert score.trend == "improving"


def test_trend_degrading_when_recent_deltas_fall():
    scorer = ConfidenceScorer(tenant_id="_test", window_size=10)
    for i in range(10):
        score = scorer.score(CountedOutcome(success_count=10 - i, failed_count=i,
                                            timed_out_count=0))
    assert score.trend == "degrading"


def test_rolling_window_is_bounded():
    scorer = ConfidenceScorer(tenant_id="_test", window_size=5)
    for _ in range(10):
        scorer.score(CountedOutcome())
    assert len(scorer.get_trend_history()) == 5


def test_score_is_immutable():
    score = ConfidenceScorer(tenant_id="_test").score(CountedOutcome())
    with pytest.raises(FrozenInstanceError):
        score.confidence_delta = 1.0


def test_score_is_committed_to_the_tenant_chain():
    score = ConfidenceScorer(tenant_id="_test").score(CountedOutcome(skill_id="test_skill"))
    rec = [r for r in _chain() if r["event_type"] == "learning.confidence_scored"][-1]
    assert rec["details"]["audit_ref"] == score.audit_ref
    assert rec["details"]["skill_id"] == "test_skill"
    assert rec["details"]["source_audit_ref"] == "audit_abc123"


def test_audit_failure_propagates(monkeypatch):
    import core.learning.event_persistence as ep

    def boom(*a, **k):
        raise RuntimeError("no commit")

    monkeypatch.setattr(ep, "core_audit_event", boom)
    with pytest.raises(RuntimeError):
        ConfidenceScorer(tenant_id="_test").score(CountedOutcome())


def test_invalid_tenant_rejected():
    with pytest.raises(ValueError):
        ConfidenceScorer(tenant_id="../x")
