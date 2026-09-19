"""
Unit tests for ADR-0302: Persona Capability Axis.
"""

import pytest
from core.context_engineering.persona_model import (
    Persona, Role, Tier, Capability, CapabilityRegistry, get_registry, bootstrap_default_capabilities
)


class TestCapabilityRegistry:
    """Test CapabilityRegistry core logic."""
    
    @pytest.fixture
    def registry(self):
        return CapabilityRegistry("test_tenant")
    
    def test_deny_by_default(self, registry):
        """Missing capabilities always return False."""
        result = registry.has_capability(
            Persona.CONSOLE_OPERATOR,
            Role.ADMIN,
            "nonexistent_capability"
        )
        assert result is False
    
    def test_register_capability(self, registry):
        cap = Capability("test", "Test", Tier.STANDARD)
        registry.register_capability(cap)
    
    def test_register_duplicate_raises(self, registry):
        cap = Capability("test", "Test", Tier.STANDARD)
        registry.register_capability(cap)
        with pytest.raises(ValueError, match="already registered"):
            registry.register_capability(cap)
    
    def test_grant_capability(self, registry):
        cap = Capability("test_cap", "Test", Tier.STANDARD)
        registry.register_capability(cap)
        
        assert not registry.has_capability(Persona.CONSOLE_OPERATOR, Role.ADMIN, "test_cap")
        
        registry.grant(Persona.CONSOLE_OPERATOR, Role.ADMIN, "test_cap")
        
        assert registry.has_capability(Persona.CONSOLE_OPERATOR, Role.ADMIN, "test_cap")
    
    def test_mfa_requirement_denied_when_not_verified(self, registry):
        cap = Capability("sensitive", "Sensitive", Tier.COMPLIANCE, requires_mfa=True)
        registry.register_capability(cap)
        registry.grant(Persona.CONSOLE_OPERATOR, Role.ADMIN, "sensitive")
        
        result = registry.has_capability(
            Persona.CONSOLE_OPERATOR,
            Role.ADMIN,
            "sensitive",
            mfa_verified=False
        )
        assert result is False
    
    def test_mfa_requirement_allowed_when_verified(self, registry):
        cap = Capability("sensitive", "Sensitive", Tier.COMPLIANCE, requires_mfa=True)
        registry.register_capability(cap)
        registry.grant(Persona.CONSOLE_OPERATOR, Role.ADMIN, "sensitive")
        
        result = registry.has_capability(
            Persona.CONSOLE_OPERATOR,
            Role.ADMIN,
            "sensitive",
            mfa_verified=True
        )
        assert result is True
    
    def test_get_capabilities(self, registry):
        cap1 = Capability("cap1", "Test 1", Tier.STANDARD)
        cap2 = Capability("cap2", "Test 2", Tier.STANDARD)
        
        registry.register_capability(cap1)
        registry.register_capability(cap2)
        
        registry.grant(Persona.CONSOLE_OPERATOR, Role.ADMIN, "cap1")
        registry.grant(Persona.CONSOLE_OPERATOR, Role.ADMIN, "cap2")
        
        caps = registry.get_capabilities(Persona.CONSOLE_OPERATOR, Role.ADMIN)
        assert "cap1" in caps
        assert "cap2" in caps


class TestBootstrapDefaultCapabilities:
    """Test default capability bootstrap."""
    
    def test_bootstrap_creates_default_capabilities(self):
        from core.context_engineering.persona_model import _registries
        
        test_tenant = "test_bootstrap_tenant_unique"
        if test_tenant in _registries:
            del _registries[test_tenant]
        
        bootstrap_default_capabilities(test_tenant)
        registry = get_registry(test_tenant)
        
        assert registry.has_capability(
            Persona.CONSOLE_OPERATOR,
            Role.ADMIN,
            "read_settings"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
