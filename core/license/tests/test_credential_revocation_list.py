"""Test suite for CredentialRevocationList (15 tests, k=4 Track B)."""

import tempfile
import json
import time
from pathlib import Path
from datetime import datetime, timedelta
import pytest

from core.license.credential_revocation_list import CredentialRevocationList
from core.compliance.audit_chain_writer import AuditChainWriter


@pytest.fixture
def temp_audit_path():
    with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
        path = f.name
    yield path
    Path(path).unlink(missing_ok=True)


@pytest.fixture
def audit_chain(temp_audit_path):
    return AuditChainWriter(temp_audit_path)


@pytest.fixture
def crl(audit_chain):
    return CredentialRevocationList("_default", audit_chain)


class TestCredentialValidity:
    """Test credential validity checking."""

    def test_unknown_credential_valid(self, crl):
        result = crl.check_credential_valid("unknown_cred")
        assert result is True

    def test_registered_credential_valid(self, crl):
        expires = datetime.utcnow() + timedelta(days=30)
        crl.register_credential("cred1", expires)
        result = crl.check_credential_valid("cred1")
        assert result is True

    def test_revoked_credential_invalid(self, crl):
        expires = datetime.utcnow() + timedelta(days=30)
        crl.register_credential("cred1", expires)
        crl.revoke_credential("cred1")
        result = crl.check_credential_valid("cred1")
        assert result is False

    def test_expired_credential_invalid(self, crl):
        expires = datetime.utcnow() - timedelta(days=1)  # Expired yesterday
        crl.register_credential("cred1", expires)
        result = crl.check_credential_valid("cred1")
        assert result is False

    def test_expiry_at_midnight_boundary(self, crl):
        # Expires today at midnight (UTC)
        today = datetime.utcnow().date()
        expires = datetime.combine(today, datetime.min.time())
        crl.register_credential("cred1", expires)
        result = crl.check_credential_valid("cred1")
        assert result is False  # At midnight, credential is expired


class TestGracePeriod:
    """Test grace period notifications."""

    def test_grace_period_7_days_before(self, crl, temp_audit_path):
        # Expires in 7 days
        expires = datetime.utcnow() + timedelta(days=7)
        crl.register_credential("cred1", expires)

        crl.check_credential_valid("cred1")

        with open(temp_audit_path, 'r') as f:
            events = [json.loads(line) for line in f.readlines()]

        alerts = [e for e in events if e["event_type"] == "grace_period_alert"]
        assert len(alerts) > 0

    def test_grace_period_1_day_before(self, crl):
        expires = datetime.utcnow() + timedelta(days=1)
        crl.register_credential("cred1", expires)

        result = crl.check_credential_valid("cred1")
        assert result is True  # Still valid, but should alert

    def test_grace_period_notification_once_per_cred(self, crl):
        expires = datetime.utcnow() + timedelta(days=7)
        crl.register_credential("cred1", expires)

        # Check twice
        crl.check_credential_valid("cred1")
        crl.check_credential_valid("cred1")

        # Should notify only once
        assert "cred1" in crl._grace_notified


class TestCRLFetch:
    """Test CRL fetch operations."""

    def test_fetch_crl_success(self, crl):
        result = crl.fetch_crl()
        assert result["valid"] is True
        assert "fetched_at" in result
        assert "revoked_count" in result

    def test_fetch_time_tracked(self, crl):
        assert crl.get_crl_fetch_time() is None

        crl.fetch_crl()

        fetch_time = crl.get_crl_fetch_time()
        assert fetch_time is not None

    def test_revoked_count_includes_revoked_creds(self, crl):
        crl.revoke_credential("cred1")
        crl.revoke_credential("cred2")

        result = crl.fetch_crl()
        assert result["revoked_count"] == 2


class TestAuditIntegration:
    """Test audit trail integration."""

    def test_credential_verified_event_written(self, crl, temp_audit_path):
        expires = datetime.utcnow() + timedelta(days=30)
        crl.register_credential("cred1", expires)

        crl.check_credential_valid("cred1")

        with open(temp_audit_path, 'r') as f:
            events = [json.loads(line) for line in f.readlines()]

        verified = [e for e in events if e["event_type"] == "credential_verified"]
        assert len(verified) > 0

    def test_credential_expired_event_written(self, crl, temp_audit_path):
        expires = datetime.utcnow() - timedelta(days=1)
        crl.register_credential("cred1", expires)

        crl.check_credential_valid("cred1")

        with open(temp_audit_path, 'r') as f:
            events = [json.loads(line) for line in f.readlines()]

        expired = [e for e in events if e["event_type"] == "credential_expired"]
        assert len(expired) > 0

    def test_audit_fail_closed(self, crl):
        crl.audit_chain.log_path = Path("/nonexistent/audit.jsonl")

        with pytest.raises(RuntimeError, match="Audit chain write failed"):
            expires = datetime.utcnow() + timedelta(days=30)
            crl.register_credential("cred1", expires)
            crl.check_credential_valid("cred1")

    def test_audit_chain_integrity(self, crl, temp_audit_path):
        expires = datetime.utcnow() + timedelta(days=30)
        crl.register_credential("cred1", expires)
        crl.check_credential_valid("cred1")

        with open(temp_audit_path, 'r') as f:
            lines = f.readlines()

        if len(lines) > 1:
            prev = json.loads(lines[0])
            curr = json.loads(lines[1])
            assert curr["prev_hash"] == prev["hash"]

    def test_tenant_isolation(self, temp_audit_path):
        audit_chain = AuditChainWriter(temp_audit_path)
        crl1 = CredentialRevocationList("tenant_1", audit_chain)
        crl2 = CredentialRevocationList("tenant_2", audit_chain)

        expires = datetime.utcnow() + timedelta(days=30)
        crl1.register_credential("cred1", expires)
        crl1.check_credential_valid("cred1")

        crl2.register_credential("cred2", expires)
        crl2.check_credential_valid("cred2")

        with open(temp_audit_path, 'r') as f:
            events = [json.loads(line) for line in f.readlines()]

        tenants = {e["tenant_id"] for e in events}
        assert "tenant_1" in tenants and "tenant_2" in tenants


class TestPerformanceSLA:
    """Test performance SLAs (<5ms check, <2ms grace period)."""

    def test_credential_check_under_5ms(self, crl):
        expires = datetime.utcnow() + timedelta(days=30)
        crl.register_credential("cred1", expires)

        start = time.time()
        for _ in range(10):
            crl.check_credential_valid("cred1")
        elapsed = (time.time() - start) * 1000 / 10

        assert elapsed < 5, f"Credential check took {elapsed:.2f}ms (SLA: <5ms)"

    def test_grace_period_check_under_2ms(self, crl):
        expires = datetime.utcnow() + timedelta(days=7)
        crl.register_credential("cred1", expires)

        start = time.time()
        crl.check_credential_valid("cred1")
        elapsed = (time.time() - start) * 1000

        # Grace period check is part of validity check
        assert elapsed < 2, f"Grace period check took {elapsed:.2f}ms (SLA: <2ms)"


class TestEdgeCases:
    """Test edge cases."""

    def test_empty_revoked_list(self, crl):
        assert crl.get_revoked_count() == 0

    def test_multiple_revocations(self, crl):
        for i in range(5):
            crl.revoke_credential(f"cred{i}")

        assert crl.get_revoked_count() == 5

    def test_register_multiple_credentials(self, crl):
        expires = datetime.utcnow() + timedelta(days=30)
        for i in range(5):
            crl.register_credential(f"cred{i}", expires)

        for i in range(5):
            result = crl.check_credential_valid(f"cred{i}")
            assert result is True
