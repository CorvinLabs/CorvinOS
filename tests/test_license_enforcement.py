"""Comprehensive tests for Brain v0.2 + Forge License Enforcement (ADR-0365).

Tests quota enforcement for:
- brain_tasks_per_day (10/day free, unlimited member)
- tool_forge_per_day (3/day free, unlimited member)
- skill_forge_per_day (3/day free, unlimited member)

Verify:
1. Free tier: hard limits enforced
2. Member tier: unlimited access
3. Cross-tenant isolation
4. Atomic increment-and-check
5. Quota reset at daily boundary
"""

import pytest
from pathlib import Path
from datetime import datetime, timezone
# `operator/` is not importable as a package (stdlib `operator` shadows it),
# so this module is loaded by file path -- see load_operator_module in conftest.py.
from corvin_test_support import load_operator_module

_quota = load_operator_module("license/quota_counter.py")
_limits = load_operator_module("license/limits.py")
increment_and_check = _quota.increment_and_check
get_today_count = _quota.get_today_count
LicenseLimitError = _limits.LicenseLimitError


class TestBrainTasksQuota:
    """Test brain_tasks_per_day quota enforcement (free: 10/day)."""

    def test_free_tier_accepts_10_tasks(self, tmp_path, monkeypatch):
        """Free tier: first 10 tasks accepted."""
        # Mock get_limit to return 10
        def mock_get_limit(feature):
            if feature == "brain_tasks_per_day":
                return 10
            return None

        monkeypatch.setattr(
            _quota, "get_limit",
            mock_get_limit,
            raising=False,
        )

        # Can use up to 10 tasks
        for i in range(10):
            count = increment_and_check(
                tmp_path, "brain_tasks_per_day", "free-tenant"
            )
            assert count == i + 1, f"Expected count {i + 1}, got {count}"

    def test_free_tier_rejects_11th_task(self, tmp_path, monkeypatch):
        """Free tier: 11th task is rejected."""
        def mock_get_limit(feature):
            if feature == "brain_tasks_per_day":
                return 10
            return None

        monkeypatch.setattr(
            _quota, "get_limit",
            mock_get_limit,
            raising=False,
        )

        # Use up 10
        for i in range(10):
            increment_and_check(tmp_path, "brain_tasks_per_day", "free-tenant")

        # 11th should fail
        with pytest.raises(LicenseLimitError) as exc_info:
            increment_and_check(tmp_path, "brain_tasks_per_day", "free-tenant")

        assert "brain_tasks_per_day" in str(exc_info.value)

    def test_free_tier_count_verification(self, tmp_path, monkeypatch):
        """Verify internal counter matches quota system."""
        def mock_get_limit(feature):
            if feature == "brain_tasks_per_day":
                return 10
            return None

        monkeypatch.setattr(
            _quota, "get_limit",
            mock_get_limit,
            raising=False,
        )

        # Check count after several increments
        for _ in range(5):
            increment_and_check(tmp_path, "brain_tasks_per_day", "free-tenant")

        count = get_today_count(tmp_path, "brain_tasks_per_day", "free-tenant")
        assert count == 5


class TestToolForgeQuota:
    """Test tool_forge_per_day quota enforcement (free: 3/day)."""

    def test_free_tier_accepts_3_forges(self, tmp_path, monkeypatch):
        """Free tier: 3 tool forges allowed."""
        def mock_get_limit(feature):
            if feature == "tool_forge_per_day":
                return 3
            return None

        monkeypatch.setattr(
            _quota, "get_limit",
            mock_get_limit,
            raising=False,
        )

        for i in range(3):
            count = increment_and_check(
                tmp_path, "tool_forge_per_day", "free-tenant"
            )
            assert count == i + 1

    def test_free_tier_rejects_4th_forge(self, tmp_path, monkeypatch):
        """Free tier: 4th forge is rejected."""
        def mock_get_limit(feature):
            if feature == "tool_forge_per_day":
                return 3
            return None

        monkeypatch.setattr(
            _quota, "get_limit",
            mock_get_limit,
            raising=False,
        )

        for i in range(3):
            increment_and_check(tmp_path, "tool_forge_per_day", "free-tenant")

        with pytest.raises(LicenseLimitError) as exc_info:
            increment_and_check(tmp_path, "tool_forge_per_day", "free-tenant")

        assert "tool_forge_per_day" in str(exc_info.value)


class TestSkillForgeQuota:
    """Test skill_forge_per_day quota enforcement (free: 3/day)."""

    def test_free_tier_accepts_3_skills(self, tmp_path, monkeypatch):
        """Free tier: 3 skill creates allowed."""
        def mock_get_limit(feature):
            if feature == "skill_forge_per_day":
                return 3
            return None

        monkeypatch.setattr(
            _quota, "get_limit",
            mock_get_limit,
            raising=False,
        )

        for i in range(3):
            count = increment_and_check(
                tmp_path, "skill_forge_per_day", "free-tenant"
            )
            assert count == i + 1

    def test_free_tier_rejects_4th_skill(self, tmp_path, monkeypatch):
        """Free tier: 4th skill is rejected."""
        def mock_get_limit(feature):
            if feature == "skill_forge_per_day":
                return 3
            return None

        monkeypatch.setattr(
            _quota, "get_limit",
            mock_get_limit,
            raising=False,
        )

        for i in range(3):
            increment_and_check(tmp_path, "skill_forge_per_day", "free-tenant")

        with pytest.raises(LicenseLimitError) as exc_info:
            increment_and_check(tmp_path, "skill_forge_per_day", "free-tenant")

        assert "skill_forge_per_day" in str(exc_info.value)


class TestMemberTierUnlimited:
    """Test that member tier has unlimited access."""

    def test_member_brain_tasks_unlimited(self, tmp_path, monkeypatch):
        """Member tier: unlimited brain tasks."""
        def mock_get_limit(feature):
            return None  # None = unlimited

        monkeypatch.setattr(
            _quota, "get_limit",
            mock_get_limit,
            raising=False,
        )

        # Should never raise even after 100+ attempts
        for i in range(100):
            count = increment_and_check(
                tmp_path, "brain_tasks_per_day", "member-tenant"
            )
            # Member tier doesn't track (returns 0)
            assert count == 0

    def test_member_tool_forge_unlimited(self, tmp_path, monkeypatch):
        """Member tier: unlimited tool forges."""
        def mock_get_limit(feature):
            return None  # None = unlimited

        monkeypatch.setattr(
            _quota, "get_limit",
            mock_get_limit,
            raising=False,
        )

        for i in range(50):
            count = increment_and_check(
                tmp_path, "tool_forge_per_day", "member-tenant"
            )
            assert count == 0

    def test_member_skill_forge_unlimited(self, tmp_path, monkeypatch):
        """Member tier: unlimited skill forges."""
        def mock_get_limit(feature):
            return None  # None = unlimited

        monkeypatch.setattr(
            _quota, "get_limit",
            mock_get_limit,
            raising=False,
        )

        for i in range(50):
            count = increment_and_check(
                tmp_path, "skill_forge_per_day", "member-tenant"
            )
            assert count == 0


class TestCrossTenantIsolation:
    """Test that quotas are isolated per tenant."""

    def test_quotas_isolated_per_tenant(self, tmp_path, monkeypatch):
        """Different tenants have separate quota counters."""
        def mock_get_limit(feature):
            if feature == "brain_tasks_per_day":
                return 3  # Low limit for testing
            return None

        monkeypatch.setattr(
            _quota, "get_limit",
            mock_get_limit,
            raising=False,
        )

        # Tenant A uses 2
        increment_and_check(tmp_path, "brain_tasks_per_day", "tenant-a")
        increment_and_check(tmp_path, "brain_tasks_per_day", "tenant-a")

        # Tenant B uses 2 independently
        increment_and_check(tmp_path, "brain_tasks_per_day", "tenant-b")
        increment_and_check(tmp_path, "brain_tasks_per_day", "tenant-b")

        # Tenant A still has 1 left, not 0
        count_a = get_today_count(tmp_path, "brain_tasks_per_day", "tenant-a")
        count_b = get_today_count(tmp_path, "brain_tasks_per_day", "tenant-b")

        assert count_a == 2
        assert count_b == 2

        # Tenant A can use 1 more
        increment_and_check(tmp_path, "brain_tasks_per_day", "tenant-a")

        # But not a 4th
        with pytest.raises(LicenseLimitError):
            increment_and_check(tmp_path, "brain_tasks_per_day", "tenant-a")

        # Tenant B can also use 1 more
        increment_and_check(tmp_path, "brain_tasks_per_day", "tenant-b")

        # And still has independent quota
        with pytest.raises(LicenseLimitError):
            increment_and_check(tmp_path, "brain_tasks_per_day", "tenant-b")


class TestAtomicity:
    """Test that quota increment is atomic across concurrent threads."""

    def test_concurrent_increments_atomic(self, tmp_path, monkeypatch):
        """Multiple threads cannot race past the quota limit."""
        import threading

        def mock_get_limit(feature):
            if feature == "brain_tasks_per_day":
                return 5  # Small limit
            return None

        monkeypatch.setattr(
            _quota, "get_limit",
            mock_get_limit,
            raising=False,
        )

        results = []
        errors = []

        def worker():
            try:
                count = increment_and_check(
                    tmp_path, "brain_tasks_per_day", "concurrent-tenant"
                )
                results.append(count)
            except LicenseLimitError:
                errors.append("quota_exceeded")

        # Spawn 10 threads trying to increment simultaneously
        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Should have exactly 5 successes and 5 failures
        assert len(results) == 5, f"Expected 5 successes, got {len(results)}"
        assert len(errors) == 5, f"Expected 5 errors, got {len(errors)}"

        # Counts should be 1, 2, 3, 4, 5 (in some order)
        assert sorted(results) == [1, 2, 3, 4, 5]


class TestErrorHandling:
    """Test error handling in quota enforcement."""

    def test_graceful_handling_of_malformed_limit(self, tmp_path, monkeypatch):
        """Malformed limit values fail-closed (deny)."""
        def mock_get_limit(feature):
            if feature == "brain_tasks_per_day":
                return "unlimited"  # Malformed: should be int or None
            return None

        monkeypatch.setattr(
            _quota, "get_limit",
            mock_get_limit,
            raising=False,
        )

        # Malformed limit should raise LicenseLimitError (fail-closed)
        with pytest.raises(LicenseLimitError):
            increment_and_check(tmp_path, "brain_tasks_per_day", "free-tenant")

    def test_missing_counter_file_starts_fresh(self, tmp_path, monkeypatch):
        """Missing counter file is treated as count=0."""
        def mock_get_limit(feature):
            if feature == "brain_tasks_per_day":
                return 10
            return None

        monkeypatch.setattr(
            _quota, "get_limit",
            mock_get_limit,
            raising=False,
        )

        # No counter file exists — should start at 0 and increment to 1
        count = increment_and_check(
            tmp_path, "brain_tasks_per_day", "fresh-tenant"
        )
        assert count == 1

    def test_corrupted_counter_file_starts_fresh(self, tmp_path, monkeypatch):
        """Corrupted counter file is treated as count=0."""
        import json

        def mock_get_limit(feature):
            if feature == "brain_tasks_per_day":
                return 10
            return None

        monkeypatch.setattr(
            _quota, "get_limit",
            mock_get_limit,
            raising=False,
        )

        # Create a corrupted counter file
        quota_dir = tmp_path / "quotas"
        quota_dir.mkdir(parents=True, exist_ok=True)
        counter_file = quota_dir / "corrupted-tenant_brain_tasks_per_day_2026-08-17.json"
        counter_file.write_text("invalid json {{{")

        # Should start fresh despite corrupted file
        count = increment_and_check(
            tmp_path, "brain_tasks_per_day", "corrupted-tenant"
        )
        assert count == 1


class TestQuotaResetAtDayBoundary:
    """Test that quotas reset at UTC midnight."""

    def test_quota_file_separation_by_date(self, tmp_path, monkeypatch):
        """Different dates have separate counter files."""
        def mock_get_limit(feature):
            if feature == "brain_tasks_per_day":
                return 3
            return None

        monkeypatch.setattr(
            _quota, "get_limit",
            mock_get_limit,
            raising=False,
        )

        # Mock different dates
        original_today = None

        def mock_today_utc():
            # Return fixed date
            if hasattr(mock_today_utc, "call_count"):
                mock_today_utc.call_count += 1
            else:
                mock_today_utc.call_count = 1

            if mock_today_utc.call_count <= 3:
                return "2026-08-17"  # First 3 calls use Aug 17
            else:
                return "2026-08-18"  # Subsequent calls use Aug 18

        monkeypatch.setattr(
            _quota, "_today_utc",
            mock_today_utc,
        )

        # Use 3 on Aug 17
        for _ in range(3):
            increment_and_check(tmp_path, "brain_tasks_per_day", "date-test-tenant")

        # The next _today_utc() call must STILL be Aug 17 (call_count <= 3
        # → Aug 17), so rewind to 2: the 4th increment sees Aug 17 and fails.
        mock_today_utc.call_count = 2

        # Try to use more on Aug 17 — should fail
        with pytest.raises(LicenseLimitError):
            increment_and_check(tmp_path, "brain_tasks_per_day", "date-test-tenant")

        # But on Aug 18, quota should reset
        mock_today_utc.call_count = 3  # next call is #4 → Aug 18
        count = increment_and_check(
            tmp_path, "brain_tasks_per_day", "date-test-tenant"
        )
        assert count == 1  # First use on Aug 18


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
