"""
Phase 3 K=4 E2E Proof: Complete A1→A2→A3→A4→B→C Learning Loop

End-to-end scenario:
1. A1: Emits HistogramBucket (10 outcomes, 85% confidence)
2. A2: Validates + audits + enqueues OutcomeRecord
3. A3: Computes ConfidenceScore (delta=0.75, trend=improving)
4. A4: Generates OptimizerConfig (config_delta=+0.075)
5. Stream B: Enriches outcome with confidence + config
6. Stream C: Displays all 3 panels in dashboard

Validates:
- Message contracts end-to-end
- Non-blocking async semantics
- Tenant isolation
- Audit chain integrity
- Dashboard readiness (<500ms total)

References: ADR-2086/2087/2088/2089 (Stream A/B/C)

2026-09-27 (adversarial review): A3 used to fabricate its success/escalation
counts, so this loop "worked" on any input. A3 now scores only records that
carry MEASURED counts, and every stage commits to the tenant's core audit
chain — so the records below carry counts and each test runs as its tenant.
"""

import pytest
from datetime import datetime
from dataclasses import dataclass
from core.learning.outcome_sink_a2 import OutcomeSink, OutcomeRecord
from core.learning.confidence_scorer_a3 import ConfidenceScorer
from core.learning.meta_optimizer_a4 import MetaOptimizer
from core.learning.audit_event_enricher_b import AuditEventEnricher
from core.console.corvin_console.routes.stream_c_dashboard import StreamCDashboard


@dataclass(frozen=True)
class MockHistogramBucket:
    """A1 output (simulated)"""
    skill_id: str = "os.delegation_router"
    outcome_count: int = 10
    avg_confidence: float = 0.85
    audit_ref: str = "a1_audit_stub"


@pytest.fixture(autouse=True)
def _chain_env(monkeypatch):
    monkeypatch.delenv("VOICE_AUDIT_PATH", raising=False)
    yield monkeypatch


def _as_tenant(monkeypatch, tid):
    monkeypatch.setenv("CORVIN_TENANT_ID", tid)


class TestPhase3E2ECompleteLearningLoop:
    """
    K=4: End-to-end proof that all streams work together.
    """

    def test_e2e_bucket_without_counts_is_not_scored(self, _chain_env):
        """A1 buckets carry no success split — A3 must not invent one."""
        _as_tenant(_chain_env, "_prod")
        b = MockHistogramBucket()
        rec = OutcomeRecord(skill_id=b.skill_id, outcome_count=b.outcome_count,
                            avg_confidence=b.avg_confidence, audit_ref=b.audit_ref)
        assert ConfidenceScorer(tenant_id="_prod").score(rec) is None

    def test_e2e_a1_to_a4_b_c_complete_flow(self, _chain_env):
        """
        Complete flow: A1 → A2 → A3 → A4 → B → C

        Verifies:
        - All message contracts satisfied
        - Data flows through all 3 streams
        - Dashboard panels populated
        - Total latency <500ms
        """
        tenant_id = "_prod"
        _as_tenant(_chain_env, tenant_id)

        # Setup all components
        a2_sink = OutcomeSink(tenant_id=tenant_id)
        a3_scorer = ConfidenceScorer(tenant_id=tenant_id)
        a4_optimizer = MetaOptimizer(tenant_id=tenant_id)
        stream_b = AuditEventEnricher(tenant_id=tenant_id)
        stream_c = StreamCDashboard(tenant_id=tenant_id)

        import time
        start_time = time.time()

        # Phase 1: A1 → A2 (simulated as OutcomeRecord)
        bucket = MockHistogramBucket()
        a2_record = OutcomeRecord(
            skill_id=bucket.skill_id,
            outcome_count=bucket.outcome_count,
            avg_confidence=bucket.avg_confidence,
            audit_ref=bucket.audit_ref,
            success_count=8, failed_count=1, timed_out_count=1,  # measured split
        )

        # Phase 2: A2 → A3
        a3_score = a3_scorer.score(a2_record)
        assert a3_score is not None, "A3 should process A2 output"

        # Phase 3: A3 → A4
        a4_config = a4_optimizer.optimize(a3_score)
        assert a4_config is not None, "A4 should process A3 output"

        # Phase 4: A2 + A3 → Stream B
        b_event = stream_b.enrich(a2_record, a3_score)
        assert b_event is not None, "Stream B should enrich A2+A3"

        # Phase 5: B + A4 → Stream C
        b_ingested = stream_c.ingest_enriched_event(b_event)
        assert b_ingested, "Stream C should ingest B event"

        a4_ingested = stream_c.ingest_optimizer_config(a4_config)
        assert a4_ingested, "Stream C should ingest A4 config"

        # Phase 6: Dashboard readiness
        panels = stream_c.get_all_panels()
        assert panels is not None
        assert "panel_1" in panels  # Confidence scoreboard
        assert "panel_2" in panels  # Config delta chart
        assert "panel_3" in panels  # Trend gauge

        elapsed_ms = (time.time() - start_time) * 1000

        # Verify all contracts
        assert a3_score.skill_id == "os.delegation_router"
        assert a4_config.skill_id == "os.delegation_router"
        assert b_event.skill_id == "os.delegation_router"

        # Verify data consistency
        assert a3_score.confidence_delta == b_event.confidence_delta
        assert a4_config.config_delta == panels["panel_2"]["data"][0]["config_delta"]

        # Verify SLA
        assert elapsed_ms < 500, f"End-to-end latency {elapsed_ms:.0f}ms exceeds 500ms SLA"

    def test_e2e_tenant_isolation_across_streams(self, _chain_env):
        """Verify tenant isolation held across all 3 streams."""
        tenant_a = "_tenant_a"
        tenant_b = "_tenant_b"

        # Setup separate streams per tenant
        a3_a = ConfidenceScorer(tenant_id=tenant_a)
        a3_b = ConfidenceScorer(tenant_id=tenant_b)

        a4_a = MetaOptimizer(tenant_id=tenant_a)
        a4_b = MetaOptimizer(tenant_id=tenant_b)

        b_a = AuditEventEnricher(tenant_id=tenant_a)
        b_b = AuditEventEnricher(tenant_id=tenant_b)

        c_a = StreamCDashboard(tenant_id=tenant_a)
        c_b = StreamCDashboard(tenant_id=tenant_b)

        # Process separate outcomes per tenant
        record_a = OutcomeRecord(
            skill_id="skill_a",
            outcome_count=5,
            avg_confidence=0.8,
            audit_ref="ref_a",
            success_count=4, failed_count=1, timed_out_count=0,
        )
        record_b = OutcomeRecord(
            skill_id="skill_b",
            outcome_count=10,
            avg_confidence=0.7,
            audit_ref="ref_b",
            success_count=7, failed_count=2, timed_out_count=1,
        )

        _as_tenant(_chain_env, tenant_a)
        score_a = a3_a.score(record_a)
        config_a = a4_a.optimize(score_a)
        event_a = b_a.enrich(record_a, score_a)
        _as_tenant(_chain_env, tenant_b)
        score_b = a3_b.score(record_b)
        config_b = a4_b.optimize(score_b)
        event_b = b_b.enrich(record_b, score_b)
        # A score of tenant A is refused by tenant B's optimizer / enricher
        assert a4_b.optimize(score_a) is None
        assert b_b.enrich(record_a, score_a) is None

        # Verify isolation
        assert score_a.tenant_id == tenant_a
        assert score_b.tenant_id == tenant_b

        assert config_a.tenant_id == tenant_a
        assert config_b.tenant_id == tenant_b

        # Stream B should also respect isolation
        assert event_a.tenant_id == tenant_a
        assert event_b.tenant_id == tenant_b

    def test_e2e_audit_chain_integrity(self, _chain_env):
        """Verify audit refs flow through all streams — and are REAL chain records."""
        import json
        from core.paths import tenant_audit_chain

        _as_tenant(_chain_env, "_test")
        a2_record = OutcomeRecord(
            skill_id="os.skill",
            outcome_count=10,
            avg_confidence=0.85,
            audit_ref="a2_ref_xyz",
            success_count=9, failed_count=1, timed_out_count=0,
        )

        a3 = ConfidenceScorer(tenant_id="_test")
        score = a3.score(a2_record)

        assert score.audit_ref is not None
        a3_audit = score.audit_ref

        b = AuditEventEnricher(tenant_id="_test")
        event = b.enrich(a2_record, score)

        # Both A2 and A3 refs should be preserved in B
        assert event.a2_audit_ref == "a2_ref_xyz"
        assert event.a3_audit_ref == a3_audit
        refs = {json.loads(ln)["details"].get("audit_ref")
                for ln in tenant_audit_chain("_test").read_text().splitlines() if ln.strip()}
        assert {a3_audit, event.audit_ref} <= refs


if __name__ == "__main__":
    pytest.main([__file__, "-xvs"])
