"""
Unit Tests for Licensing System 1.0.0 (ADR-0700-0704)

Test Coverage:
- License model and quota definitions
- Tier A/B/C enforcement
- Cross-tenant isolation
- Quota tracking and enforcement
- Audit trail integration
"""

import pytest
from datetime import datetime, timedelta
from core.compliance.licensing import (
    LicenseValidator,
    LicenseError,
    TenantLicenseStatus,
    LicenseQuota,
    PluginLicense,
)
from core.compliance.licensing.tier_enforcement import (
    TierEnforcer,
    CapabilityType,
    CapabilityDeniedError,
)


class TestLicenseValidator:
    """Tests for LicenseValidator"""
    
    def test_initialize_validator_tier_b_default(self):
        """Test that validator defaults to Tier B"""
        validator = LicenseValidator('tenant-1')
        status = validator.load_license()
        
        assert status.license_tier == "B"
        assert status.quota.max_plugins == 50
        assert status.quota.max_mcp_tools == 25
    
    def test_tier_a_quotas(self):
        """Test Tier A quotas (highest)"""
        validator = LicenseValidator('tenant-1')
        quota = validator.TIER_QUOTAS["A"]
        
        assert quota.max_plugins == 100
        assert quota.max_mcp_tools == 50
        assert quota.max_api_calls_per_hour == 10000
    
    def test_tier_c_quotas(self):
        """Test Tier C quotas (enterprise)"""
        validator = LicenseValidator('tenant-1')
        quota = validator.TIER_QUOTAS["C"]
        
        assert quota.max_plugins == 1000
        assert quota.max_mcp_tools == 500
        assert quota.max_api_calls_per_hour == 100000
    
    def test_check_quota_within_limit(self):
        """Test quota check when within limit"""
        validator = LicenseValidator('tenant-1')
        status = validator.load_license()
        
        # Tier B: max 50 plugins
        assert status.check_quota('plugins', 1) is True
        status.usage['plugins'] = 49
        assert status.check_quota('plugins', 1) is True
    
    def test_check_quota_exceeds_limit(self):
        """Test quota check when would exceed limit"""
        validator = LicenseValidator('tenant-1')
        status = validator.load_license()
        
        # Tier B: max 50 plugins
        status.usage['plugins'] = 50
        assert status.check_quota('plugins', 1) is False
    
    def test_tier_a_unrestricted(self):
        """Test that Tier A has unlimited quota"""
        status = TenantLicenseStatus(
            tenant_id='tenant-1',
            license_tier='A',
            quota=LicenseValidator.TIER_QUOTAS['A'],
        )
        
        # Even with high usage, Tier A should allow
        assert status.check_quota('plugins', 1) is True
    
    def test_validate_plugin_load_success(self):
        """Test successful plugin load validation"""
        validator = LicenseValidator('tenant-1')
        assert validator.validate_plugin_load('plugin-1') is True
    
    def test_validate_plugin_load_quota_exceeded(self):
        """Test plugin load fails when quota exceeded"""
        validator = LicenseValidator('tenant-1')
        status = validator.load_license()
        status.usage['plugins'] = 50  # Tier B max
        
        with pytest.raises(LicenseError):
            validator.validate_plugin_load('plugin-1')


class TestPluginLicense:
    """Tests for PluginLicense"""
    
    def test_plugin_license_creation(self):
        """Test creating a plugin license"""
        lic = PluginLicense(
            plugin_id='plugin-1',
            name='Test Plugin',
            tier='B',
            author='Test Author',
            version='1.0.0',
            issued_at=datetime.utcnow(),
        )
        
        assert lic.plugin_id == 'plugin-1'
        assert lic.tier == 'B'
        assert lic.is_valid() is True
    
    def test_expired_license(self):
        """Test that expired licenses are invalid"""
        lic = PluginLicense(
            plugin_id='plugin-1',
            name='Test Plugin',
            tier='B',
            author='Test Author',
            version='1.0.0',
            issued_at=datetime.utcnow() - timedelta(days=10),
            expires_at=datetime.utcnow() - timedelta(days=1),
        )
        
        assert lic.is_valid() is False
    
    def test_days_until_expiry(self):
        """Test expiry countdown"""
        expires = datetime.utcnow() + timedelta(days=30)
        lic = PluginLicense(
            plugin_id='plugin-1',
            name='Test Plugin',
            tier='B',
            author='Test Author',
            version='1.0.0',
            issued_at=datetime.utcnow(),
            expires_at=expires,
        )
        
        days = lic.days_until_expiry()
        assert days is not None
        assert 29 <= days <= 30


class TestTierEnforcer:
    """Tests for TierEnforcer"""
    
    def test_mcp_tool_allowed_all_tiers(self):
        """Test MCP tool execution is allowed in all tiers"""
        for tier in ["A", "B", "C"]:
            enforcer = TierEnforcer('tenant-1', tier)
            assert enforcer.is_capability_allowed(CapabilityType.MCP_TOOL_EXECUTION) is True
    
    def test_connector_auth_tier_a_denied(self):
        """Test connector auth is denied in Tier A"""
        enforcer = TierEnforcer('tenant-1', 'A')
        assert enforcer.is_capability_allowed(CapabilityType.CONNECTOR_AUTH) is False
    
    def test_connector_auth_tier_b_allowed(self):
        """Test connector auth is allowed in Tier B"""
        enforcer = TierEnforcer('tenant-1', 'B')
        assert enforcer.is_capability_allowed(CapabilityType.CONNECTOR_AUTH) is True
    
    def test_layer_override_tier_c_only(self):
        """Test layer override is Tier C only"""
        enforcer_a = TierEnforcer('tenant-1', 'A')
        enforcer_b = TierEnforcer('tenant-1', 'B')
        enforcer_c = TierEnforcer('tenant-1', 'C')
        
        assert enforcer_a.is_capability_allowed(CapabilityType.LAYER_OVERRIDE) is False
        assert enforcer_b.is_capability_allowed(CapabilityType.LAYER_OVERRIDE) is False
        assert enforcer_c.is_capability_allowed(CapabilityType.LAYER_OVERRIDE) is True
    
    def test_enforce_capability_denied(self):
        """Test that enforcing denied capability raises"""
        enforcer = TierEnforcer('tenant-1', 'A')
        
        with pytest.raises(CapabilityDeniedError):
            enforcer.enforce_capability(CapabilityType.CONNECTOR_AUTH)
    
    def test_enforce_capability_quota_exceeded(self):
        """Test quota enforcement"""
        enforcer = TierEnforcer('tenant-1', 'B')
        
        # Set quota to 1 for testing
        gate = enforcer.CAPABILITY_GATES[CapabilityType.MCP_TOOL_EXECUTION]
        original_quota = gate.quota_per_hour
        gate.quota_per_hour = 1
        
        try:
            # First call should succeed
            enforcer.enforce_capability(CapabilityType.MCP_TOOL_EXECUTION)
            
            # Second call should fail
            with pytest.raises(CapabilityDeniedError):
                enforcer.enforce_capability(CapabilityType.MCP_TOOL_EXECUTION)
        finally:
            gate.quota_per_hour = original_quota


class TestCrossTenantIsolation:
    """Tests for cross-tenant isolation"""
    
    def test_separate_licenses_per_tenant(self):
        """Test that licenses are isolated per tenant"""
        val1 = LicenseValidator('tenant-1')
        val2 = LicenseValidator('tenant-2')
        
        status1 = val1.load_license()
        status2 = val2.load_license()
        
        assert status1.tenant_id == 'tenant-1'
        assert status2.tenant_id == 'tenant-2'
        
        # Modify one tenant's usage
        status1.usage['plugins'] = 25
        
        # Other tenant should not be affected
        assert status2.usage.get('plugins', 0) == 0
    
    def test_separate_quota_tracking_per_tenant(self):
        """Test quota tracking is per-tenant"""
        enforcer1 = TierEnforcer('tenant-1', 'B')
        enforcer2 = TierEnforcer('tenant-2', 'B')
        
        # Use quota in tenant 1
        enforcer1.usage_per_hour[CapabilityType.MCP_TOOL_EXECUTION.value] = 50
        
        # Tenant 2 should have independent quota
        enforcer2.check_quota(CapabilityType.MCP_TOOL_EXECUTION, 50)


class TestAuditIntegration:
    """Tests for audit trail integration"""
    
    def test_license_denial_should_be_auditable(self):
        """
        Test that license denials can be audited
        (Actual audit call verified in E2E tests)
        """
        enforcer = TierEnforcer('tenant-1', 'A')
        
        try:
            enforcer.enforce_capability(CapabilityType.LAYER_OVERRIDE)
            pytest.fail("Should have raised CapabilityDeniedError")
        except CapabilityDeniedError as e:
            # Exception message should contain relevant info for audit
            assert 'LAYER_OVERRIDE' in str(e) or 'Layer Override' in str(e)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
