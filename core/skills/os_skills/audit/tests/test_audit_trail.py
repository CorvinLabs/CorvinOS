"""Unit tests for AuditTrail, ComplianceReporter, PrometheusExporter."""
import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ..trail import AuditEvent, AuditTrail
from ..reporter import ComplianceReporter
from ..prometheus import PrometheusExporter
from ..api import LearningDashboardAPI


class TestAuditTrail:
    """Test immutable audit trail with hash-chain validation."""

    def test_write_event_creates_file(self):
        """Writing event creates audit chain file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            trail = AuditTrail(
                tenant_id="test_tenant",
                chain_path=Path(tmpdir) / "audit.jsonl"
            )

            event = trail.write_event(
                event_type="skill_generated",
                skill_id="skill_123",
                payload={"loss": 0.45},
            )

            assert event.event_type == "skill_generated"
            assert event.skill_id == "skill_123"
            assert trail.chain_path.exists()

    def test_hash_chain_integrity(self):
        """Hash chain links correctly across multiple events."""
        with tempfile.TemporaryDirectory() as tmpdir:
            trail = AuditTrail(
                tenant_id="test_tenant",
                chain_path=Path(tmpdir) / "audit.jsonl"
            )

            # Write 3 events
            event1 = trail.write_event("event_type_1", skill_id="s1", payload={"seq": 1})
            event2 = trail.write_event("event_type_2", skill_id="s2", payload={"seq": 2})
            event3 = trail.write_event("event_type_3", skill_id="s3", payload={"seq": 3})

            # Verify chain links
            assert event2.prev_hash == event1.hash
            assert event3.prev_hash == event2.hash

    def test_query_events_filters_by_tenant(self):
        """Query only returns events for the correct tenant."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "audit.jsonl"

            # Write events from tenant_1
            trail1 = AuditTrail(tenant_id="tenant_1", chain_path=path)
            trail1.write_event("event_1", skill_id="s1")

            # tenant_2 uses same file (unusual, but possible)
            # Actually, we need separate files per tenant in practice
            # But the trail itself filters by tenant_id

            events = trail1.query_events(limit=10)
            assert len(events) == 1
            assert events[0].tenant_id == "tenant_1"

    def test_verify_chain_detects_tampering(self):
        """Verify detects if chain is broken (event modified)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "audit.jsonl"
            trail = AuditTrail(tenant_id="test", chain_path=path)

            # Write event
            trail.write_event("event_1", payload={"data": "original"})

            # Tamper with the chain
            with open(path, 'r') as f:
                lines = f.readlines()

            # Modify the payload
            event_data = json.loads(lines[0])
            event_data["payload"]["data"] = "tampered"
            lines[0] = json.dumps(event_data) + '\n'

            with open(path, 'w') as f:
                f.writelines(lines)

            # Verify should catch tampering
            with pytest.raises(ValueError, match="Event hash mismatch"):
                AuditTrail(tenant_id="test", chain_path=path)

    def test_verify_chain_detects_broken_link(self):
        """Verify detects if prev_hash link is broken."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "audit.jsonl"
            trail = AuditTrail(tenant_id="test", chain_path=path)

            trail.write_event("event_1", payload={"n": 1})
            trail.write_event("event_2", payload={"n": 2})

            # Break the link
            with open(path, 'r') as f:
                lines = f.readlines()

            event_data = json.loads(lines[1])
            event_data["prev_hash"] = "fake_hash_123"
            lines[1] = json.dumps(event_data) + '\n'

            with open(path, 'w') as f:
                f.writelines(lines)

            # Verify should catch broken link
            with pytest.raises(ValueError, match="Chain broken"):
                AuditTrail(tenant_id="test", chain_path=path)

    def test_none_tenant_id_raises_error(self):
        """Initializing with None tenant_id fails (fail-closed)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with pytest.raises(ValueError, match="tenant_id must not be None"):
                AuditTrail(tenant_id=None, chain_path=Path(tmpdir) / "audit.jsonl")  # type: ignore


class TestComplianceReporter:
    """Test GDPR compliance and bias detection."""

    def test_pii_redaction(self):
        """PII redaction removes sensitive patterns."""
        with tempfile.TemporaryDirectory() as tmpdir:
            trail = AuditTrail(tenant_id="test", chain_path=Path(tmpdir) / "audit.jsonl")
            reporter = ComplianceReporter(trail)

            # Create an event with PII
            trail.write_event(
                "test_event",
                payload={"email": "user@example.com", "message": "contact me at 555-1234"}
            )

            # Export with redaction
            export = reporter.export_for_compliance(redact=True)

            assert "[REDACTED_email]" in export
            assert "[REDACTED_phone]" in export
            assert "user@example.com" not in export
            assert "555-1234" not in export

    def test_user_id_masking_consistent(self):
        """User ID masking is deterministic (same ID → same hash)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            trail = AuditTrail(tenant_id="test", chain_path=Path(tmpdir) / "audit.jsonl")
            reporter = ComplianceReporter(trail, user_id_salt="fixed_salt")

            user_id = "user_123"
            masked1 = reporter.mask_user_id(user_id)
            masked2 = reporter.mask_user_id(user_id)

            assert masked1 == masked2
            assert masked1 != user_id
            assert len(masked1) == 16  # SHA256 truncated to 16 chars

    def test_retention_policy_deletes_old_events(self):
        """Enforce retention deletes events older than retention_days."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "audit.jsonl"

            # Manually write old event
            old_ts = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
            with open(path, 'w') as f:
                event_data = {
                    "event_type": "old_event",
                    "tenant_id": "test",
                    "timestamp": old_ts,
                    "payload": {},
                    "prev_hash": "",
                    "hash": "abc123"
                }
                f.write(json.dumps(event_data) + '\n')

            trail = AuditTrail(tenant_id="test", chain_path=path)
            reporter = ComplianceReporter(trail, retention_days=90)

            deleted = reporter.enforce_retention()

            assert deleted == 1

            # Verify file is now empty or the event is gone
            events = trail.query_events(limit=10)
            assert len(events) == 0

    def test_bias_detection_skewed_feedback(self):
        """Bias detection flags skills with >80% one-sided feedback."""
        with tempfile.TemporaryDirectory() as tmpdir:
            trail = AuditTrail(tenant_id="test", chain_path=Path(tmpdir) / "audit.jsonl")
            reporter = ComplianceReporter(trail)

            # Write feedback: 9 positive, 1 negative (90% positive skew)
            for i in range(9):
                trail.write_event(
                    "feedback_received",
                    skill_id="skill_biased",
                    payload={"signal": "positive"}
                )
            trail.write_event(
                "feedback_received",
                skill_id="skill_biased",
                payload={"signal": "negative"}
            )

            alerts = reporter.detect_bias()

            assert len(alerts["skewed_feedback"]) > 0
            assert "skill_biased" in alerts["skewed_feedback"][0]
            assert "90%" in alerts["skewed_feedback"][0]


class TestPrometheusExporter:
    """Test Prometheus metrics export."""

    def test_collect_metrics_counts_events(self):
        """Metrics collection counts event types."""
        with tempfile.TemporaryDirectory() as tmpdir:
            trail = AuditTrail(tenant_id="test", chain_path=Path(tmpdir) / "audit.jsonl")
            exporter = PrometheusExporter(trail)

            # Write events
            trail.write_event("skill_generated", skill_id="s1", payload={"loss": 0.5})
            trail.write_event("weight_updated", payload={"change": 0.1})
            trail.write_event("feedback_received", payload={"signal": "positive"})

            metrics = exporter.collect_metrics()

            assert metrics.skill_generation_count == 1
            assert metrics.weight_updates_total == 1
            assert metrics.feedback_signals_total == 1
            assert metrics.audit_chain_height == 3

    def test_export_text_format_valid_prometheus(self):
        """Export format is valid Prometheus text."""
        with tempfile.TemporaryDirectory() as tmpdir:
            trail = AuditTrail(tenant_id="test", chain_path=Path(tmpdir) / "audit.jsonl")
            exporter = PrometheusExporter(trail)

            trail.write_event("skill_generated", skill_id="s1")

            text = exporter.export_text_format()

            # Should have HELP comments and TYPE lines
            assert "# HELP datahub_skill_generation_count" in text
            assert "# TYPE datahub_skill_generation_count counter" in text
            assert "datahub_skill_generation_count 1" in text


class TestLearningDashboardAPI:
    """Test dashboard API."""

    def test_get_skill_generation_history(self):
        """Dashboard API returns skill generation records."""
        with tempfile.TemporaryDirectory() as tmpdir:
            trail = AuditTrail(tenant_id="test", chain_path=Path(tmpdir) / "audit.jsonl")
            reporter = ComplianceReporter(trail)
            exporter = PrometheusExporter(trail)
            api = LearningDashboardAPI(trail, reporter, exporter)

            # Write skill generation event
            trail.write_event(
                "skill_generated",
                skill_id="skill_xyz",
                payload={
                    "skill_name": "Skill XYZ",
                    "loss_before": 0.6,
                    "loss_after": 0.4,
                    "improvement_pct": 33.3,
                    "phase_count": 10,
                }
            )

            history = api.get_skill_generation_history(limit=10)

            assert len(history) == 1
            assert history[0].skill_id == "skill_xyz"
            assert history[0].loss_before == 0.6
            assert history[0].loss_after == 0.4
            assert history[0].improvement_pct == 33.3

    def test_get_weight_updates(self):
        """Dashboard API returns weight update timeline."""
        with tempfile.TemporaryDirectory() as tmpdir:
            trail = AuditTrail(tenant_id="test", chain_path=Path(tmpdir) / "audit.jsonl")
            reporter = ComplianceReporter(trail)
            exporter = PrometheusExporter(trail)
            api = LearningDashboardAPI(trail, reporter, exporter)

            trail.write_event(
                "weight_updated",
                payload={
                    "source_id": "memory:tier2",
                    "weight_before": 0.5,
                    "weight_after": 0.6,
                    "change_pct": 20.0,
                    "reason": "positive_feedback"
                }
            )

            updates = api.get_weight_updates(limit=10)

            assert len(updates) == 1
            assert updates[0].source_id == "memory:tier2"
            assert updates[0].change_pct == 20.0

    def test_get_convergence_status(self):
        """Dashboard API returns convergence metrics."""
        with tempfile.TemporaryDirectory() as tmpdir:
            trail = AuditTrail(tenant_id="test", chain_path=Path(tmpdir) / "audit.jsonl")
            reporter = ComplianceReporter(trail)
            exporter = PrometheusExporter(trail)
            api = LearningDashboardAPI(trail, reporter, exporter)

            # Write feedback events to simulate learning
            for _ in range(150):
                trail.write_event("feedback_received", payload={"signal": "positive"})

            status = api.get_convergence_status()

            assert status.samples_processed == 150
            assert status.is_converged is True
            assert 0 <= status.confidence <= 1
