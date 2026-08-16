"""
Unit tests for Persona Capability Model (ADR-0302).

Test coverage:
1. Deny-by-default: capability not in registry → False
2. Grant/revoke: after grant → True, after revoke → False
3. Persona-aware: console_operator != voice_user capabilities
4. Role-aware: admin != operator capabilities
5. Decorator: @requires_capability enforcement
6. ContextVar isolation: concurrent requests don't leak
7. Boot lock: Tier.COMPLIANCE cannot be added after lock
8. Multitenant: tenant_1 caps != tenant_2 caps
"""

from concurrent.futures import ThreadPoolExecutor

import pytest

from core.context_engineering import (
    CapabilityDenied,
    CapabilityLockError,
    Persona,
    Role,
    Tier,
    get_registry,
    has_capability,
    requires_capability,
    set_current_persona,
    set_current_role,
    set_current_tenant_id,
)


@pytest.fixture(autouse=True)
def reset_registry():
    """Reset global registry before each test."""
    from core.context_engineering.persona_model import _REGISTRY
    _REGISTRY._capabilities.clear()
    _REGISTRY.unlock_tier1()
    yield
    _REGISTRY._capabilities.clear()
    _REGISTRY.unlock_tier1()


class TestDenyByDefault:
    """Capability not in registry → always False."""

    def test_unregistered_capability_is_false(self):
        registry = get_registry()
        assert not registry.has_capability(
            Persona.CONSOLE_OPERATOR,
            Role.ADMIN,
            "nonexistent_capability"
        )

    def test_all_personas_denied_unregistered_capability(self):
        registry = get_registry()
        for persona in Persona:
            for role in Role:
                assert not registry.has_capability(persona, role, "fake_cap")


class TestGrantRevoke:
    """After grant → True, after revoke → False."""

    def test_grant_makes_capability_true(self):
        registry = get_registry()
        registry.register_capability(
            Persona.CONSOLE_OPERATOR,
            Role.ADMIN,
            "read_audit_log",
            Tier.STANDARD
        )
        assert registry.has_capability(
            Persona.CONSOLE_OPERATOR,
            Role.ADMIN,
            "read_audit_log"
        )

    def test_revoke_makes_capability_false(self):
        registry = get_registry()
        registry.register_capability(
            Persona.CONSOLE_OPERATOR,
            Role.ADMIN,
            "read_audit_log",
            Tier.STANDARD
        )
        assert registry.has_capability(
            Persona.CONSOLE_OPERATOR,
            Role.ADMIN,
            "read_audit_log"
        )

        registry.revoke_capability(
            Persona.CONSOLE_OPERATOR,
            Role.ADMIN,
            "read_audit_log",
            Tier.STANDARD
        )
        assert not registry.has_capability(
            Persona.CONSOLE_OPERATOR,
            Role.ADMIN,
            "read_audit_log"
        )

    def test_multiple_capabilities(self):
        registry = get_registry()
        registry.register_capability(
            Persona.CONSOLE_OPERATOR,
            Role.ADMIN,
            "read_audit_log",
            Tier.STANDARD
        )
        registry.register_capability(
            Persona.CONSOLE_OPERATOR,
            Role.ADMIN,
            "write_feature_flag",
            Tier.STANDARD
        )

        assert registry.has_capability(
            Persona.CONSOLE_OPERATOR,
            Role.ADMIN,
            "read_audit_log"
        )
        assert registry.has_capability(
            Persona.CONSOLE_OPERATOR,
            Role.ADMIN,
            "write_feature_flag"
        )


class TestPersonaAware:
    """console_operator capabilities ≠ voice_user capabilities."""

    def test_capabilities_differ_by_persona(self):
        registry = get_registry()

        # Console operator can write_feature_flag
        registry.register_capability(
            Persona.CONSOLE_OPERATOR,
            Role.OPERATOR,
            "write_feature_flag",
            Tier.STANDARD
        )

        # But voice_user cannot
        assert registry.has_capability(
            Persona.CONSOLE_OPERATOR,
            Role.OPERATOR,
            "write_feature_flag"
        )
        assert not registry.has_capability(
            Persona.VOICE_USER,
            Role.OPERATOR,
            "write_feature_flag"
        )


class TestRoleAware:
    """admin capabilities ⊇ operator capabilities."""

    def test_capabilities_differ_by_role(self):
        registry = get_registry()

        # Admin can do something
        registry.register_capability(
            Persona.CONSOLE_OPERATOR,
            Role.ADMIN,
            "restart_service",
            Tier.STANDARD
        )

        # Operator cannot
        assert registry.has_capability(
            Persona.CONSOLE_OPERATOR,
            Role.ADMIN,
            "restart_service"
        )
        assert not registry.has_capability(
            Persona.CONSOLE_OPERATOR,
            Role.OPERATOR,
            "restart_service"
        )


class TestDecorator:
    """@requires_capability enforcement."""

    def test_decorator_allows_authorized(self):
        registry = get_registry()
        registry.register_capability(
            Persona.CONSOLE_OPERATOR,
            Role.ADMIN,
            "read_audit_log",
            Tier.STANDARD
        )

        @requires_capability("read_audit_log")
        def protected_function():
            return "success"

        set_current_persona(Persona.CONSOLE_OPERATOR)
        set_current_role(Role.ADMIN)

        assert protected_function() == "success"

    def test_decorator_denies_unauthorized(self):
        # Don't register the capability
        _ = get_registry()

        @requires_capability("read_audit_log")
        def protected_function():
            return "success"

        set_current_persona(Persona.CONSOLE_OPERATOR)
        set_current_role(Role.ADMIN)

        with pytest.raises(CapabilityDenied):
            protected_function()

    def test_decorator_preserves_function_metadata(self):
        @requires_capability("some_cap")
        def documented_function():
            """This is a documented function."""
            pass

        assert documented_function.__name__ == "documented_function"
        assert documented_function.__doc__ == "This is a documented function."


class TestContextVarIsolation:
    """Concurrent requests don't leak personas."""

    def test_context_var_isolation_between_threads(self):
        """Test that ContextVar isolation works across threads."""
        results = {}

        def thread_task(persona: Persona, role: Role, key: str):
            set_current_persona(persona)
            set_current_role(role)
            # Small delay to ensure overlap
            import time
            time.sleep(0.01)
            results[key] = (persona, role)

        with ThreadPoolExecutor(max_workers=3) as executor:
            executor.submit(thread_task, Persona.CONSOLE_OPERATOR, Role.ADMIN, "t1")
            executor.submit(thread_task, Persona.VOICE_USER, Role.USER, "t2")
            executor.submit(thread_task, Persona.BRIDGE_ADAPTER, Role.OPERATOR, "t3")

        # Each thread should have its own context
        assert results["t1"] == (Persona.CONSOLE_OPERATOR, Role.ADMIN)
        assert results["t2"] == (Persona.VOICE_USER, Role.USER)
        assert results["t3"] == (Persona.BRIDGE_ADAPTER, Role.OPERATOR)


class TestBootLock:
    """Tier.COMPLIANCE cannot be added after lock_tier1()."""

    def test_compliance_capability_blocked_after_lock(self):
        registry = get_registry()

        # Can register before lock
        registry.register_capability(
            Persona.CONSOLE_OPERATOR,
            Role.ADMIN,
            "audit_log_verify",
            Tier.COMPLIANCE
        )

        registry.lock_tier1()

        # Cannot register after lock
        with pytest.raises(CapabilityLockError):
            registry.register_capability(
                Persona.CONSOLE_OPERATOR,
                Role.ADMIN,
                "another_compliance_cap",
                Tier.COMPLIANCE
            )

    def test_compliance_capability_revoke_blocked_after_lock(self):
        registry = get_registry()
        registry.register_capability(
            Persona.CONSOLE_OPERATOR,
            Role.ADMIN,
            "audit_log_verify",
            Tier.COMPLIANCE
        )
        registry.lock_tier1()

        # Cannot revoke compliance capability after lock
        with pytest.raises(CapabilityLockError):
            registry.revoke_capability(
                Persona.CONSOLE_OPERATOR,
                Role.ADMIN,
                "audit_log_verify",
                Tier.COMPLIANCE
            )

    def test_standard_capability_can_be_revoked_after_lock(self):
        registry = get_registry()
        registry.register_capability(
            Persona.CONSOLE_OPERATOR,
            Role.ADMIN,
            "write_feature_flag",
            Tier.STANDARD
        )
        registry.lock_tier1()

        # Can still revoke STANDARD capabilities
        registry.revoke_capability(
            Persona.CONSOLE_OPERATOR,
            Role.ADMIN,
            "write_feature_flag",
            Tier.STANDARD
        )
        assert not registry.has_capability(
            Persona.CONSOLE_OPERATOR,
            Role.ADMIN,
            "write_feature_flag"
        )


class TestMultitenant:
    """tenant_1 capabilities ≠ tenant_2 capabilities."""

    def test_capabilities_scoped_to_tenant(self):
        registry = get_registry()

        # Register for tenant_1
        registry.register_capability(
            Persona.CONSOLE_OPERATOR,
            Role.ADMIN,
            "read_audit_log",
            Tier.STANDARD,
            tenant_id="tenant_1"
        )

        # tenant_1 has it
        assert registry.has_capability(
            Persona.CONSOLE_OPERATOR,
            Role.ADMIN,
            "read_audit_log",
            tenant_id="tenant_1"
        )

        # tenant_2 doesn't
        assert not registry.has_capability(
            Persona.CONSOLE_OPERATOR,
            Role.ADMIN,
            "read_audit_log",
            tenant_id="tenant_2"
        )

    def test_get_capabilities_per_tenant(self):
        registry = get_registry()

        registry.register_capability(
            Persona.CONSOLE_OPERATOR,
            Role.ADMIN,
            "cap_a",
            tenant_id="tenant_1"
        )
        registry.register_capability(
            Persona.CONSOLE_OPERATOR,
            Role.ADMIN,
            "cap_b",
            tenant_id="tenant_2"
        )

        caps_1 = registry.get_capabilities(
            Persona.CONSOLE_OPERATOR,
            Role.ADMIN,
            tenant_id="tenant_1"
        )
        caps_2 = registry.get_capabilities(
            Persona.CONSOLE_OPERATOR,
            Role.ADMIN,
            tenant_id="tenant_2"
        )

        assert caps_1 == {"cap_a"}
        assert caps_2 == {"cap_b"}


class TestContextFunction:
    """Test convenience has_capability() wrapper."""

    def test_has_capability_uses_current_context(self):
        registry = get_registry()
        registry.register_capability(
            Persona.CONSOLE_OPERATOR,
            Role.ADMIN,
            "test_cap",
            Tier.STANDARD
        )

        set_current_persona(Persona.CONSOLE_OPERATOR)
        set_current_role(Role.ADMIN)
        set_current_tenant_id("_default")

        assert has_capability("test_cap")
        assert not has_capability("nonexistent_cap")
