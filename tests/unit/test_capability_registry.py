"""
Unit Tests for Capability Registry — ADR-0302

Tests for deny-by-default access control.
"""

import pytest

from core.capabilities import (
    CapabilityRegistry,
    CapabilityGrantError,
    CapabilityDeniedError,
)
from core.capabilities.registry import set_registry, get_registry


class TestCapabilityRegistryBasic:
    """Test basic capability registry."""

    @pytest.fixture
    def registry(self):
        """Create fresh registry for each test."""
        return CapabilityRegistry()

    def test_registry_deny_by_default(self, registry):
        """All capabilities denied by default."""
        assert registry.has_capability("user_1", "read", "tenant_1") is False
        assert registry.has_capability("user_1", "write", "tenant_1") is False

    def test_grant_capability(self, registry):
        """Grant a capability."""
        registry.grant("user_1", "read", "tenant_1")
        assert registry.has_capability("user_1", "read", "tenant_1") is True

    def test_grant_idempotent(self, registry):
        """Granting twice is idempotent."""
        g1 = registry.grant("user_1", "read", "tenant_1")
        g2 = registry.grant("user_1", "read", "tenant_1")
        assert g1.granted_at == g2.granted_at

    def test_check_capability_allowed(self, registry):
        """check_capability passes when granted."""
        registry.grant("user_1", "write", "tenant_1")
        # Should not raise
        registry.check_capability("user_1", "write", "tenant_1")

    def test_check_capability_denied(self, registry):
        """check_capability raises when denied."""
        with pytest.raises(CapabilityDeniedError):
            registry.check_capability("user_1", "write", "tenant_1")

    def test_different_capabilities_separate(self, registry):
        """Different capabilities are separate."""
        registry.grant("user_1", "read", "tenant_1")
        assert registry.has_capability("user_1", "read", "tenant_1") is True
        assert registry.has_capability("user_1", "write", "tenant_1") is False

    def test_different_tenants_separate(self, registry):
        """Tenants are isolated."""
        registry.grant("user_1", "read", "tenant_1")
        assert registry.has_capability("user_1", "read", "tenant_1") is True
        assert registry.has_capability("user_1", "read", "tenant_2") is False

    def test_different_actors_separate(self, registry):
        """Actors are separate."""
        registry.grant("user_1", "read", "tenant_1")
        assert registry.has_capability("user_1", "read", "tenant_1") is True
        assert registry.has_capability("user_2", "read", "tenant_1") is False


class TestCapabilityRegistryGrants:
    """Test grant records and audit."""

    @pytest.fixture
    def registry(self):
        """Create registry."""
        return CapabilityRegistry()

    def test_grant_record_stores_metadata(self, registry):
        """Grant records store full metadata."""
        registry.grant(
            "user_1", "admin", "tenant_1", granted_by="admin_id", reason="onboarding"
        )
        record = registry.get_grant_record("user_1", "admin", "tenant_1")

        assert record is not None
        assert record.actor == "user_1"
        assert record.capability == "admin"
        assert record.granted_by == "admin_id"
        assert record.reason == "onboarding"
        assert record.granted_at is not None

    def test_get_grants_for_actor(self, registry):
        """List grants for an actor."""
        registry.grant("user_1", "read", "tenant_1")
        registry.grant("user_1", "write", "tenant_1")
        registry.grant("user_1", "admin", "tenant_2")

        grants = registry.get_grants_for_actor("user_1", "tenant_1")
        assert set(grants) == {"read", "write"}

    def test_get_all_grants(self, registry):
        """Get all grants."""
        registry.grant("user_1", "read", "tenant_1")
        registry.grant("user_2", "write", "tenant_2")

        all_grants = registry.get_all_grants()
        assert len(all_grants) == 2

    def test_grant_count(self, registry):
        """Count grants."""
        registry.grant("user_1", "read", "tenant_1")
        registry.grant("user_1", "write", "tenant_1")
        assert registry.grant_count() == 2


class TestCapabilityRegistryFreezing:
    """Test readonly/freezing."""

    @pytest.fixture
    def registry(self):
        """Create registry."""
        return CapabilityRegistry()

    def test_registry_unfrozen_by_default(self, registry):
        """Registry starts unfrozen."""
        assert registry.is_readonly() is False

    def test_freeze_registry(self, registry):
        """Freeze makes registry readonly."""
        registry.freeze()
        assert registry.is_readonly() is True

    def test_grant_fails_when_frozen(self, registry):
        """Cannot grant on frozen registry."""
        registry.freeze()

        with pytest.raises(CapabilityGrantError):
            registry.grant("user_1", "read", "tenant_1")

    def test_grant_before_freeze_succeeds(self, registry):
        """Grants before freezing are persisted."""
        registry.grant("user_1", "read", "tenant_1")
        registry.freeze()

        # Check persists
        assert registry.has_capability("user_1", "read", "tenant_1") is True


class TestCapabilityRegistryContextVar:
    """Test ContextVar isolation."""

    def test_default_registry(self):
        """Default global registry."""
        registry = get_registry()
        assert registry is not None

    def test_set_registry_context(self):
        """Can set registry per context."""
        custom = CapabilityRegistry()
        custom.grant("user_1", "read", "tenant_1")

        set_registry(custom)
        current = get_registry()

        assert current is custom
        assert current.has_capability("user_1", "read", "tenant_1") is True

    def test_separate_registry_contexts(self):
        """Each context can have separate registry."""
        reg1 = CapabilityRegistry()
        reg1.grant("user_1", "read", "tenant_1")

        reg2 = CapabilityRegistry()
        reg2.grant("user_2", "write", "tenant_2")

        assert reg1.has_capability("user_1", "read", "tenant_1") is True
        assert reg1.has_capability("user_2", "write", "tenant_2") is False

        assert reg2.has_capability("user_1", "read", "tenant_1") is False
        assert reg2.has_capability("user_2", "write", "tenant_2") is True


class TestCapabilityRegistryIntegration:
    """Integration tests."""

    def test_multi_actor_multi_capability_setup(self):
        """Complex multi-actor, multi-capability scenario."""
        registry = CapabilityRegistry()

        # Admin has all capabilities
        admin_caps = ["read", "write", "delete", "admin"]
        for cap in admin_caps:
            registry.grant("admin_1", cap, "tenant_1")

        # User has limited capabilities
        registry.grant("user_1", "read", "tenant_1")
        registry.grant("user_1", "write", "tenant_1")

        # Another tenant is separate
        registry.grant("user_2", "read", "tenant_2")

        # Verify setup
        assert registry.get_grants_for_actor("admin_1", "tenant_1") == admin_caps
        assert registry.get_grants_for_actor("user_1", "tenant_1") == ["read", "write"]
        assert registry.get_grants_for_actor("user_2", "tenant_2") == ["read"]
        assert registry.get_grants_for_actor("user_2", "tenant_1") == []

    def test_deny_by_default_enforced(self):
        """Verify deny-by-default is enforced."""
        registry = CapabilityRegistry()

        # Grant selective capabilities
        registry.grant("user_1", "read", "tenant_1")

        # Verify only granted capability works
        registry.check_capability("user_1", "read", "tenant_1")

        # Every other check fails
        with pytest.raises(CapabilityDeniedError):
            registry.check_capability("user_1", "write", "tenant_1")

        with pytest.raises(CapabilityDeniedError):
            registry.check_capability("user_1", "read", "tenant_2")

        with pytest.raises(CapabilityDeniedError):
            registry.check_capability("user_2", "read", "tenant_1")

    def test_production_mode(self):
        """Registry can be frozen for production."""
        registry = CapabilityRegistry()

        # Setup initial grants
        registry.grant("admin_1", "admin", "tenant_1")
        registry.grant("user_1", "read", "tenant_1")

        # Freeze for production
        registry.freeze()

        # Existing grants work
        assert registry.has_capability("admin_1", "admin", "tenant_1") is True

        # New grants blocked
        with pytest.raises(CapabilityGrantError):
            registry.grant("user_2", "read", "tenant_1")


class TestCapabilityRegistryEdgeCases:
    """Test edge cases."""

    @pytest.fixture
    def registry(self):
        """Create registry."""
        return CapabilityRegistry()

    def test_empty_actor_name(self, registry):
        """Empty actor name is allowed (validates at gate, not here)."""
        registry.grant("", "read", "tenant_1")
        assert registry.has_capability("", "read", "tenant_1") is True

    def test_special_characters_in_names(self, registry):
        """Special characters in actor/capability/tenant names."""
        registry.grant("user@domain.com", "read:write", "tenant_1:prod")
        assert (
            registry.has_capability("user@domain.com", "read:write", "tenant_1:prod")
            is True
        )

    def test_many_grants(self, registry):
        """Registry handles many grants."""
        for i in range(100):
            registry.grant(f"user_{i}", f"cap_{i}", "tenant_1")

        assert registry.grant_count() == 100
        assert registry.has_capability("user_50", "cap_50", "tenant_1") is True
        assert registry.has_capability("user_50", "cap_51", "tenant_1") is False
