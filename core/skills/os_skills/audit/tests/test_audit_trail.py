"""Unit tests for AuditTrail, ComplianceReporter, PrometheusExporter.

Since 2026-09-27 AuditTrail is a view onto the tenant's CORE audit chain
(``forge.security_events.write_event`` on ``tenant_audit_chain``); the
autouse conftest fixture points CORVIN_HOME at a scratch directory.
"""
import json
from pathlib import Path

import pytest

from ..trail import AuditTrail, EVENT_PREFIX
from ..reporter import ComplianceReporter
from ..prometheus import PrometheusExporter
from ..api import LearningDashboardAPI


def _lines(trail: AuditTrail) -> list[dict]:
    return [json.loads(l) for l in trail.chain_path.read_text().splitlines() if l.strip()]


class TestAuditTrail:
    """The trail writes chained records to the tenant's core chain."""

    def test_write_event_lands_in_the_core_chain(self, _scratch_corvin_home):
        trail = AuditTrail(tenant_id="test_tenant")
        event = trail.write_event(
            event_type="skill_generated", skill_id="skill_123",
            payload={"loss_before": 0.6, "loss_after": 0.45},
        )
        assert event.event_type == "skill_generated"
        assert event.skill_id == "skill_123"
        assert trail.chain_path == (
            _scratch_corvin_home / "tenants" / "test_tenant" / "global" / "forge" / "audit.jsonl"
        )
        record = _lines(trail)[-1]
        assert record["event_type"] == EVENT_PREFIX + "skill_generated"
        assert record["details"]["loss_after"] == 0.45
        assert record["hash"] == event.hash

        from forge.security_events import verify_chain
        assert verify_chain(trail.chain_path)[0]

    def test_hash_chain_links_across_events(self):
        trail = AuditTrail(tenant_id="test_tenant")
        e1 = trail.write_event("feedback_received", skill_id="s1", payload={"signal": "positive"})
        e2 = trail.write_event("feedback_received", skill_id="s2", payload={"signal": "negative"})
        e3 = trail.write_event("weight_updated", skill_id="s3", payload={"change_pct": 3.0})
        assert e2.prev_hash == e1.hash
        assert e3.prev_hash == e2.hash

    def test_private_chain_path_is_not_implemented(self, tmp_path):
        with pytest.raises(NotImplementedError):
            AuditTrail(tenant_id="t-audit", chain_path=tmp_path / "audit.jsonl")
        assert not (tmp_path / "audit.jsonl").exists()

    def test_the_canonical_path_may_be_passed_explicitly(self):
        from core.paths.tenant import tenant_audit_chain
        trail = AuditTrail(tenant_id="t-audit", chain_path=tenant_audit_chain("t-audit"))
        assert trail.chain_path == tenant_audit_chain("t-audit")

    def test_unknown_event_type_refused(self):
        trail = AuditTrail(tenant_id="t-audit")
        with pytest.raises(ValueError, match="unknown DataHub audit event type"):
            trail.write_event("event_1", payload={"n": 1})
        assert not trail.chain_path.exists()

    def test_payload_outside_vocabulary_refused(self):
        trail = AuditTrail(tenant_id="t-audit")
        with pytest.raises(ValueError, match="vocabulary"):
            trail.write_event("feedback_received",
                              payload={"email": "user@example.com", "signal": "positive"})
        assert not trail.chain_path.exists()

    def test_pii_value_in_allowed_key_is_never_written_raw(self):
        trail = AuditTrail(tenant_id="t-audit")
        with pytest.raises(ValueError, match="dropped"):
            trail.write_event("weight_updated",
                              payload={"reason": "mail me at user@example.com"})
        assert "user@example.com" not in trail.chain_path.read_text()

    def test_query_events_filters_by_tenant(self):
        trail1 = AuditTrail(tenant_id="tenant_1")
        trail1.write_event("feedback_received", skill_id="s1", payload={"signal": "positive"})
        events = trail1.query_events(limit=10)
        assert len(events) == 1
        assert events[0].tenant_id == "tenant_1"
        assert AuditTrail(tenant_id="tenant_2").query_events(limit=10) == []

    def test_verify_detects_tampering(self):
        trail = AuditTrail(tenant_id="t-audit")
        trail.write_event("weight_updated", payload={"change_pct": 1.0})
        lines = trail.chain_path.read_text().splitlines()
        rec = json.loads(lines[-1])
        rec["details"]["change_pct"] = 99.0
        lines[-1] = json.dumps(rec)
        trail.chain_path.write_text("\n".join(lines) + "\n")
        with pytest.raises(ValueError, match="Audit chain broken"):
            AuditTrail(tenant_id="t-audit")

    def test_verify_detects_broken_link(self):
        trail = AuditTrail(tenant_id="t-audit")
        trail.write_event("weight_updated", payload={"change_pct": 1.0})
        trail.write_event("weight_updated", payload={"change_pct": 2.0})
        lines = trail.chain_path.read_text().splitlines()
        rec = json.loads(lines[-1])
        rec["prev_hash"] = "fake_hash_123"
        lines[-1] = json.dumps(rec)
        trail.chain_path.write_text("\n".join(lines) + "\n")
        with pytest.raises(ValueError, match="Audit chain broken"):
            AuditTrail(tenant_id="t-audit")

    def test_none_tenant_id_raises_error(self):
        with pytest.raises(ValueError, match="tenant_id must not be None"):
            AuditTrail(tenant_id=None)  # type: ignore[arg-type]

    def test_invalid_tenant_id_refused(self):
        with pytest.raises(ValueError):
            AuditTrail(tenant_id="../escape")


class TestComplianceReporter:
    """GDPR export helpers and bias detection."""

    def test_pii_redaction_of_text(self):
        reporter = ComplianceReporter(AuditTrail(tenant_id="t-audit"))
        out = reporter.redact_pii("user@example.com, contact me at 555-1234")
        assert "[REDACTED_EMAIL]" in out and "[REDACTED_PHONE]" in out
        assert "user@example.com" not in out and "555-1234" not in out

    def test_export_contains_no_pii_because_none_can_be_written(self):
        trail = AuditTrail(tenant_id="t-audit")
        with pytest.raises(ValueError):
            trail.write_event("feedback_received", payload={"email": "user@example.com"})
        export = ComplianceReporter(trail).export_for_compliance(redact=False)
        assert "user@example.com" not in export

    def test_user_id_masking_consistent(self):
        reporter = ComplianceReporter(AuditTrail(tenant_id="t-audit"), user_id_salt="fixed_salt")
        masked1 = reporter.mask_user_id("user_123")
        masked2 = reporter.mask_user_id("user_123")
        assert masked1 == masked2
        assert masked1 != "user_123"
        assert len(masked1) == 16

    def test_retention_never_rewrites_the_chain(self):
        trail = AuditTrail(tenant_id="t-audit")
        trail.write_event("feedback_received", payload={"signal": "positive"})
        before = trail.chain_path.read_bytes()
        reporter = ComplianceReporter(trail, retention_days=0)
        with pytest.raises(NotImplementedError):
            reporter.enforce_retention()
        assert trail.chain_path.read_bytes() == before

    def test_bias_detection_skewed_feedback(self):
        trail = AuditTrail(tenant_id="t-audit")
        reporter = ComplianceReporter(trail)
        for _ in range(9):
            trail.write_event("feedback_received", skill_id="skill_biased",
                              payload={"signal": "positive"})
        trail.write_event("feedback_received", skill_id="skill_biased",
                          payload={"signal": "negative"})
        alerts = reporter.detect_bias()
        assert len(alerts["skewed_feedback"]) > 0
        assert "skill_biased" in alerts["skewed_feedback"][0]
        assert "90%" in alerts["skewed_feedback"][0]


class TestPrometheusExporter:
    def test_collect_metrics_counts_events(self):
        trail = AuditTrail(tenant_id="t-audit")
        exporter = PrometheusExporter(trail)
        trail.write_event("skill_generated", skill_id="s1", payload={"loss_after": 0.5})
        trail.write_event("weight_updated", payload={"change_pct": 0.1})
        trail.write_event("feedback_received", payload={"signal": "positive"})
        metrics = exporter.collect_metrics()
        assert metrics.skill_generation_count == 1
        assert metrics.weight_updates_total == 1
        assert metrics.feedback_signals_total == 1
        assert metrics.audit_chain_height == 3
        assert metrics.audit_chain_verified == 1.0

    def test_export_text_format_valid_prometheus(self):
        trail = AuditTrail(tenant_id="t-audit")
        exporter = PrometheusExporter(trail)
        trail.write_event("skill_generated", skill_id="s1")
        text = exporter.export_text_format()
        assert "# HELP datahub_skill_generation_count" in text
        assert "# TYPE datahub_skill_generation_count counter" in text
        assert "datahub_skill_generation_count 1" in text


class TestLearningDashboardAPI:
    def _api(self, trail):
        return LearningDashboardAPI(trail, ComplianceReporter(trail), PrometheusExporter(trail))

    def test_get_skill_generation_history(self):
        trail = AuditTrail(tenant_id="t-audit")
        trail.write_event("skill_generated", skill_id="skill_xyz", payload={
            "skill_name": "Skill XYZ", "loss_before": 0.6, "loss_after": 0.4,
            "improvement_pct": 33.3, "phase_count": 10,
        })
        history = self._api(trail).get_skill_generation_history(limit=10)
        assert len(history) == 1
        assert history[0].skill_id == "skill_xyz"
        assert history[0].loss_before == 0.6
        assert history[0].loss_after == 0.4
        assert history[0].improvement_pct == 33.3

    def test_get_weight_updates(self):
        trail = AuditTrail(tenant_id="t-audit")
        trail.write_event("weight_updated", payload={
            "source_id": "memory:tier2", "weight_before": 0.5, "weight_after": 0.6,
            "change_pct": 20.0, "reason": "positive_feedback",
        })
        updates = self._api(trail).get_weight_updates(limit=10)
        assert len(updates) == 1
        assert updates[0].source_id == "memory:tier2"
        assert updates[0].change_pct == 20.0

    def test_get_convergence_status(self):
        trail = AuditTrail(tenant_id="t-audit")
        for _ in range(150):
            trail.write_event("feedback_received", payload={"signal": "positive"})
        status = self._api(trail).get_convergence_status()
        assert status.samples_processed == 150
        assert status.is_converged is True
        assert 0 <= status.confidence <= 1
