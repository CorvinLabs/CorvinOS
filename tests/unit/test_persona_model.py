"""Unit Tests for Persona Capability Model — ADR-0302

Tests for deny-by-default capability registry based on persona+role.
"""

import pytest
from core.context_engineering import (
    Persona,
    Role,
    Tier,
    Capability,
    PersonaRoleCapabilities,
    CapabilityRegistry,
    CapabilityLockError,
    CapabilityDeniedError,
    get_current_persona,
    set_current_persona,
    get_current_role,
    set_current_role,
    requires_capability,
)


class TestPersonaEnum:
    """Test Persona enum."""

    def test_persona_console_operator(self):
        """Persona has CONSOLE_OPERATOR variant."""
        assert Persona.CONSOLE_OPERATOR.value == "console_operator"

    def test_persona_voice_user(self):
        """Persona has VOICE_USER variant."""
        assert Persona.VOICE_USER.value == "voice_user"

    def test_persona_bridge_adapter(self):
        """Persona has BRIDGE_ADAPTER variant."""
        assert Persona.BRIDGE_ADAPTER.value == "bridge_adapter"

    def test_persona_mcp_tool(self):
        """Persona has MCP_TOOL variant."""
        assert Persona.MCP_TOOL.value == "mcp_tool"


class TestRoleEnum:
    """Test Role enum."""

    def test_role_admin(self):
        """Role has ADMIN variant."""
        assert Role.ADMIN.value == "admin"

    def test_role_operator(self):
        """Role has OPERATOR variant."""
        assert Role.OPERATOR.value == "operator"

    def test_role_user(self):
        """Role has USER variant."""
        assert Role.USER.value == "user"


class TestTierEnum:
    """Test Tier enum."""

    def test_tier_compliance(self):
        """Tier has COMPLIANCE variant."""
        assert Tier.COMPLIANCE.value == "compliance"

    def test_tier_standard(self):
        """Tier has STANDARD variant."""
        assert Tier.STANDARD.value == "standard"

    def test_tier_user(self):
        """Tier has USER variant."""
        assert Tier.USER.value == "user"


class TestCapabilityDataclass:
    """Test Capability dataclass."""

    def test_capability_creation(self):
        """Create a Capability."""
        cap = Capability(
            id="read_audit_log",
            description="Read audit logs",
            tier=Tier.COMPLIANCE,
        )
        assert cap.id == "read_audit_log"
        assert cap.description == "Read audit logs"
        assert cap.tier == Tier.COMPLIANCE
        assert cap.requires_mfa is False

    def test_capability_mfa_required(self):
        """Capability can require MFA."""
        cap = Capability(
            id="delete_user",
            description="Delete user",
            tier=Tier.COMPLIANCE,
            requires_mfa=True,
        )
        assert cap.requires_mfa is True

    def test_capability_frozen(self):
        """Capability is frozen (immutable)."""
        cap = Capability(
            id="read_audit_log",
            description="Read audit logs",
            tier=Tier.COMPLIANCE,
        )
        with pytest.raises(AttributeError):
            cap.id = "modified"


class TestCapabilityRegistryBasic:
    """Test basic capability registry operations."""

    @pytest.fixture
    def registry(self):
        """Create fresh registry for each test."""
        return CapabilityRegistry()

    def test_registry_deny_by_default(self, registry):
        """All capabilities denied by default."""
        result = registry.has_capability(
            Persona.CONSOLE_OPERATOR, Role.ADMIN, "read_audit_log"
        )
        assert result is False

    def test_register_capability(self, registry):
        """Register a capability definition."""
        cap = registry.register_capability(
            "read_audit_log",
            "Read audit logs",
            tier=Tier.COMPLIANCE,
        )
        assert cap.id == "read_audit_log"
        assert cap.tier == Tier.COMPLIANCE

    def test_grant_capability(self, registry):
        """Grant a capability to (persona, role)."""
        registry.register_capability(
            "read_audit_log",
            "Read audit logs",
            tier=Tier.STANDARD,
        )
        registry.grant(Persona.CONSOLE_OPERATOR, Role.ADMIN, "read_audit_log")
        assert registry.has_capability(
            Persona.CONSOLE_OPERATOR, Role.ADMIN, "read_audit_log"
        )

    def test_grant_unregistered_capability_raises(self, registry):
        """Granting unregistered capability raises ValueError."""
        with pytest.raises(ValueError):
            registry.grant(Persona.CONSOLE_OPERATOR, Role.ADMIN, "nonexistent")

    def test_revoke_capability(self, registry):
        """Revoke a capability from (persona, role)."""
        registry.register_capability(
            "read_audit_log",
            "Read audit logs",
            tier=Tier.STANDARD,
        )
        registry.grant(Persona.CONSOLE_OPERATOR, Role.ADMIN, "read_audit_log")
        assert registry.has_capability(
            Persona.CONSOLE_OPERATOR, Role.ADMIN, "read_audit_log"
        )
        registry.revoke(Persona.CONSOLE_OPERATOR, Role.ADMIN, "read_audit_log")
        assert not registry.has_capability(
            Persona.CONSOLE_OPERATOR, Role.ADMIN, "read_audit_log"
        )


class TestCapabilityRegistryTiers:
    """Test tier-based locking."""

    @pytest.fixture
    def registry(self):
        """Create fresh registry for each test."""
        return CapabilityRegistry()

    def test_register_compliance_tier_before_lock(self, registry):
        """Can register COMPLIANCE tier before lock_tier1()."""
        cap = registry.register_capability(
            "audit_immutability",
            "Cannot be revoked",
            tier=Tier.COMPLIANCE,
        )
        assert cap.tier == Tier.COMPLIANCE

    def test_lock_tier1(self, registry):
        """lock_tier1() prevents COMPLIANCE tier registration."""
        registry.lock_tier1()
        with pytest.raises(CapabilityLockError):
            registry.register_capability(
                "audit_immutability",
                "Cannot be revoked",
                tier=Tier.COMPLIANCE,
            )

    def test_can_register_standard_after_lock(self, registry):
        """Can still register STANDARD tier after lock_tier1()."""
        registry.lock_tier1()
        cap = registry.register_capability(
            "read_logs",
            "Read system logs",
            tier=Tier.STANDARD,
        )
        assert cap.tier == Tier.STANDARD

    def test_is_locked(self, registry):
        """is_locked() reflects lock state."""
        assert registry.is_locked() is False
        registry.lock_tier1()
        assert registry.is_locked() is True


class TestContextVariables:
    """Test persona/role context variables."""

    def test_get_set_persona(self):
        """Get and set current persona."""
        set_current_persona(Persona.CONSOLE_OPERATOR)
        assert get_current_persona() == Persona.CONSOLE_OPERATOR

    def test_get_set_role(self):
        """Get and set current role."""
        set_current_role(Role.ADMIN)
        assert get_current_role() == Role.ADMIN

    def test_context_isolation(self):
        """Context variables are isolated per task/thread."""
        # Note: full async isolation test would require asyncio/threading
        set_current_persona(Persona.CONSOLE_OPERATOR)
        set_current_role(Role.ADMIN)
        assert get_current_persona() == Persona.CONSOLE_OPERATOR
        assert get_current_role() == Role.ADMIN

        # Change context
        set_current_persona(Persona.VOICE_USER)
        set_current_role(Role.USER)
        assert get_current_persona() == Persona.VOICE_USER
        assert get_current_role() == Role.USER


class TestRequiresCapabilityDecorator:
    """Test @requires_capability decorator."""

    @pytest.fixture
    def setup_capability(self):
        """Setup test capability."""
        from core.context_engineering import REGISTRY

        REGISTRY.register_capability(
            "test_capability",
            "Test capability",
            tier=Tier.STANDARD,
        )
        REGISTRY.grant(Persona.CONSOLE_OPERATOR, Role.ADMIN, "test_capability")

    def test_decorator_allows_authorized(self, setup_capability):
        """Decorator allows function execution when authorized."""

        @requires_capability("test_capability")
        def protected_func():
            return "success"

        set_current_persona(Persona.CONSOLE_OPERATOR)
        set_current_role(Role.ADMIN)
        result = protected_func()
        assert result == "success"

    def test_decorator_denies_unauthorized(self, setup_capability):
        """Decorator raises CapabilityDeniedError when denied."""

        @requires_capability("test_capability")
        def protected_func():
            return "success"

        set_current_persona(Persona.VOICE_USER)
        set_current_role(Role.USER)
        with pytest.raises(CapabilityDeniedError):
            protected_func()

    def test_decorator_error_message(self, setup_capability):
        """Decorator error message is clear."""

        @requires_capability("test_capability")
        def protected_func():
            return "success"

        set_current_persona(Persona.BRIDGE_ADAPTER)
        set_current_role(Role.OPERATOR)
        try:
            protected_func()
        except CapabilityDeniedError as e:
            assert "bridge_adapter" in str(e)
            assert "test_capability" in str(e)


class TestGetCapabilities:
    """Test getting all capabilities for a (persona, role)."""

    @pytest.fixture
    def registry(self):
        """Create registry with test capabilities."""
        reg = CapabilityRegistry()
        reg.register_capability("read_audit", "Read audit", tier=Tier.STANDARD)
        reg.register_capability("write_config", "Write config", tier=Tier.STANDARD)
        reg.register_capability("admin_only", "Admin only", tier=Tier.COMPLIANCE)

        # Grant some capabilities
        reg.grant(Persona.CONSOLE_OPERATOR, Role.ADMIN, "read_audit")
        reg.grant(Persona.CONSOLE_OPERATOR, Role.ADMIN, "write_config")
        reg.grant(Persona.CONSOLE_OPERATOR, Role.ADMIN, "admin_only")
        reg.grant(Persona.CONSOLE_OPERATOR, Role.OPERATOR, "read_audit")

        return reg

    def test_get_capabilities_admin(self, registry):
        """Get capabilities for console_operator admin."""
        caps = registry.get_capabilities(Persona.CONSOLE_OPERATOR, Role.ADMIN)
        assert "read_audit" in caps
        assert "write_config" in caps
        assert "admin_only" in caps
        assert len(caps) == 3

    def test_get_capabilities_operator(self, registry):
        """Get capabilities for console_operator operator."""
        caps = registry.get_capabilities(Persona.CONSOLE_OPERATOR, Role.OPERATOR)
        assert "read_audit" in caps
        assert "write_config" not in caps
        assert "admin_only" not in caps
        assert len(caps) == 1

    def test_get_capabilities_empty(self, registry):
        """Get capabilities returns empty set for ungranted (persona, role)."""
        caps = registry.get_capabilities(Persona.VOICE_USER, Role.USER)
        assert len(caps) == 0


class TestGetCapabilityDef:
    """Test getting capability definitions."""

    @pytest.fixture
    def registry(self):
        """Create registry with test capability."""
        reg = CapabilityRegistry()
        reg.register_capability(
            "read_audit",
            "Read audit logs",
            tier=Tier.COMPLIANCE,
            requires_mfa=True,
        )
        return reg

    def test_get_capability_def_exists(self, registry):
        """Get existing capability definition."""
        cap = registry.get_capability_def("read_audit")
        assert cap is not None
        assert cap.id == "read_audit"
        assert cap.description == "Read audit logs"
        assert cap.tier == Tier.COMPLIANCE
        assert cap.requires_mfa is True

    def test_get_capability_def_not_exists(self, registry):
        """Get nonexistent capability definition returns None."""
        cap = registry.get_capability_def("nonexistent")
        assert cap is None


class TestListCapabilities:
    """Test listing all capabilities."""

    @pytest.fixture
    def registry(self):
        """Create registry with test capabilities."""
        reg = CapabilityRegistry()
        reg.register_capability("read_audit", "Read audit", tier=Tier.STANDARD)
        reg.register_capability("write_config", "Write config", tier=Tier.STANDARD)
        reg.register_capability("admin_only", "Admin only", tier=Tier.COMPLIANCE)
        return reg

    def test_list_capabilities(self, registry):
        """List all registered capabilities."""
        caps = registry.list_capabilities()
        assert len(caps) == 3
        assert "read_audit" in caps
        assert "write_config" in caps
        assert "admin_only" in caps

    def test_list_capabilities_is_copy(self, registry):
        """list_capabilities returns a copy (not reference)."""
        caps = registry.list_capabilities()
        original_len = len(caps)
        caps["fake_cap"] = Capability("fake", "Fake", Tier.STANDARD)
        # Registry should not be modified
        assert len(registry.list_capabilities()) == original_len
