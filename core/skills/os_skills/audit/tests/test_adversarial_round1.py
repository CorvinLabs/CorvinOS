"""ADVERSARIAL REVIEW ROUND 1: Security & Integrity Attacks on DataHub Creator Audit System.

Tests all 6 attack vectors:
1. Audit Trail Tampering (hash-chain modification)
2. PII Leakage (never written; redaction of text)
3. User ID Masking Bypass (collision/reversal)
4. Tenant Isolation Breach (cross-tenant query)
5. Hash-Chain Boot Verification (corruption detection)
6. Prometheus Metric Injection (out-of-range values)

Since 2026-09-27 the trail is the tenant's CORE audit chain (see trail.py);
tampering is judged by ``forge.security_events.verify_chain``. The autouse
conftest fixture points CORVIN_HOME at a scratch directory.
"""
import hashlib
import json
import math

import pytest

from ..trail import AuditEvent, AuditTrail
from ..reporter import ComplianceReporter
from ..prometheus import PrometheusExporter
from ..api import LearningDashboardAPI

T = "t-adv"


def _rewrite(trail: AuditTrail, mutate) -> None:
    lines = trail.chain_path.read_text().splitlines()
    lines = mutate(lines)
    trail.chain_path.write_text("\n".join(lines) + "\n")


def _two(trail: AuditTrail) -> None:
    trail.write_event("weight_updated", payload={"change_pct": 1.0})
    trail.write_event("weight_updated", payload={"change_pct": 2.0})


# ============================================================================
# ATTACK VECTOR 1: Audit Trail Tampering
# ============================================================================


class TestAttackVector1_AuditTamperingDetection:
    """Tampering is detected when the trail is opened (fail-closed)."""

    def test_modification_of_event_hash(self):
        trail = AuditTrail(tenant_id=T)
        trail.write_event("feedback_received", payload={"signal": "positive"})

        def m(lines):
            rec = json.loads(lines[-1])
            rec["hash"] = "f" * len(rec["hash"])
            return lines[:-1] + [json.dumps(rec)]
        _rewrite(trail, m)
        with pytest.raises(ValueError, match="Audit chain broken"):
            AuditTrail(tenant_id=T)

    def test_modification_of_prev_hash_link(self):
        trail = AuditTrail(tenant_id=T)
        _two(trail)

        def m(lines):
            rec = json.loads(lines[-1])
            rec["prev_hash"] = "0" * 64
            return lines[:-1] + [json.dumps(rec)]
        _rewrite(trail, m)
        with pytest.raises(ValueError, match="Audit chain broken"):
            AuditTrail(tenant_id=T)

    def test_insertion_of_fake_event(self):
        trail = AuditTrail(tenant_id=T)
        _two(trail)
        fake = json.dumps({"ts": 0, "event_type": "datahub.feedback_received",
                           "details": {"signal": "injected"},
                           "prev_hash": "", "hash": "0" * 16})

        def m(lines):
            return lines[:-1] + [fake] + lines[-1:]
        _rewrite(trail, m)
        with pytest.raises(ValueError, match="Audit chain broken"):
            AuditTrail(tenant_id=T)

    def test_deletion_of_middle_event(self):
        trail = AuditTrail(tenant_id=T)
        _two(trail)
        trail.write_event("weight_updated", payload={"change_pct": 3.0})

        def m(lines):
            return lines[:-2] + lines[-1:]
        _rewrite(trail, m)
        with pytest.raises(ValueError, match="Audit chain broken"):
            AuditTrail(tenant_id=T)


# ============================================================================
# ATTACK VECTOR 2: PII Leakage
# ============================================================================


class TestAttackVector2_PIILeakageTesting:
    """PII can no longer be WRITTEN (the chain is append-only and cannot be
    redacted later); the export-side redaction of text still holds."""

    @pytest.mark.parametrize("payload", [
        {"email": "user@example.com"},
        {"phone": "555-1234"},
        {"iban": "DE89370400440532013000"},
        {"cc": "1234-5678-9012-3456"},
        {"user_id": "user_123"},
    ])
    def test_pii_keys_are_refused_before_writing(self, payload):
        trail = AuditTrail(tenant_id=T)
        with pytest.raises(ValueError, match="vocabulary"):
            trail.write_event("feedback_received", payload=payload)
        assert not trail.chain_path.exists()

    @pytest.mark.parametrize("value", [
        "user@example.com", "test.user+tag@example.com",
    ])
    def test_pii_value_in_free_text_key_never_lands_raw(self, value):
        trail = AuditTrail(tenant_id=T)
        with pytest.raises(ValueError):
            trail.write_event("weight_updated", payload={"reason": f"see {value}"})
        if trail.chain_path.exists():
            assert value not in trail.chain_path.read_text()

    @pytest.mark.parametrize("text,marker", [
        ("user@example.com", "[REDACTED_EMAIL]"),
        ("555-1234", "[REDACTED_PHONE]"),
        ("DE89370400440532013000", "[REDACTED_IBAN]"),
        ("1234-5678-9012-3456", "[REDACTED_CREDIT_CARD]"),
    ])
    def test_redact_pii_text(self, text, marker):
        out = ComplianceReporter(AuditTrail(tenant_id=T)).redact_pii(f"x {text} y")
        assert text not in out and marker in out

    def test_user_id_masked_in_redacted_event(self):
        reporter = ComplianceReporter(AuditTrail(tenant_id=T), user_id_salt="test_salt")
        ev = AuditEvent(event_type="x", tenant_id=T, timestamp="", payload={"user_id": "user_123"})
        red = reporter.redact_event(ev)
        expected = hashlib.sha256("user_123:test_salt".encode()).hexdigest()[:16]
        assert red.payload["user_id"] == expected


# ============================================================================
# ATTACK VECTOR 3: User ID Masking Bypass
# ============================================================================


class TestAttackVector3_UserIDMaskingBypass:
    def test_user_id_masking_deterministic(self):
        reporter = ComplianceReporter(AuditTrail(tenant_id=T), user_id_salt="fixed_salt")
        assert reporter.mask_user_id("user_123") == reporter.mask_user_id("user_123")
        assert len(reporter.mask_user_id("user_123")) == 16

    def test_user_id_masking_collision_resistance_small_sample(self):
        reporter = ComplianceReporter(AuditTrail(tenant_id=T), user_id_salt="test_salt")
        assert len({reporter.mask_user_id(f"user_{i}") for i in range(100)}) == 100

    def test_user_id_masking_different_salt_different_hash(self):
        trail = AuditTrail(tenant_id=T)
        assert (ComplianceReporter(trail, user_id_salt="salt1").mask_user_id("u")
                != ComplianceReporter(trail, user_id_salt="salt2").mask_user_id("u"))

    def test_user_id_masking_not_reversible(self):
        masked = ComplianceReporter(AuditTrail(tenant_id=T), user_id_salt="s").mask_user_id("user_123")
        assert masked != "user_123" and "user_123" not in masked


# ============================================================================
# ATTACK VECTOR 4: Tenant Isolation Breach
# ============================================================================


class TestAttackVector4_TenantIsolationBreach:
    def test_query_filters_by_tenant(self):
        trail1 = AuditTrail(tenant_id="tenant_1")
        trail1.write_event("feedback_received", skill_id="s1", payload={"signal": "positive"})
        trail1.write_event("feedback_received", skill_id="s2", payload={"signal": "positive"})
        events = trail1.query_events(limit=10)
        assert len(events) == 2
        assert all(e.tenant_id == "tenant_1" for e in events)

    def test_each_tenant_has_its_own_chain(self):
        trail1 = AuditTrail(tenant_id="tenant_1")
        trail1.write_event("feedback_received", skill_id="s1", payload={"signal": "positive"})
        trail2 = AuditTrail(tenant_id="tenant_2")
        assert trail2.chain_path != trail1.chain_path
        assert trail2.query_events(limit=10) == []

    def test_foreign_chain_path_refused(self):
        trail1 = AuditTrail(tenant_id="tenant_1")
        with pytest.raises(NotImplementedError):
            AuditTrail(tenant_id="tenant_2", chain_path=trail1.chain_path)

    def test_api_respects_tenant_isolation(self):
        trail1 = AuditTrail(tenant_id="tenant_1")
        trail1.write_event("skill_generated", skill_id="s1", payload={
            "loss_before": 0.6, "loss_after": 0.4, "improvement_pct": 33.3, "phase_count": 10,
        })
        trail2 = AuditTrail(tenant_id="tenant_2")
        api2 = LearningDashboardAPI(trail2, ComplianceReporter(trail2), PrometheusExporter(trail2))
        assert api2.get_skill_generation_history(limit=10) == []


# ============================================================================
# ATTACK VECTOR 5: Hash-Chain Boot Verification
# ============================================================================


class TestAttackVector5_BootVerificationFailClosed:
    def test_boot_with_broken_chain_fails(self):
        trail = AuditTrail(tenant_id=T)
        _two(trail)

        def m(lines):
            rec = json.loads(lines[-1])
            rec["prev_hash"] = "f" * 64
            return lines[:-1] + [json.dumps(rec)]
        _rewrite(trail, m)
        with pytest.raises(ValueError, match="Audit chain broken"):
            AuditTrail(tenant_id=T)

    def test_boot_with_empty_chain_succeeds(self):
        trail = AuditTrail(tenant_id=T)
        assert not trail.chain_path.exists()
        assert trail.verify_integrity() == (True, "Audit chain intact")

    def test_boot_with_malformed_json_fails(self):
        from core.paths.tenant import tenant_audit_chain
        path = tenant_audit_chain(T)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not valid json\n")
        with pytest.raises(ValueError, match="Audit chain broken"):
            AuditTrail(tenant_id=T)


# ============================================================================
# ATTACK VECTOR 6: Prometheus Metric Injection
# ============================================================================


class TestAttackVector6_PrometheusMetricInjection:
    """Out-of-range / non-finite values are refused."""

    @pytest.mark.parametrize("value", [-1.5, 999999.0, math.nan, math.inf])
    def test_invalid_convergence_refused(self, value):
        exporter = PrometheusExporter(AuditTrail(tenant_id=T))
        with pytest.raises(ValueError):
            exporter.export_text_format(daemon_convergence_status=value)

    def test_metric_with_special_prefix(self):
        text = PrometheusExporter(AuditTrail(tenant_id=T)).export_text_format(prefix="malicious_")
        assert "malicious_skill_generation_count" in text

    def test_valid_convergence_range(self):
        text = PrometheusExporter(AuditTrail(tenant_id=T)).export_text_format(
            daemon_convergence_status=0.75)
        assert "datahub_daemon_convergence_status 0.75" in text
