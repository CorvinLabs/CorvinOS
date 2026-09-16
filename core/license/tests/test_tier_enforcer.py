"""Test suite for TierEnforcer (20 tests, k=3 Track B)."""

import tempfile
import json
import time
from pathlib import Path
from datetime import datetime, timedelta
import pytest

from core.license.tier_enforcer import TierEnforcer, UserTier
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
def enforcer(audit_chain):
    return TierEnforcer("_default", audit_chain)


class TestFreeUserAccess:
    """Test FREE tier access control."""

    def test_free_can_access_haiku(self, enforcer):
        result = enforcer.check_model_access("free_user", "claude-haiku-4-5")
        assert result["allowed"] is True
        assert result["user_tier"] == "free"

    def test_free_can_access_sonnet(self, enforcer):
        result = enforcer.check_model_access("free_user", "claude-sonnet-3")
        assert result["allowed"] is True

    def test_free_cannot_access_opus(self, enforcer):
        result = enforcer.check_model_access("free_user", "claude-opus-4")
        assert result["allowed"] is False
        assert result["status_code"] == 403

    def test_free_rejection_writes_audit(self, enforcer, temp_audit_path):
        enforcer.check_model_access("free_user", "claude-opus-4")
        with open(temp_audit_path, 'r') as f:
            events = [json.loads(line) for line in f.readlines()]
        denied = [e for e in events if e["event_type"] == "tier_denied"]
        assert len(denied) > 0


class TestMemberAccess:
    """Test MEMBER tier access control."""

    def test_member_can_access_all_models(self, enforcer):
        enforcer.set_user_tier("member_user", UserTier.MEMBER)
        for model in ["claude-haiku-4-5", "claude-sonnet-3", "claude-opus-4"]:
            result = enforcer.check_model_access("member_user", model)
            assert result["allowed"] is True

    def test_member_access_writes_audit(self, enforcer, temp_audit_path):
        enforcer.set_user_tier("member_user", UserTier.MEMBER)
        enforcer.check_model_access("member_user", "claude-opus-4")
        with open(temp_audit_path, 'r') as f:
            events = [json.loads(line) for line in f.readlines()]
        checked = [e for e in events if e["event_type"] == "tier_checked"]
        assert len(checked) > 0


class TestTierEnforcement:
    """Test tier setting and enforcement."""

    def test_set_user_tier_free(self, enforcer):
        result = enforcer.set_user_tier("user1", UserTier.FREE)
        assert result["tier"] == "free"

    def test_set_user_tier_member(self, enforcer):
        result = enforcer.set_user_tier("user1", UserTier.MEMBER)
        assert result["tier"] == "member"

    def test_set_tier_writes_audit(self, enforcer, temp_audit_path):
        enforcer.set_user_tier("user1", UserTier.MEMBER)
        with open(temp_audit_path, 'r') as f:
            events = [json.loads(line) for line in f.readlines()]
        enforced = [e for e in events if e["event_type"] == "tier_enforced"]
        assert len(enforced) > 0

    def test_get_user_tier(self, enforcer):
        enforcer.set_user_tier("user1", UserTier.MEMBER)
        tier = enforcer.get_user_tier("user1")
        assert tier == UserTier.MEMBER

    def test_default_tier_is_free(self, enforcer):
        tier = enforcer.get_user_tier("unknown_user")
        assert tier == UserTier.FREE


class TestTierExpiry:
    """Test tier expiration and auto-downgrade."""

    def test_set_tier_with_expiry(self, enforcer):
        result = enforcer.set_user_tier("user1", UserTier.MEMBER, expire_days=30)
        assert result["expires_at"] is not None

    def test_expiry_downgrade_not_triggered_before_expire(self, enforcer):
        enforcer.set_user_tier("user1", UserTier.MEMBER, expire_days=30)
        result = enforcer.check_and_downgrade_on_expiry("user1")
        assert result is None  # Not expired yet

    def test_expiry_downgrade_triggered_after_expire(self, enforcer):
        # Set tier to expire 1 second ago
        expire_date = datetime.utcnow() - timedelta(seconds=1)
        enforcer._tier_expire_date["user1"] = expire_date
        enforcer._user_tiers["user1"] = UserTier.MEMBER

        result = enforcer.check_and_downgrade_on_expiry("user1")
        assert result is not None
        assert result["new_tier"] == "free"

    def test_expiry_downgrade_writes_audit(self, enforcer, temp_audit_path):
        expire_date = datetime.utcnow() - timedelta(seconds=1)
        enforcer._tier_expire_date["user1"] = expire_date
        enforcer._user_tiers["user1"] = UserTier.MEMBER

        enforcer.check_and_downgrade_on_expiry("user1")
        with open(temp_audit_path, 'r') as f:
            events = [json.loads(line) for line in f.readlines()]
        downgraded = [e for e in events if e["event_type"] == "tier_downgrade"]
        assert len(downgraded) > 0

    def test_auto_downgrade_on_model_access(self, enforcer):
        # Set MEMBER tier that expires now
        expire_date = datetime.utcnow() - timedelta(seconds=1)
        enforcer._tier_expire_date["user1"] = expire_date
        enforcer._user_tiers["user1"] = UserTier.MEMBER

        # Check access to Opus (should be denied because tier expired)
        result = enforcer.check_model_access("user1", "claude-opus-4")
        assert result["allowed"] is False
        assert result["user_tier"] == "free"


class TestModelAvailability:
    """Test model availability by tier."""

    def test_get_available_models_free(self, enforcer):
        models = enforcer.get_available_models(UserTier.FREE)
        assert models["claude-haiku-4-5"] is True
        assert models["claude-sonnet-3"] is True
        assert models["claude-opus-4"] is False

    def test_get_available_models_member(self, enforcer):
        models = enforcer.get_available_models(UserTier.MEMBER)
        assert models["claude-haiku-4-5"] is True
        assert models["claude-sonnet-3"] is True
        assert models["claude-opus-4"] is True

    def test_get_available_models_enterprise(self, enforcer):
        models = enforcer.get_available_models(UserTier.ENTERPRISE)
        assert models["claude-haiku-4-5"] is True
        assert models["claude-sonnet-3"] is True
        assert models["claude-opus-4"] is True


class TestAuditTrail:
    """Test audit trail integration."""

    def test_all_tier_checks_audited(self, enforcer, temp_audit_path):
        enforcer.check_model_access("user1", "claude-opus-4")
        with open(temp_audit_path, 'r') as f:
            lines = f.readlines()
        assert len(lines) > 0

    def test_audit_chain_integrity(self, enforcer, temp_audit_path):
        enforcer.set_user_tier("user1", UserTier.MEMBER)
        enforcer.check_model_access("user1", "claude-opus-4")
        with open(temp_audit_path, 'r') as f:
            lines = f.readlines()
        if len(lines) > 1:
            prev = json.loads(lines[0])
            curr = json.loads(lines[1])
            assert curr["prev_hash"] == prev["hash"]

    def test_audit_fail_closed(self, enforcer):
        enforcer.audit_chain.log_path = Path("/nonexistent/audit.jsonl")
        with pytest.raises(RuntimeError, match="Failed to write"):
            enforcer.set_user_tier("user1", UserTier.MEMBER)

    def test_tenant_isolation(self, temp_audit_path):
        audit_chain = AuditChainWriter(temp_audit_path)
        enf1 = TierEnforcer("tenant_1", audit_chain)
        enf2 = TierEnforcer("tenant_2", audit_chain)

        enf1.set_user_tier("user1", UserTier.MEMBER)
        enf2.set_user_tier("user2", UserTier.MEMBER)

        with open(temp_audit_path, 'r') as f:
            events = [json.loads(line) for line in f.readlines()]
        tenants = {e["tenant_id"] for e in events}
        assert "tenant_1" in tenants and "tenant_2" in tenants


class TestPerformance:
    """Test performance SLA (<10ms inherited from QuotaEnforcer)."""

    def test_check_under_10ms(self, enforcer):
        enforcer.set_user_tier("user1", UserTier.MEMBER)
        start = time.time()
        for _ in range(20):
            enforcer.check_model_access("user1", "claude-opus-4")
        elapsed = (time.time() - start) / 20
        assert elapsed < 0.010

    def test_enforcement_under_10ms(self, enforcer):
        start = time.time()
        for _ in range(20):
            enforcer.set_user_tier(f"user{_}", UserTier.MEMBER)
        elapsed = (time.time() - start) / 20
        assert elapsed < 0.010


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_unknown_model(self, enforcer):
        with pytest.raises(ValueError):
            enforcer.check_model_access("user1", "unknown-model")

    def test_thread_safety(self, enforcer):
        import threading
        def worker():
            for i in range(10):
                enforcer.set_user_tier(f"user{i}", UserTier.MEMBER)
                enforcer.check_model_access(f"user{i}", "claude-opus-4")

        threads = [threading.Thread(target=worker) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # No crashes = success
