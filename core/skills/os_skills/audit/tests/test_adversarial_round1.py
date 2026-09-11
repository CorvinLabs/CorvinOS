"""ADVERSARIAL REVIEW ROUND 1: Security & Integrity Attacks on DataHub Creator Audit System.

Tests all 6 attack vectors:
1. Audit Trail Tampering (hash-chain modification)
2. PII Leakage (redaction bypass)
3. User ID Masking Bypass (collision/reversal)
4. Tenant Isolation Breach (cross-tenant query)
5. Hash-Chain Boot Verification (corruption detection)
6. Prometheus Metric Injection (out-of-range values)
"""
import hashlib
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ..trail import AuditEvent, AuditTrail
from ..reporter import ComplianceReporter
from ..prometheus import PrometheusExporter
from ..api import LearningDashboardAPI


# ============================================================================
# ATTACK VECTOR 1: Audit Trail Tampering
# ============================================================================


class TestAttackVector1_AuditTamperingDetection:
    """Test that tampering is detected at boot (fail-closed)."""

    def test_modification_of_event_hash(self):
        """PASS: Modifying an event's hash field is detected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "audit.jsonl"
            trail = AuditTrail(tenant_id="test", chain_path=path)
            trail.write_event("event_1", payload={"data": "original"})

            # Tamper: change the event's hash
            with open(path, 'r') as f:
                event_data = json.loads(f.readline())
            event_data["hash"] = "ffffffffffffffffffffffffffffffff"  # Invalid hash
            with open(path, 'w') as f:
                f.write(json.dumps(event_data) + '\n')

            # Boot should fail (fail-closed)
            with pytest.raises(ValueError, match="Event hash mismatch"):
                AuditTrail(tenant_id="test", chain_path=path)

    def test_modification_of_prev_hash_link(self):
        """PASS: Breaking prev_hash link is detected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "audit.jsonl"
            trail = AuditTrail(tenant_id="test", chain_path=path)
            trail.write_event("event_1", payload={"n": 1})
            trail.write_event("event_2", payload={"n": 2})

            # Tamper: break the link
            with open(path, 'r') as f:
                lines = f.readlines()
            event_data = json.loads(lines[1])
            event_data["prev_hash"] = "0000000000000000000000000000000000000000000000000000000000000000"
            lines[1] = json.dumps(event_data) + '\n'
            with open(path, 'w') as f:
                f.writelines(lines)

            # Boot should fail (fail-closed)
            with pytest.raises(ValueError, match="Chain broken"):
                AuditTrail(tenant_id="test", chain_path=path)

    def test_insertion_of_fake_event(self):
        """PASS: Inserting a fake event with correct hash fails on next event."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "audit.jsonl"
            trail = AuditTrail(tenant_id="test", chain_path=path)
            event1 = trail.write_event("event_1", payload={"n": 1})
            event2 = trail.write_event("event_2", payload={"n": 2})

            # Inject a fake event between them
            fake_event = AuditEvent(
                event_type="fake_event",
                tenant_id="test",
                timestamp=datetime.now(timezone.utc).isoformat(),
                payload={"data": "injected"},
                prev_hash=event1.hash,
            )

            with open(path, 'r') as f:
                lines = f.readlines()

            # Insert fake between lines 0 and 1
            fake_line = json.dumps({
                "event_type": fake_event.event_type,
                "tenant_id": fake_event.tenant_id,
                "timestamp": fake_event.timestamp,
                "skill_id": None,
                "skill_version": None,
                "payload": fake_event.payload,
                "prev_hash": fake_event.prev_hash,
                "hash": fake_event.hash,
            }) + '\n'

            lines.insert(1, fake_line)
            with open(path, 'w') as f:
                f.writelines(lines)

            # Boot should fail (event2's prev_hash no longer matches event1's hash)
            with pytest.raises(ValueError, match="Chain broken"):
                AuditTrail(tenant_id="test", chain_path=path)

    def test_deletion_of_middle_event(self):
        """PASS: Deleting an event from middle breaks the chain."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "audit.jsonl"
            trail = AuditTrail(tenant_id="test", chain_path=path)
            trail.write_event("event_1", payload={"n": 1})
            trail.write_event("event_2", payload={"n": 2})
            trail.write_event("event_3", payload={"n": 3})

            # Delete middle event
            with open(path, 'r') as f:
                lines = f.readlines()
            lines.pop(1)  # Remove event_2
            with open(path, 'w') as f:
                f.writelines(lines)

            # Boot should fail (event_3's prev_hash won't match event_1's hash)
            with pytest.raises(ValueError, match="Chain broken"):
                AuditTrail(tenant_id="test", chain_path=path)


# ============================================================================
# ATTACK VECTOR 2: PII Leakage in Audit Export
# ============================================================================


class TestAttackVector2_PIILeakageTesting:
    """Test PII redaction and potential bypass patterns."""

    def test_pii_redaction_email(self):
        """PASS: Email addresses are redacted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            trail = AuditTrail(tenant_id="test", chain_path=Path(tmpdir) / "audit.jsonl")
            trail.write_event("test_event", payload={"email": "user@example.com"})

            reporter = ComplianceReporter(trail)
            export = reporter.export_for_compliance(redact=True)

            assert "user@example.com" not in export
            assert "[REDACTED_email]" in export

    def test_pii_redaction_phone(self):
        """PASS: Phone numbers are redacted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            trail = AuditTrail(tenant_id="test", chain_path=Path(tmpdir) / "audit.jsonl")
            trail.write_event("test_event", payload={"phone": "555-1234"})

            reporter = ComplianceReporter(trail)
            export = reporter.export_for_compliance(redact=True)

            assert "555-1234" not in export
            assert "[REDACTED_phone]" in export

    def test_pii_leak_german_iban_not_redacted(self):
        """FAIL: German IBAN patterns are NOT in PII_PATTERNS, so not redacted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            trail = AuditTrail(tenant_id="test", chain_path=Path(tmpdir) / "audit.jsonl")
            iban = "DE89370400440532013000"
            trail.write_event("test_event", payload={"iban": iban})

            reporter = ComplianceReporter(trail)
            export = reporter.export_for_compliance(redact=True)

            # VULNERABILITY: German IBAN is NOT redacted
            assert iban in export  # Should be redacted but isn't!
            # Missing pattern in PII_PATTERNS

    def test_pii_leak_unicode_email_bypass(self):
        """EDGE CASE: Unicode email variants might bypass regex."""
        with tempfile.TemporaryDirectory() as tmpdir:
            trail = AuditTrail(tenant_id="test", chain_path=Path(tmpdir) / "audit.jsonl")
            # Standard email format (should work)
            trail.write_event("test_event", payload={"email": "test.user+tag@example.com"})

            reporter = ComplianceReporter(trail)
            export = reporter.export_for_compliance(redact=True)

            # This should be redacted (and it is with the current regex)
            assert "test.user+tag@example.com" not in export

    def test_pii_redaction_credit_card(self):
        """PASS: Credit card patterns are redacted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            trail = AuditTrail(tenant_id="test", chain_path=Path(tmpdir) / "audit.jsonl")
            trail.write_event("test_event", payload={"cc": "1234-5678-9012-3456"})

            reporter = ComplianceReporter(trail)
            export = reporter.export_for_compliance(redact=True)

            assert "1234-5678-9012-3456" not in export
            assert "[REDACTED_credit_card]" in export

    def test_pii_user_id_masking_in_redaction(self):
        """PASS: User IDs are masked (hashed), not redacted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            trail = AuditTrail(tenant_id="test", chain_path=Path(tmpdir) / "audit.jsonl")
            trail.write_event("test_event", payload={"user_id": "user_123"})

            reporter = ComplianceReporter(trail, user_id_salt="test_salt")
            export = reporter.export_for_compliance(redact=True)

            # User ID should be masked, not appear as-is or [REDACTED_...]
            assert "user_id" in export  # Field name is there
            assert "user_123" not in export  # Original value is gone
            # It should contain the masked hash
            expected_hash = hashlib.sha256("user_123:test_salt".encode()).hexdigest()[:16]
            assert expected_hash in export


# ============================================================================
# ATTACK VECTOR 3: User ID Masking Bypass
# ============================================================================


class TestAttackVector3_UserIDMaskingBypass:
    """Test user ID masking determinism and collision resistance."""

    def test_user_id_masking_deterministic(self):
        """PASS: Same user ID → same masked value (deterministic)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            trail = AuditTrail(tenant_id="test", chain_path=Path(tmpdir) / "audit.jsonl")
            reporter = ComplianceReporter(trail, user_id_salt="fixed_salt")

            user_id = "user_123"
            masked1 = reporter.mask_user_id(user_id)
            masked2 = reporter.mask_user_id(user_id)

            assert masked1 == masked2
            assert len(masked1) == 16

    def test_user_id_masking_collision_resistance_small_sample(self):
        """PASS: No collisions in small sample (100 users)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            trail = AuditTrail(tenant_id="test", chain_path=Path(tmpdir) / "audit.jsonl")
            reporter = ComplianceReporter(trail, user_id_salt="test_salt")

            masked_ids = set()
            for i in range(100):
                user_id = f"user_{i}"
                masked = reporter.mask_user_id(user_id)
                assert masked not in masked_ids, f"Collision detected for user_{i}"
                masked_ids.add(masked)

            assert len(masked_ids) == 100

    def test_user_id_masking_different_salt_different_hash(self):
        """PASS: Different salts produce different masks."""
        with tempfile.TemporaryDirectory() as tmpdir:
            trail = AuditTrail(tenant_id="test", chain_path=Path(tmpdir) / "audit.jsonl")
            reporter1 = ComplianceReporter(trail, user_id_salt="salt1")
            reporter2 = ComplianceReporter(trail, user_id_salt="salt2")

            user_id = "user_123"
            masked1 = reporter1.mask_user_id(user_id)
            masked2 = reporter2.mask_user_id(user_id)

            assert masked1 != masked2

    def test_user_id_masking_not_reversible(self):
        """PASS: Cannot reverse the hash to get original user_id."""
        with tempfile.TemporaryDirectory() as tmpdir:
            trail = AuditTrail(tenant_id="test", chain_path=Path(tmpdir) / "audit.jsonl")
            reporter = ComplianceReporter(trail, user_id_salt="test_salt")

            user_id = "user_123"
            masked = reporter.mask_user_id(user_id)

            # Try to reverse (should fail)
            # With SHA256, reversal is computationally infeasible
            # This is a conceptual test - we just verify the hash is one-way
            assert masked != user_id
            # Attempting reverse lookup would require brute force
            # This is expected and secure behavior


# ============================================================================
# ATTACK VECTOR 4: Tenant Isolation Breach
# ============================================================================


class TestAttackVector4_TenantIsolationBreach:
    """Test that queries enforce tenant_id filtering."""

    def test_query_filters_by_tenant(self):
        """PASS: Query only returns events for matching tenant."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "audit.jsonl"

            # Write events from tenant_1
            trail1 = AuditTrail(tenant_id="tenant_1", chain_path=path)
            trail1.write_event("event_1", skill_id="s1", payload={"data": "t1"})
            trail1.write_event("event_2", skill_id="s2", payload={"data": "t1"})

            # Query with tenant_1 (should get 2 events)
            events1 = trail1.query_events(limit=10)
            assert len(events1) == 2
            assert all(e.tenant_id == "tenant_1" for e in events1)

    def test_different_tenant_instance_cannot_query_other_tenant_data(self):
        """PASS: Creating a different AuditTrail instance with different tenant_id is isolated."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "audit.jsonl"

            # Write events from tenant_1
            trail1 = AuditTrail(tenant_id="tenant_1", chain_path=path)
            trail1.write_event("event_1", skill_id="s1", payload={"data": "t1"})

            # Try to access with tenant_2 (same file, different tenant_id)
            trail2 = AuditTrail(tenant_id="tenant_2", chain_path=path)
            events2 = trail2.query_events(limit=10)

            # Should be empty (tenant_2 has no events in the file)
            assert len(events2) == 0

    def test_api_respects_tenant_isolation(self):
        """PASS: API endpoints respect tenant isolation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "audit.jsonl"

            # Tenant 1 writes events
            trail1 = AuditTrail(tenant_id="tenant_1", chain_path=path)
            trail1.write_event("skill_generated", skill_id="s1", payload={
                "loss_before": 0.6,
                "loss_after": 0.4,
                "improvement_pct": 33.3,
                "phase_count": 10,
            })

            # API for tenant_2 (different instance)
            trail2 = AuditTrail(tenant_id="tenant_2", chain_path=path)
            reporter2 = ComplianceReporter(trail2)
            exporter2 = PrometheusExporter(trail2)
            api2 = LearningDashboardAPI(trail2, reporter2, exporter2)

            # API should return empty results for tenant_2
            history = api2.get_skill_generation_history(limit=10)
            assert len(history) == 0


# ============================================================================
# ATTACK VECTOR 5: Hash-Chain Boot Verification
# ============================================================================


class TestAttackVector5_BootVerificationFailClosed:
    """Test that boot verification is fail-closed (rejects broken chains)."""

    def test_boot_with_broken_chain_fails(self):
        """PASS: Boot with broken chain raises ValueError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "audit.jsonl"

            # Write valid chain
            trail = AuditTrail(tenant_id="test", chain_path=path)
            trail.write_event("event_1", payload={"n": 1})
            trail.write_event("event_2", payload={"n": 2})

            # Corrupt the chain
            with open(path, 'r') as f:
                lines = f.readlines()
            event_data = json.loads(lines[1])
            event_data["prev_hash"] = "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
            lines[1] = json.dumps(event_data) + '\n'
            with open(path, 'w') as f:
                f.writelines(lines)

            # Boot should fail
            with pytest.raises(ValueError, match="Chain broken"):
                AuditTrail(tenant_id="test", chain_path=path)

    def test_boot_with_empty_chain_succeeds(self):
        """PASS: Boot with empty (new) chain succeeds."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "audit.jsonl"
            # File doesn't exist yet
            trail = AuditTrail(tenant_id="test", chain_path=path)
            assert trail is not None

    def test_boot_with_malformed_json_fails(self):
        """PASS: Boot with malformed JSON fails."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "audit.jsonl"

            # Write malformed JSON
            with open(path, 'w') as f:
                f.write("{not valid json\n")

            # Boot should fail
            with pytest.raises(ValueError, match="Malformed event"):
                AuditTrail(tenant_id="test", chain_path=path)


# ============================================================================
# ATTACK VECTOR 6: Prometheus Metric Injection
# ============================================================================


class TestAttackVector6_PrometheusMetricInjection:
    """Test Prometheus metrics validation and injection resistance."""

    def test_metric_with_negative_convergence_status(self):
        """FAIL: Negative convergence status is accepted (should be clamped to [0,1])."""
        with tempfile.TemporaryDirectory() as tmpdir:
            trail = AuditTrail(tenant_id="test", chain_path=Path(tmpdir) / "audit.jsonl")
            exporter = PrometheusExporter(trail)

            # Inject negative value
            text = exporter.export_text_format(daemon_convergence_status=-1.5)

            # VULNERABILITY: Negative value is exported without validation
            assert "datahub_daemon_convergence_status -1.5" in text

    def test_metric_with_out_of_range_convergence(self):
        """FAIL: Out-of-range convergence (>1.0) is accepted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            trail = AuditTrail(tenant_id="test", chain_path=Path(tmpdir) / "audit.jsonl")
            exporter = PrometheusExporter(trail)

            # Inject value > 1.0
            text = exporter.export_text_format(daemon_convergence_status=999999.0)

            # VULNERABILITY: Out-of-range value is exported without validation
            assert "datahub_daemon_convergence_status 999999" in text

    def test_metric_with_nan_value(self):
        """EDGE CASE: NaN value may cause Prometheus parsing issues."""
        with tempfile.TemporaryDirectory() as tmpdir:
            trail = AuditTrail(tenant_id="test", chain_path=Path(tmpdir) / "audit.jsonl")
            exporter = PrometheusExporter(trail)

            # Try with NaN (Python's float('nan'))
            import math
            text = exporter.export_text_format(daemon_convergence_status=math.nan)

            # NaN might be exported as "nan" which is not valid Prometheus
            assert "nan" in text.lower() or "NaN" in text

    def test_metric_with_infinity(self):
        """EDGE CASE: Infinity value may cause Prometheus parsing issues."""
        with tempfile.TemporaryDirectory() as tmpdir:
            trail = AuditTrail(tenant_id="test", chain_path=Path(tmpdir) / "audit.jsonl")
            exporter = PrometheusExporter(trail)

            # Try with infinity
            import math
            text = exporter.export_text_format(daemon_convergence_status=math.inf)

            # Infinity might be exported as "inf" which is not standard Prometheus
            assert "inf" in text.lower()

    def test_metric_with_special_prefix_injection(self):
        """EDGE CASE: Prefix parameter is not validated."""
        with tempfile.TemporaryDirectory() as tmpdir:
            trail = AuditTrail(tenant_id="test", chain_path=Path(tmpdir) / "audit.jsonl")
            exporter = PrometheusExporter(trail)

            # Try to inject through prefix parameter
            # (This would require API to accept the prefix, but worth testing)
            # The export_text_format function accepts a prefix parameter
            text = exporter.export_text_format(prefix="malicious_")

            # Metrics would have the injected prefix
            assert "malicious_skill_generation_count" in text

    def test_valid_convergence_range(self):
        """PASS: Valid convergence values (0-1) are exported correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            trail = AuditTrail(tenant_id="test", chain_path=Path(tmpdir) / "audit.jsonl")
            exporter = PrometheusExporter(trail)

            # Valid value
            text = exporter.export_text_format(daemon_convergence_status=0.75)

            assert "datahub_daemon_convergence_status 0.75" in text


# ============================================================================
# SUMMARY
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
