"""Integration Tests — Phase 9 Auth Flow (ADR-0302, 0294, 0296)

End-to-end tests of persona/capability flow across multiple transports.
"""

import pytest
from unittest.mock import Mock, patch

from core.context_engineering import (
    Persona,
    Role,
    Tier,
    REGISTRY,
    set_current_persona,
    set_current_role,
    requires_auth_capability,
    TransportResolver,
)


class TestFlaskAuthFlow:
    """Test Flask request auth flow."""

    @pytest.fixture
    def setup_flask_capabilities(self):
        """Setup capabilities for Flask test."""
        REGISTRY.register_capability(
            "flask_read",
            "Flask read capability",
            tier=Tier.STANDARD,
        )
        REGISTRY.register_capability(
            "flask_write",
            "Flask write capability",
            tier=Tier.STANDARD,
        )
        REGISTRY.grant(Persona.CONSOLE_OPERATOR, Role.ADMIN, "flask_read")
        REGISTRY.grant(Persona.CONSOLE_OPERATOR, Role.ADMIN, "flask_write")
        REGISTRY.grant(Persona.CONSOLE_OPERATOR, Role.OPERATOR, "flask_read")

    def test_flask_console_admin_can_read(self, setup_flask_capabilities):
        """Console operator (admin) can read."""

        @requires_auth_capability("flask_read")
        def protected_read():
            return {"data": "secret"}

        set_current_persona(Persona.CONSOLE_OPERATOR)
        set_current_role(Role.ADMIN)

        # Mock Flask request
        request = Mock()
        request.headers = {
            "X-Persona": "console_operator",
            "X-Role": "admin",
        }

        with patch(
            "core.context_engineering.auth_decorators.TransportResolver.resolve_flask_request"
        ) as mock_resolve:
            mock_resolve.return_value = (Persona.CONSOLE_OPERATOR, Role.ADMIN)

            result = protected_read()
            assert result == {"data": "secret"}

    def test_flask_console_admin_can_write(self, setup_flask_capabilities):
        """Console operator (admin) can write."""

        @requires_auth_capability("flask_write")
        def protected_write():
            return {"status": "written"}

        set_current_persona(Persona.CONSOLE_OPERATOR)
        set_current_role(Role.ADMIN)

        request = Mock()
        request.headers = {
            "X-Persona": "console_operator",
            "X-Role": "admin",
        }

        with patch(
            "core.context_engineering.auth_decorators.TransportResolver.resolve_flask_request"
        ) as mock_resolve:
            mock_resolve.return_value = (Persona.CONSOLE_OPERATOR, Role.ADMIN)

            result = protected_write()
            assert result == {"status": "written"}

    def test_flask_console_operator_cannot_write(self, setup_flask_capabilities):
        """Console operator (role=operator) cannot write."""

        @requires_auth_capability("flask_write")
        def protected_write():
            return {"status": "written"}

        set_current_persona(Persona.CONSOLE_OPERATOR)
        set_current_role(Role.OPERATOR)

        # Decorator will use current context since Flask is not in request context
        # So it will raise CapabilityDeniedError
        from core.context_engineering import CapabilityDeniedError

        with pytest.raises(CapabilityDeniedError):
            protected_write()

    def test_flask_voice_user_no_access(self, setup_flask_capabilities):
        """Voice user has no capabilities (denied by default)."""

        @requires_auth_capability("flask_read")
        def protected_read():
            return {"data": "secret"}

        set_current_persona(Persona.VOICE_USER)
        set_current_role(Role.USER)

        # Voice user has no capabilities, so should be denied
        from core.context_engineering import CapabilityDeniedError

        with pytest.raises(CapabilityDeniedError):
            protected_read()


class TestCrossPersonaCapabilities:
    """Test capabilities across different personas."""

    @pytest.fixture
    def setup_cross_persona(self):
        """Setup cross-persona capabilities."""
        REGISTRY.register_capability(
            "message_relay",
            "Relay messages through bridge",
            tier=Tier.STANDARD,
        )
        REGISTRY.register_capability(
            "status_report",
            "Report bridge status",
            tier=Tier.STANDARD,
        )

        # CONSOLE_OPERATOR: admin role can do everything
        REGISTRY.grant(Persona.CONSOLE_OPERATOR, Role.ADMIN, "message_relay")
        REGISTRY.grant(Persona.CONSOLE_OPERATOR, Role.ADMIN, "status_report")

        # BRIDGE_ADAPTER: operator role can relay and report
        REGISTRY.grant(Persona.BRIDGE_ADAPTER, Role.OPERATOR, "message_relay")
        REGISTRY.grant(Persona.BRIDGE_ADAPTER, Role.OPERATOR, "status_report")

        # VOICE_USER: only can submit feedback
        # (no capabilities by default — deny-by-default)

    def test_console_operator_admin_can_relay(self, setup_cross_persona):
        """Console operator (admin) can relay messages."""

        @requires_auth_capability("message_relay")
        def relay_message():
            return {"relayed": True}

        set_current_persona(Persona.CONSOLE_OPERATOR)
        set_current_role(Role.ADMIN)

        result = relay_message()
        assert result == {"relayed": True}

    def test_bridge_adapter_operator_can_relay(self, setup_cross_persona):
        """Bridge adapter (operator) can relay messages."""

        @requires_auth_capability("message_relay")
        def relay_message():
            return {"relayed": True}

        set_current_persona(Persona.BRIDGE_ADAPTER)
        set_current_role(Role.OPERATOR)

        result = relay_message()
        assert result == {"relayed": True}

    def test_voice_user_cannot_relay(self, setup_cross_persona):
        """Voice user cannot relay messages (denied by default)."""
        from core.context_engineering import CapabilityDeniedError

        @requires_auth_capability("message_relay")
        def relay_message():
            return {"relayed": True}

        set_current_persona(Persona.VOICE_USER)
        set_current_role(Role.USER)

        with pytest.raises(CapabilityDeniedError):
            relay_message()


class TestMultipleCapabilityChecks:
    """Test multiple capability checks in sequence."""

    @pytest.fixture
    def setup_multiple_caps(self):
        """Setup multiple capabilities."""
        for cap in ["read", "write", "delete", "audit"]:
            REGISTRY.register_capability(
                f"test_{cap}",
                f"Test {cap}",
                tier=Tier.STANDARD,
            )

        # Admin: all capabilities
        for cap in ["read", "write", "delete", "audit"]:
            REGISTRY.grant(Persona.CONSOLE_OPERATOR, Role.ADMIN, f"test_{cap}")

        # Operator: read, write, audit
        for cap in ["read", "write", "audit"]:
            REGISTRY.grant(Persona.CONSOLE_OPERATOR, Role.OPERATOR, f"test_{cap}")

        # User: read only
        REGISTRY.grant(Persona.CONSOLE_OPERATOR, Role.USER, "test_read")

    def test_admin_passes_all_checks(self, setup_multiple_caps):
        """Admin passes all capability checks."""

        @requires_auth_capability("test_read")
        def read():
            return "read"

        @requires_auth_capability("test_write")
        def write():
            return "write"

        @requires_auth_capability("test_delete")
        def delete():
            return "delete"

        set_current_persona(Persona.CONSOLE_OPERATOR)
        set_current_role(Role.ADMIN)

        assert read() == "read"
        assert write() == "write"
        assert delete() == "delete"

    def test_operator_fails_delete_check(self, setup_multiple_caps):
        """Operator fails delete capability check."""
        from core.context_engineering import CapabilityDeniedError

        @requires_auth_capability("test_delete")
        def delete():
            return "delete"

        set_current_persona(Persona.CONSOLE_OPERATOR)
        set_current_role(Role.OPERATOR)

        with pytest.raises(CapabilityDeniedError):
            delete()

    def test_user_only_passes_read(self, setup_multiple_caps):
        """User only passes read check."""
        from core.context_engineering import CapabilityDeniedError

        @requires_auth_capability("test_read")
        def read():
            return "read"

        @requires_auth_capability("test_write")
        def write():
            return "write"

        set_current_persona(Persona.CONSOLE_OPERATOR)
        set_current_role(Role.USER)

        assert read() == "read"

        with pytest.raises(CapabilityDeniedError):
            write()


class TestTransportResolverIntegration:
    """Test transport resolver with different inputs."""

    def test_resolve_all_persona_types(self):
        """Resolver handles all persona types."""
        personas = [
            Persona.CONSOLE_OPERATOR,
            Persona.VOICE_USER,
            Persona.BRIDGE_ADAPTER,
            Persona.MCP_TOOL,
        ]

        for persona in personas:
            request = Mock()
            request.headers = {
                "X-Persona": persona.value,
                "X-Role": "user",
            }

            resolved, _ = TransportResolver.resolve_flask_request(request)
            assert resolved == persona

    def test_resolve_all_role_types(self):
        """Resolver handles all role types."""
        roles = [Role.ADMIN, Role.OPERATOR, Role.USER]

        for role in roles:
            request = Mock()
            request.headers = {
                "X-Persona": "console_operator",
                "X-Role": role.value,
            }

            _, resolved = TransportResolver.resolve_flask_request(request)
            assert resolved == role


class TestBootstrapCapabilityLocking:
    """Test boot-time capability locking."""

    @pytest.fixture
    def fresh_registry(self):
        """Create fresh registry for each test."""
        return __import__("core.context_engineering", fromlist=["CapabilityRegistry"]).CapabilityRegistry()

    def test_can_register_compliance_before_lock(self, fresh_registry):
        """Can register COMPLIANCE tier before lock."""
        cap = fresh_registry.register_capability(
            "critical",
            "Critical",
            tier=Tier.COMPLIANCE,
        )
        assert cap.tier == Tier.COMPLIANCE

    def test_cannot_register_compliance_after_lock(self, fresh_registry):
        """Cannot register COMPLIANCE tier after lock."""
        from core.context_engineering import CapabilityLockError

        fresh_registry.lock_tier1()

        with pytest.raises(CapabilityLockError):
            fresh_registry.register_capability(
                "critical",
                "Critical",
                tier=Tier.COMPLIANCE,
            )

    def test_can_register_standard_after_lock(self, fresh_registry):
        """Can still register STANDARD tier after lock."""
        fresh_registry.lock_tier1()

        cap = fresh_registry.register_capability(
            "normal",
            "Normal",
            tier=Tier.STANDARD,
        )
        assert cap.tier == Tier.STANDARD


class TestDenyByDefaultSemantic:
    """Test deny-by-default semantics."""

    def test_no_grant_is_denied(self):
        """Capability with no explicit grant is denied."""
        set_current_persona(Persona.VOICE_USER)
        set_current_role(Role.USER)

        result = REGISTRY.has_capability(
            Persona.VOICE_USER, Role.USER, "nonexistent_capability"
        )
        assert result is False

    def test_empty_registry_denies_all(self):
        """Empty registry denies all capabilities."""
        from core.context_engineering import CapabilityRegistry

        registry = CapabilityRegistry()

        # Even if capability is defined, not granted = denied
        registry.register_capability("test", "Test", tier=Tier.STANDARD)

        result = registry.has_capability(
            Persona.CONSOLE_OPERATOR, Role.ADMIN, "test"
        )
        assert result is False

    def test_grant_enables_only_grantee(self):
        """Grant enables only for specific (persona, role) pair."""
        from core.context_engineering import CapabilityRegistry

        registry = CapabilityRegistry()
        registry.register_capability("test", "Test", tier=Tier.STANDARD)

        # Grant only to CONSOLE_OPERATOR / ADMIN
        registry.grant(Persona.CONSOLE_OPERATOR, Role.ADMIN, "test")

        # Should pass for CONSOLE_OPERATOR / ADMIN
        assert registry.has_capability(Persona.CONSOLE_OPERATOR, Role.ADMIN, "test")

        # Should fail for CONSOLE_OPERATOR / OPERATOR
        assert not registry.has_capability(
            Persona.CONSOLE_OPERATOR, Role.OPERATOR, "test"
        )

        # Should fail for VOICE_USER / USER
        assert not registry.has_capability(Persona.VOICE_USER, Role.USER, "test")
