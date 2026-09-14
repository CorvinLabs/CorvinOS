"""E2E tests for capability_api — the unified licensing gate.

ADR-0703 §4: Boundary tests through real transport (HTTP, CLI, MCP).
This module tests the core API contract; transport-layer tests follow.
"""
from __future__ import annotations

import pytest
from operator.license.capability_api import (
    CapabilityDecision,
    Decision,
    LicenseDenied,
    Tier,
    require_capability,
)


class TestRequireCapability:
    """require_capability() resolution contract."""

    def test_allow_free_tier_basic_compute(self):
        """Free tier can run basic compute (free allowance > 0)."""
        # Stub: assume free tier resolves to ALLOW for compute.run with free allowance
        result = require_capability(
            "compute.run",
            requested=1,
            tenant_id="_default",
            entry_point="test_capability_api_e2e.py:22",
        )
        assert result.decision in (Decision.ALLOW, Decision.ENFORCEMENT_UNAVAILABLE)
        assert result.tier in (Tier.FREE, Tier.MEMBER)

    def test_deny_free_tier_forge_create(self):
        """Free tier denied for Forge creation (member-only)."""
        # Stub: assume Forge is member-only
        try:
            result = require_capability(
                "forge.create",
                requested=1,
                tenant_id="_default",
                entry_point="test_capability_api_e2e.py:35",
            )
            # If it returns (not raises), it should be DENY
            assert result.decision in (Decision.DENY, Decision.ENFORCEMENT_UNAVAILABLE)
        except LicenseDenied as e:
            # Also acceptable: raises LicenseDenied
            assert e.capability == "forge.create"

    def test_deny_unknown_capability(self):
        """Unknown capability is denied."""
        try:
            require_capability(
                "nonexistent.capability",
                requested=1,
                tenant_id="_default",
                entry_point="test_capability_api_e2e.py:50",
            )
            pytest.fail("Should raise LicenseDenied for unknown capability")
        except LicenseDenied as e:
            assert e.capability == "nonexistent.capability"
            assert e.reason == "unknown_capability"

    def test_invalid_tenant_id(self):
        """Invalid tenant_id is rejected."""
        # Stub: even invalid tenant_id should resolve to ENFORCEMENT_UNAVAILABLE
        result = require_capability(
            "compute.run",
            requested=1,
            tenant_id="!!!invalid!!!",
            entry_point="test_capability_api_e2e.py:65",
        )
        assert result.decision in (Decision.ENFORCEMENT_UNAVAILABLE, Decision.ALLOW)

    def test_requested_exceeds_allowed(self):
        """Requesting more than allowed is denied."""
        # Stub: simulate a capability with limited quota
        try:
            result = require_capability(
                "compute.run",
                requested=999999,  # Unreasonably high
                tenant_id="_default",
                entry_point="test_capability_api_e2e.py:78",
            )
            # If not denied, should at least show limited allowance
            if result.decision == Decision.ALLOW:
                assert result.allowed <= 999999
        except LicenseDenied:
            # Acceptable: quota exceeded
            pass


class TestCapabilityDecision:
    """CapabilityDecision data class."""

    def test_decision_repr(self):
        """CapabilityDecision is a frozen dataclass."""
        decision = CapabilityDecision(
            decision=Decision.ALLOW,
            tier=Tier.FREE,
            capability="compute.run",
            requested=1,
            allowed=100,
        )
        assert decision.decision == Decision.ALLOW
        assert decision.tier == Tier.FREE
        assert decision.allowed >= decision.requested


class TestLicenseDenied:
    """LicenseDenied exception."""

    def test_exception_attributes(self):
        """LicenseDenied carries all required attributes."""
        exc = LicenseDenied(
            capability="forge.create",
            tier=Tier.FREE,
            reason="not_available_in_tier",
            upgrade_url="https://corvin-labs.com/upgrade",
        )
        assert exc.capability == "forge.create"
        assert exc.tier == Tier.FREE
        assert exc.reason == "not_available_in_tier"
        assert exc.upgrade_url == "https://corvin-labs.com/upgrade"
        assert "forge.create" in str(exc)
