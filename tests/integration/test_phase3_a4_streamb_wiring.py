"""
Phase 3: A4 MetaOptimizer + Stream B enrichment.

Rewritten 2026-09-27 (adversarial review): both modules returned a stub uuid
as "audit_ref"; they now commit to the tenant's core audit chain. These tests
assert the formulas, the chain records, fail-closed on audit failure, and
refusal of another tenant's score.
References: ADR-2086 (A3), ADR-2087 (A4), ADR-2088 (Stream B)
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

import pytest

from core.learning.audit_event_enricher_b import AuditEventEnricher
from core.learning.meta_optimizer_a4 import MetaOptimizer
from core.paths import tenant_audit_chain


@dataclass(frozen=True)
class Score:
    skill_id: str = "os.skill"
    outcome_count: int = 10
    success_count: int = 9
    escalation_count: int = 1
    confidence_delta: float = 0.75
    trend: str = "improving"
    audit_ref: str = "a3_audit_123"
    tenant_id: str = "_test"
    timestamp: datetime = None


@dataclass(frozen=True)
class Outcome:
    skill_id: str = "os.skill"
    outcome_count: int = 10
    avg_confidence: float = 0.85
    audit_ref: str = "a2_audit_456"


@pytest.fixture(autouse=True)
def _tenant(monkeypatch):
    monkeypatch.setenv("CORVIN_TENANT_ID", "_test")
    monkeypatch.delenv("VOICE_AUDIT_PATH", raising=False)


def _last(event_type, tid="_test"):
    p = tenant_audit_chain(tid)
    recs = [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()]
    return [r for r in recs if r["event_type"] == event_type][-1]


def test_a4_config_delta_formula_and_projection():
    config = MetaOptimizer(tenant_id="_test", learning_rate=0.1).optimize(Score(confidence_delta=0.5))
    assert config.config_delta == pytest.approx(0.05)
    assert config.confidence_after == pytest.approx(0.55)


def test_a4_delta_clamped():
    config = MetaOptimizer(tenant_id="_test", learning_rate=10.0).optimize(Score(confidence_delta=0.9))
    assert config.config_delta == 0.5


def test_a4_recommendation_is_on_the_chain():
    config = MetaOptimizer(tenant_id="_test").optimize(Score(skill_id="test_skill"))
    rec = _last("learning.optimizer_config_updated")
    assert rec["details"]["audit_ref"] == config.audit_ref
    assert rec["details"]["skill_id"] == "test_skill"
    assert rec["details"]["source_audit_ref"] == "a3_audit_123"


def test_a4_refuses_another_tenants_score():
    assert MetaOptimizer(tenant_id="_test").optimize(Score(tenant_id="tenant_b")) is None


def test_a4_audit_failure_propagates(monkeypatch):
    import core.learning.event_persistence as ep

    monkeypatch.setattr(ep, "core_audit_event",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no commit")))
    with pytest.raises(RuntimeError):
        MetaOptimizer(tenant_id="_test").optimize(Score())


def test_b_enrichment_combines_fields_and_commits():
    event = AuditEventEnricher(tenant_id="_test").enrich(Outcome(), Score())
    assert (event.outcome_count, event.avg_confidence) == (10, 0.85)
    assert (event.confidence_delta, event.trend) == (0.75, "improving")
    assert (event.a2_audit_ref, event.a3_audit_ref) == ("a2_audit_456", "a3_audit_123")
    rec = _last("learning.enriched_outcome_event")
    assert rec["details"]["audit_ref"] == event.audit_ref
    assert rec["details"]["a2_audit_ref"] == "a2_audit_456"


def test_b_skill_mismatch_rejected():
    assert AuditEventEnricher(tenant_id="_test").enrich(Outcome(skill_id="a"), Score(skill_id="b")) is None


def test_b_refuses_another_tenants_score():
    assert AuditEventEnricher(tenant_id="_test").enrich(Outcome(), Score(tenant_id="tenant_y")) is None


def test_composition_a4_to_b():
    config = MetaOptimizer(tenant_id="_test").optimize(Score())
    event = AuditEventEnricher(tenant_id="_test").enrich(Outcome(), Score())
    assert event.skill_id == config.skill_id
    assert event.audit_ref != config.audit_ref
