"""Test suite for QuotaEnforcer (15 tests, k=2).

Test categories:
1. Quota checking correctness (COMMUNITY vs MEMBER limits)
2. Daily reset behavior
3. Rejection logic (429 status code)
4. Performance SLA (<10ms per check)
5. Audit trail integration (events written synchronously, fail-closed)
6. Usage tracking and cost calculation
"""

import time
import tempfile
import json
from pathlib import Path
from datetime import datetime, timedelta
from decimal import Decimal
import pytest

from core.license.quota_enforcer import QuotaEnforcer, QuotaUsage
from core.licensing.billing import (
    BillingSchema,
    ModelTier,
    create_default_billing_schema,
)
from core.compliance.audit_chain_writer import AuditChainWriter


@pytest.fixture
def temp_audit_path():
    """Create a temporary audit file for testing."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
        path = f.name
    yield path
    Path(path).unlink(missing_ok=True)


@pytest.fixture
def audit_chain(temp_audit_path):
    """Create an AuditChainWriter for testing."""
    return AuditChainWriter(temp_audit_path)


@pytest.fixture
def billing_schema():
    """Create default billing schema."""
    return create_default_billing_schema()


@pytest.fixture
def enforcer(audit_chain, billing_schema):
    """Create a QuotaEnforcer for testing."""
    return QuotaEnforcer(
        tenant_id="_default",
        billing_schema=billing_schema,
        audit_chain=audit_chain,
    )


class TestCommunityQuotaLimit:
    """Test COMMUNITY tier quota limits: 50 req/day, 100k tokens/day."""

    def test_community_request_limit(self, enforcer):
        """COMMUNITY tier allows 50 requests/day, rejects on 51st."""
        # Allow first 50
        for i in range(50):
            result = enforcer.check_quota(
                user_id="user1",
                tier=ModelTier.COMMUNITY,
                model_id="claude-haiku-4-5",
                request_tokens=1000,
            )
            assert result["allowed"] is True
            assert result["status_code"] == 200
            assert result["requests_remaining"] == (49 - i)

        # Reject 51st
        result = enforcer.check_quota(
            user_id="user1",
            tier=ModelTier.COMMUNITY,
            model_id="claude-haiku-4-5",
            request_tokens=1000,
        )
        assert result["allowed"] is False
        assert result["status_code"] == 429
        assert "request limit" in result["reason"].lower()

    def test_community_token_limit(self, enforcer):
        """COMMUNITY tier allows 100k tokens/day, rejects if exceeded."""
        # Use 90k tokens (9 requests of 10k each)
        for _ in range(9):
            result = enforcer.check_quota(
                user_id="user2",
                tier=ModelTier.COMMUNITY,
                model_id="claude-haiku-4-5",
                request_tokens=10_000,
            )
            assert result["allowed"] is True

        # 11th request (100k total) should be rejected
        result = enforcer.check_quota(
            user_id="user2",
            tier=ModelTier.COMMUNITY,
            model_id="claude-haiku-4-5",
            request_tokens=15_000,  # Would exceed
        )
        assert result["allowed"] is False
        assert result["status_code"] == 429
        assert "token limit" in result["reason"].lower()

    def test_community_quota_remaining(self, enforcer):
        """remaining counts decrease correctly."""
        result = enforcer.check_quota(
            user_id="user3",
            tier=ModelTier.COMMUNITY,
            model_id="claude-haiku-4-5",
            request_tokens=50_000,
        )

        assert result["requests_remaining"] == 49  # 50 - 1
        assert result["tokens_remaining"] == 50_000  # 100k - 50k


class TestMemberQuotaUnlimited:
    """Test MEMBER tier: unlimited requests and tokens."""

    def test_member_unlimited_requests(self, enforcer):
        """MEMBER tier allows unlimited requests (no rejection)."""
        for _ in range(200):  # Well over the 50-request COMMUNITY limit
            result = enforcer.check_quota(
                user_id="member_user",
                tier=ModelTier.MEMBER,
                model_id="claude-opus-4",
                request_tokens=1000,
            )
            assert result["allowed"] is True
            assert result["status_code"] == 200

    def test_member_unlimited_tokens(self, enforcer):
        """MEMBER tier allows unlimited tokens."""
        for _ in range(100):
            result = enforcer.check_quota(
                user_id="member_user",
                tier=ModelTier.MEMBER,
                model_id="claude-opus-4",
                request_tokens=100_000,  # 100k tokens per request
            )
            assert result["allowed"] is True
            assert result["status_code"] == 200


class TestDailyReset:
    """Test daily quota reset at UTC midnight."""

    def test_reset_on_new_day(self, enforcer):
        """Quota resets when calendar day changes."""
        # Use up quota on "today"
        for _ in range(50):
            enforcer.check_quota(
                user_id="user4",
                tier=ModelTier.COMMUNITY,
                model_id="claude-haiku-4-5",
                request_tokens=1000,
            )

        # Next request should be rejected (used up 50)
        result = enforcer.check_quota(
            user_id="user4",
            tier=ModelTier.COMMUNITY,
            model_id="claude-haiku-4-5",
            request_tokens=1000,
        )
        assert result["allowed"] is False

        # Manually reset and try again (simulating new day)
        enforcer.reset_user_quota("user4")
        result = enforcer.check_quota(
            user_id="user4",
            tier=ModelTier.COMMUNITY,
            model_id="claude-haiku-4-5",
            request_tokens=1000,
        )
        assert result["allowed"] is True


class TestRejectionLogic:
    """Test rejection logic and status codes."""

    def test_rejection_returns_429(self, enforcer):
        """Rejection returns status_code 429 (Quota Exhausted)."""
        # Fill quota
        for _ in range(50):
            enforcer.check_quota(
                user_id="user5",
                tier=ModelTier.COMMUNITY,
                model_id="claude-haiku-4-5",
                request_tokens=1000,
            )

        # Reject
        result = enforcer.check_quota(
            user_id="user5",
            tier=ModelTier.COMMUNITY,
            model_id="claude-haiku-4-5",
            request_tokens=1000,
        )
        assert result["status_code"] == 429

    def test_rejection_includes_reset_time(self, enforcer):
        """Rejection includes reset_at (next UTC midnight)."""
        # Fill quota
        for _ in range(50):
            enforcer.check_quota(
                user_id="user6",
                tier=ModelTier.COMMUNITY,
                model_id="claude-haiku-4-5",
                request_tokens=1000,
            )

        result = enforcer.check_quota(
            user_id="user6",
            tier=ModelTier.COMMUNITY,
            model_id="claude-haiku-4-5",
            request_tokens=1000,
        )

        # reset_at should be in the future
        assert "reset_at" in result
        assert isinstance(result["reset_at"], str)
        assert result["reset_at"].endswith("Z")  # ISO 8601 UTC format
        # Reset time should be roughly 24 hours from now (within a day)
        reset_dt = datetime.fromisoformat(result["reset_at"].replace("Z", "+00:00"))
        now_utc = datetime.fromisoformat(datetime.utcnow().isoformat() + "+00:00")
        assert reset_dt > now_utc  # Should be in the future


class TestPerformanceSLA:
    """Test performance SLA: <10ms per check."""

    def test_check_under_10ms(self, enforcer):
        """check_quota() completes in <10ms."""
        # Warm up
        enforcer.check_quota(
            user_id="perf_user",
            tier=ModelTier.COMMUNITY,
            model_id="claude-haiku-4-5",
            request_tokens=1000,
        )

        # Measure
        start = time.time()
        for _ in range(20):
            enforcer.check_quota(
                user_id="perf_user",
                tier=ModelTier.COMMUNITY,
                model_id="claude-haiku-4-5",
                request_tokens=1000,
            )
        elapsed = (time.time() - start) / 20  # Average

        assert elapsed < 0.010, f"check_quota() took {elapsed*1000:.2f}ms (SLA: <10ms)"

    def test_check_consistent_performance(self, enforcer):
        """Performance is consistent across calls."""
        times = []
        for _ in range(20):
            start = time.time()
            enforcer.check_quota(
                user_id="perf_user2",
                tier=ModelTier.COMMUNITY,
                model_id="claude-haiku-4-5",
                request_tokens=1000,
            )
            times.append(time.time() - start)

        # All should be <10ms
        assert all(t < 0.010 for t in times)


class TestAuditTrailIntegration:
    """Test audit trail: events written synchronously, fail-closed."""

    def test_audit_event_written_on_allowed(self, enforcer, temp_audit_path):
        """Allowed quota check writes quota_checked event."""
        enforcer.check_quota(
            user_id="user7",
            tier=ModelTier.COMMUNITY,
            model_id="claude-haiku-4-5",
            request_tokens=1000,
        )

        # Read audit trail
        with open(temp_audit_path, 'r') as f:
            lines = f.readlines()

        assert len(lines) > 0
        last_event = json.loads(lines[-1])
        assert last_event["event_type"] == "quota_checked"
        assert last_event["details"]["allowed"] is True

    def test_audit_event_written_on_rejected(self, enforcer, temp_audit_path):
        """Rejected quota check writes quota_denied event."""
        # Fill quota
        for _ in range(50):
            enforcer.check_quota(
                user_id="user8",
                tier=ModelTier.COMMUNITY,
                model_id="claude-haiku-4-5",
                request_tokens=1000,
            )

        # Reject
        enforcer.check_quota(
            user_id="user8",
            tier=ModelTier.COMMUNITY,
            model_id="claude-haiku-4-5",
            request_tokens=1000,
        )

        # Read audit trail
        with open(temp_audit_path, 'r') as f:
            lines = f.readlines()

        # Find the rejection event
        events = [json.loads(line) for line in lines]
        denied_events = [e for e in events if e["event_type"] == "quota_denied"]
        assert len(denied_events) > 0
        assert denied_events[-1]["details"]["allowed"] is False

    def test_audit_fail_closed(self, enforcer):
        """If audit chain write fails, check_quota() raises RuntimeError."""
        enforcer.audit_chain.log_path = Path("/nonexistent/path/audit.jsonl")

        with pytest.raises(RuntimeError, match="Audit chain write failed"):
            enforcer.check_quota(
                user_id="user9",
                tier=ModelTier.COMMUNITY,
                model_id="claude-haiku-4-5",
                request_tokens=1000,
            )

    def test_audit_tenant_isolation(self, audit_chain, billing_schema):
        """Audit events are tenant-scoped."""
        enforcer1 = QuotaEnforcer("tenant_1", billing_schema, audit_chain)
        enforcer2 = QuotaEnforcer("tenant_2", billing_schema, audit_chain)

        enforcer1.check_quota(
            user_id="user",
            tier=ModelTier.COMMUNITY,
            model_id="claude-haiku-4-5",
            request_tokens=1000,
        )

        enforcer2.check_quota(
            user_id="user",
            tier=ModelTier.COMMUNITY,
            model_id="claude-haiku-4-5",
            request_tokens=1000,
        )

        # Verify both tenant_ids are present
        with open(audit_chain.log_path, 'r') as f:
            events = [json.loads(line) for line in f.readlines()]

        tenant_ids = {e["tenant_id"] for e in events}
        assert "tenant_1" in tenant_ids
        assert "tenant_2" in tenant_ids


class TestUsageTracking:
    """Test usage tracking and cost calculation."""

    def test_get_usage_initial(self, enforcer):
        """New user has zero usage."""
        usage = enforcer.get_usage("new_user")
        assert usage["request_count"] == 0
        assert usage["token_count"] == 0
        assert usage["cost_eur"] == 0.0

    def test_get_usage_after_checks(self, enforcer):
        """Usage accumulates after checks."""
        for _ in range(5):
            enforcer.check_quota(
                user_id="user10",
                tier=ModelTier.COMMUNITY,
                model_id="claude-haiku-4-5",
                request_tokens=10_000,
            )

        usage = enforcer.get_usage("user10")
        assert usage["request_count"] == 5
        assert usage["token_count"] == 50_000

    def test_record_usage_cost_calculation(self, enforcer, billing_schema):
        """Cost is calculated correctly on record_usage()."""
        enforcer.record_usage(
            user_id="user11",
            request_tokens=1000,
            output_tokens=500,
            model_id="claude-haiku-4-5",
        )

        usage = enforcer.get_usage("user11")

        # Haiku costs 0.0008 per input token and 0.004 per output token
        expected_cost = (
            Decimal("1000") * Decimal("0.0000008") +
            Decimal("500") * Decimal("0.000004")
        )
        assert abs(float(usage["cost_eur"]) - float(expected_cost)) < 0.0001


class TestStatistics:
    """Test enforcement statistics."""

    def test_get_stats(self, enforcer):
        """get_stats() returns enforcement statistics."""
        for i in range(3):
            enforcer.check_quota(
                user_id=f"user{i}",
                tier=ModelTier.COMMUNITY,
                model_id="claude-haiku-4-5",
                request_tokens=1000,
            )

        stats = enforcer.get_stats()
        assert stats["tracked_users"] == 3
        assert stats["total_rejections"] == 0

    def test_rejection_counter(self, enforcer):
        """Rejection counter increments on rejections."""
        # Fill quota
        for _ in range(50):
            enforcer.check_quota(
                user_id="user12",
                tier=ModelTier.COMMUNITY,
                model_id="claude-haiku-4-5",
                request_tokens=1000,
            )

        # Reject 5 times
        for _ in range(5):
            enforcer.check_quota(
                user_id="user12",
                tier=ModelTier.COMMUNITY,
                model_id="claude-haiku-4-5",
                request_tokens=1000,
            )

        stats = enforcer.get_stats()
        assert stats["total_rejections"] == 5
