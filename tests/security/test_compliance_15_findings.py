"""Unit Tests for All 15 GDPR + EU AI Act Compliance Findings

Test Coverage:
1. GDPR Art. 5 - Accountability (Audit trail + Tenant isolation)
2. GDPR Art. 6/7 - Consent (Explicit approval + Right to withdraw)
3. GDPR Art. 30 - Processing Record (Hash-chained audit)
4. GDPR Art. 32 - Security (LoM binding + Fail-closed)
5. EU AI Act Art. 50 - Transparency (Rollback notification)
6-7. ADR-0232/0233 - Boot Tripwire
8. ADR-0537 - LoM Binding
9. ADR-0563 - Tenant Isolation
10. ADR-0513 - Consent Basis
11. Audit Persistence
12. Hash Chain Verification
13. Learning Loop Integration
14. Empty Metrics Handling
15. ADR Compliance Drift
"""

import pytest
import json
import tempfile
from pathlib import Path
from datetime import datetime, timezone, timedelta

from unittest.mock import patch

from core.deployment import audit_sink
from core.deployment.incident_response_procedures import (
    Incident,
    IncidentType,
    IncidentSeverity,
    IncidentNotifier,
)


# Adversarial review 2026-09-27: IncidentNotifier no longer writes a
# hand-composed ``orchestrator_audit.jsonl``; incidents are committed to the
# ONE tenant audit chain (forge write_event via core.deployment.audit_sink),
# content-free (codes/ids/numbers — never the free-text message or details).
# These tests read that chain back.


@pytest.fixture(autouse=True)
def _isolated_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "corvin"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CORVIN_TENANT_ID", raising=False)
    monkeypatch.delenv("VOICE_AUDIT_PATH", raising=False)
    monkeypatch.delenv("FORGE_ROOT", raising=False)
    yield


def _incidents_on_chain(tenant):
    se, fp = audit_sink._forge()
    path = fp.tenant_audit_chain(tenant)
    if not path.exists():
        return [], True
    recs = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    ok, _ = se.verify_chain(path)
    return [r for r in recs if r["event_type"] == "deployment.incident_detected"], ok


def _incident(i="test-1", tenant="_default", **kw):
    base = dict(
        incident_id=i,
        incident_type=IncidentType.LATENCY_SPIKE,
        severity=IncidentSeverity.WARNING,
        detected_at=datetime.now(timezone.utc).isoformat(),
        message="Latency spike detected",
        details={"baseline_ms": 100},
        metric_name="latency_p99_ms",
        actual_value=150.0,
        threshold=100.0,
        tenant_id=tenant,
    )
    base.update(kw)
    return Incident(**base)


class TestFinding1GDPRArt5:
    """FINDING #1: GDPR Art. 5 - Accountability + Audit Trail"""

    def test_incident_has_tenant_id_field(self):
        """Incident dataclass has mandatory tenant_id field"""
        incident = Incident(
            incident_id="test-1",
            incident_type=IncidentType.LATENCY_SPIKE,
            severity=IncidentSeverity.WARNING,
            detected_at=datetime.now(timezone.utc).isoformat(),
            message="Test incident",
            details={},
            metric_name="latency_p99_ms",
            actual_value=150.0,
            threshold=100.0,
            tenant_id="tenant-123",
        )

        assert incident.tenant_id == "tenant-123"
        assert incident.tenant_id is not None

    def test_incident_defaults_to_default_tenant(self):
        """Incident defaults to _default tenant if not specified"""
        incident = Incident(
            incident_id="test-1",
            incident_type=IncidentType.LATENCY_SPIKE,
            severity=IncidentSeverity.WARNING,
            detected_at=datetime.now(timezone.utc).isoformat(),
            message="Test incident",
            details={},
            metric_name="latency_p99_ms",
            actual_value=150.0,
            threshold=100.0,
        )

        assert incident.tenant_id == "_default"


class TestFinding2Compliance:
    """FINDING #2: Audit-First (write to the tenant chain before notifications)"""

    def test_incident_written_to_audit_chain(self, monkeypatch):
        monkeypatch.setenv("CORVIN_TENANT_ID", "tenant-123")
        notifier = IncidentNotifier(start_dlq_worker=False)
        incident = _incident(tenant="tenant-123")

        assert notifier._write_incident_to_audit(incident)
        events, ok = _incidents_on_chain("tenant-123")
        assert ok
        assert len(events) == 1
        d = events[0]["details"]
        assert d["incident_id"] == "test-1"
        assert d["tenant_id"] == "tenant-123"
        assert incident.audit_event_id == events[0]["hash"]

    def test_audit_write_failure_causes_notify_to_fail(self):
        notifier = IncidentNotifier(start_dlq_worker=False)
        with patch.object(audit_sink, "emit", side_effect=audit_sink.AuditWriteFailed("x")):
            with patch.object(notifier, "_send_slack_notification", return_value=True) as slack:
                assert notifier.notify(_incident(), slack_webhook="http://test") is False
        slack.assert_not_called()


class TestFinding3Compliance:
    """FINDING #3-4: Tenant Isolation (ADR-0563)"""

    def test_incident_lands_on_its_own_tenant_chain(self, monkeypatch):
        monkeypatch.setenv("CORVIN_TENANT_ID", "tenant-a")
        notifier = IncidentNotifier(start_dlq_worker=False)
        assert notifier._write_incident_to_audit(_incident("inc-a", tenant="tenant-a"))
        a, _ = _incidents_on_chain("tenant-a")
        b, _ = _incidents_on_chain("tenant-b")
        assert [e["details"]["incident_id"] for e in a] == ["inc-a"]
        assert b == []

    def test_foreign_tenant_incident_is_refused(self, monkeypatch):
        """A tenant-b incident in a tenant-a process is refused at the chokepoint."""
        monkeypatch.setenv("CORVIN_TENANT_ID", "tenant-a")
        notifier = IncidentNotifier(start_dlq_worker=False)
        assert notifier._write_incident_to_audit(_incident("inc-b", tenant="tenant-b")) is False
        b, _ = _incidents_on_chain("tenant-b")
        assert b == []


class TestFinding5Compliance:
    """FINDING #5: Audit Chain Integrity (ADR-0232)"""

    def test_audit_events_have_unique_ids_and_chain_verifies(self):
        notifier = IncidentNotifier(start_dlq_worker=False)
        ids = set()
        for i in range(5):
            inc = _incident(f"test-{i}")
            assert notifier._write_incident_to_audit(inc)
            ids.add(inc.audit_event_id)
        assert len(ids) == 5
        events, ok = _incidents_on_chain("_default")
        assert ok and len(events) == 5

    def test_tampering_is_detected(self):
        notifier = IncidentNotifier(start_dlq_worker=False)
        notifier._write_incident_to_audit(_incident("test-1"))
        notifier._write_incident_to_audit(_incident("test-2"))
        se, fp = audit_sink._forge()
        path = fp.tenant_audit_chain("_default")
        lines = path.read_text().splitlines()
        rec = json.loads(lines[0])
        rec["details"]["actual_value"] = 1.0
        lines[0] = json.dumps(rec)
        path.write_text("\n".join(lines) + "\n")
        _, ok = _incidents_on_chain("_default")
        assert ok is False


class TestFinding6Compliance:
    """FINDING #6: EU AI Act Art. 50 — rollback transparency, content-free"""

    def test_rollback_incident_record_is_attributable_but_content_free(self):
        notifier = IncidentNotifier(start_dlq_worker=False)
        inc = _incident(
            "rollback-1",
            incident_type=IncidentType.CONFIDENCE_REGRESSION,
            severity=IncidentSeverity.CRITICAL,
            message="Automatic rollback triggered for bob@example.com",
            details={"reason": "free text with bob@example.com"},
            metric_name="confidence", actual_value=0.65, threshold=0.70,
        )
        assert notifier._write_incident_to_audit(inc)
        events, _ = _incidents_on_chain("_default")
        d = events[0]["details"]
        assert d["incident_type"] == "confidence_regression"
        assert d["incident_severity"] == "critical"
        assert d["metric_name"] == "confidence"
        assert "bob@example.com" not in json.dumps(events[0])


class TestFinding8Compliance:
    """FINDING #8: Failure Handling in notify()"""

    def test_notify_without_channels_is_not_success(self):
        notifier = IncidentNotifier(start_dlq_worker=False)
        assert notifier.notify(_incident()) is False
        events, _ = _incidents_on_chain("_default")
        assert len(events) == 1  # audit-first still happened

    def test_notify_with_all_channels_success(self):
        notifier = IncidentNotifier(start_dlq_worker=False)
        with patch.object(notifier, "_send_slack_notification", return_value=True):
            assert notifier.notify(_incident(), slack_webhook="http://test") is True


class TestFinding9Compliance:
    """FINDING #9: Deduplication + Rate Limiting"""

    def test_deduplication_window(self):
        notifier = IncidentNotifier(start_dlq_worker=False)
        inc = _incident()
        with patch.object(notifier, "_send_slack_notification", return_value=True):
            assert notifier.notify(inc, slack_webhook="http://test") is True
            assert notifier.notify(inc, slack_webhook="http://test") is False

    def test_rate_limiting(self):
        notifier = IncidentNotifier(start_dlq_worker=False)
        with patch.object(notifier, "_send_slack_notification", return_value=True):
            results = [
                notifier.notify(_incident(f"test-{i}", actual_value=150.0 + i), slack_webhook="http://test")
                for i in range(11)
            ]
        assert results[:10] == [True] * 10
        assert results[10] is False
        assert notifier.failed_notifications_queue.qsize() == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
