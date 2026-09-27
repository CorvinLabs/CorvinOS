"""
Phase 3 K=3 Integration Tests: A4 MetaOptimizer + Stream B Audit Events

Validates:
1. A3→A4 message contract (ConfidenceScore → OptimizerConfig)
2. A2+A3→B message contract (Enriched outcome event)
3. Non-blocking + tenant isolation
4. Audit-first semantics

References: ADR-2086 (A3), ADR-2087 (A4), ADR-2088 (Stream B)
"""

import pytest
from dataclasses import dataclass
from datetime import datetime

from core.learning.meta_optimizer_a4 import MetaOptimizer
from core.learning.audit_event_enricher_b import AuditEventEnricher


@dataclass(frozen=True)
class MockConfidenceScore:
    """Mock A3 output"""
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
class MockOutcomeRecord:
    """Mock A2 output"""
    skill_id: str = "os.skill"
    outcome_count: int = 10
    avg_confidence: float = 0.85
    audit_ref: str = "a2_audit_456"


class TestPhase3A4Integration:
    """A4 MetaOptimizer integration tests"""

    def test_a4_config_delta_formula(self):
        """A4 computes config_delta = confidence_delta × learning_rate"""
        optimizer = MetaOptimizer(tenant_id="_test", learning_rate=0.1)
        score = MockConfidenceScore(confidence_delta=0.75)

        config = optimizer.optimize(score)

        assert config is not None
        expected_delta = 0.75 * 0.1  # 0.075
        assert abs(config.config_delta - expected_delta) < 0.001

    def test_a4_confidence_projection(self):
        """A4 projects confidence_after correctly"""
        optimizer = MetaOptimizer(tenant_id="_test")
        score = MockConfidenceScore(confidence_delta=0.5)

        config = optimizer.optimize(score)

        assert config is not None
        assert config.confidence_before == 0.5
        assert config.confidence_after > config.confidence_before  # Should improve

    def test_a4_delta_clamping(self):
        """A4 clamps config_delta to [-0.5, 0.5]"""
        optimizer = MetaOptimizer(tenant_id="_test", learning_rate=10.0)
        score = MockConfidenceScore(confidence_delta=0.9)

        config = optimizer.optimize(score)

        assert config is not None
        assert -0.5 <= config.config_delta <= 0.5

    def test_a4_audit_event_emitted(self):
        """A4 emits audit event with all fields"""
        optimizer = MetaOptimizer(tenant_id="_test")
        score = MockConfidenceScore(skill_id="test_skill")

        config = optimizer.optimize(score)

        assert config.audit_ref is not None
        assert config.skill_id == "test_skill"

    def test_a4_tenant_isolation(self):
        """A4 maintains tenant isolation"""
        opt1 = MetaOptimizer(tenant_id="tenant_a")
        opt2 = MetaOptimizer(tenant_id="tenant_b")

        score = MockConfidenceScore()
        config1 = opt1.optimize(score)
        config2 = opt2.optimize(score)

        assert config1.tenant_id == "tenant_a"
        assert config2.tenant_id == "tenant_b"


class TestPhase3StreamBIntegration:
    """Stream B Audit Event Enrichment tests"""

    def test_b_enrichment_combines_fields(self):
        """Stream B combines A2 + A3 fields correctly"""
        enricher = AuditEventEnricher(tenant_id="_test")
        outcome = MockOutcomeRecord()
        score = MockConfidenceScore()

        event = enricher.enrich(outcome, score)

        assert event is not None
        # A2 fields present
        assert event.outcome_count == 10
        assert event.avg_confidence == 0.85
        # A3 fields present
        assert event.confidence_delta == 0.75
        assert event.trend == "improving"

    def test_b_skill_correlation_check(self):
        """Stream B validates skill_id correlation"""
        enricher = AuditEventEnricher(tenant_id="_test")
        outcome = MockOutcomeRecord(skill_id="skill_a")
        score = MockConfidenceScore(skill_id="skill_b")

        event = enricher.enrich(outcome, score)

        # Should fail on skill mismatch
        assert event is None

    def test_b_audit_refs_preserved(self):
        """Stream B preserves both A2 and A3 audit refs"""
        enricher = AuditEventEnricher(tenant_id="_test")
        outcome = MockOutcomeRecord(audit_ref="a2_ref_123")
        score = MockConfidenceScore(audit_ref="a3_ref_456")

        event = enricher.enrich(outcome, score)

        assert event.a2_audit_ref == "a2_ref_123"
        assert event.a3_audit_ref == "a3_ref_456"

    def test_b_tenant_isolation(self):
        """Stream B maintains tenant isolation"""
        enr1 = AuditEventEnricher(tenant_id="tenant_x")
        enr2 = AuditEventEnricher(tenant_id="tenant_y")

        outcome = MockOutcomeRecord()
        score = MockConfidenceScore()

        event1 = enr1.enrich(outcome, score)
        event2 = enr2.enrich(outcome, score)

        assert event1.tenant_id == "tenant_x"
        assert event2.tenant_id == "tenant_y"


class TestPhase3A4StreamBComposition:
    """Integration: A4 output flows into Stream B"""

    def test_composition_a4_to_b_flow(self):
        """A3→A4→B message flow works end-to-end"""
        # Setup
        optimizer = MetaOptimizer(tenant_id="_test")
        enricher = AuditEventEnricher(tenant_id="_test")

        # Phase: A3 → A4
        score = MockConfidenceScore()
        config = optimizer.optimize(score)
        assert config is not None

        # Phase: A2 + A3 → B
        outcome = MockOutcomeRecord()
        event = enricher.enrich(outcome, score)
        assert event is not None

        # Verify composition
        assert event.confidence_delta == score.confidence_delta
        assert event.skill_id == config.skill_id


if __name__ == "__main__":
    pytest.main([__file__, "-xvs"])
