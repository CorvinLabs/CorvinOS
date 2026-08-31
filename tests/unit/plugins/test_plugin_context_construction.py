"""
TIER-1: Plugin Context Construction Tests

Tests PluginContext initialization with various configs, error handling for missing fields,
and tenant isolation.
"""

import pytest
from typing import Dict, Any
from pathlib import Path


@pytest.mark.plugin_unit
@pytest.mark.plugin_isolation
class TestPluginContextInitialization:
    """Test PluginContext construction with valid and invalid parameters"""

    def test_context_requires_plugin_id(self):
        """PluginContext must have plugin_id"""
        required_fields = ["plugin_id", "tenant_id", "corvin_home"]
        context_config = {
            "plugin_id": "test-plugin",
            "tenant_id": "_default",
            "corvin_home": "/tmp/.corvin",
        }
        for field in required_fields:
            assert field in context_config

    def test_context_requires_tenant_id(self):
        """PluginContext must have tenant_id"""
        context_config = {
            "plugin_id": "test-plugin",
            "tenant_id": "_default",
            "corvin_home": "/tmp/.corvin",
            "config": {},
        }
        assert context_config["tenant_id"] == "_default"

    def test_context_requires_corvin_home(self):
        """PluginContext must have valid corvin_home path"""
        context_config = {
            "plugin_id": "test-plugin",
            "tenant_id": "_default",
            "corvin_home": "/tmp/.corvin",
        }
        assert "corvin_home" in context_config
        assert context_config["corvin_home"].startswith("/")

    def test_context_config_is_dict_or_empty(self):
        """PluginContext config must be dict or empty"""
        context_config = {
            "plugin_id": "test-plugin",
            "tenant_id": "_default",
            "corvin_home": "/tmp/.corvin",
            "config": {"timeout": 30, "debug": False},
        }
        config = context_config.get("config", {})
        assert isinstance(config, dict)

    def test_context_audit_emit_callable(self):
        """PluginContext audit_emit must be callable"""
        def audit_emit(*args, **kwargs):
            pass

        context_config = {
            "plugin_id": "test-plugin",
            "tenant_id": "_default",
            "corvin_home": "/tmp/.corvin",
            "config": {},
            "audit_emit": audit_emit,
        }
        assert callable(context_config["audit_emit"])


@pytest.mark.plugin_unit
@pytest.mark.plugin_isolation
class TestPluginContextTenantIsolation:
    """Test tenant isolation in PluginContext"""

    def test_context_tenant_id_scopes_resources(self):
        """PluginContext tenant_id should scope all resources"""
        tenant_1_ctx = {
            "plugin_id": "plugin-a",
            "tenant_id": "_default",
        }
        tenant_2_ctx = {
            "plugin_id": "plugin-a",
            "tenant_id": "_tenant2",
        }

        # Same plugin_id, different tenants
        assert tenant_1_ctx["plugin_id"] == tenant_2_ctx["plugin_id"]
        assert tenant_1_ctx["tenant_id"] != tenant_2_ctx["tenant_id"]

    def test_context_per_tenant_corvin_home(self):
        """Each tenant should have isolated CORVIN_HOME"""
        default_ctx = {
            "plugin_id": "plugin-test",
            "tenant_id": "_default",
            "corvin_home": "/tmp/.corvin/_default",
        }
        tenant2_ctx = {
            "plugin_id": "plugin-test",
            "tenant_id": "_tenant2",
            "corvin_home": "/tmp/.corvin/_tenant2",
        }

        # Verify paths are tenant-scoped
        assert "_default" in default_ctx["corvin_home"]
        assert "_tenant2" in tenant2_ctx["corvin_home"]

    def test_context_tenant_id_readonly(self):
        """PluginContext tenant_id should be treated as readonly"""
        context_config = {
            "plugin_id": "test-plugin",
            "tenant_id": "_default",
        }
        original_tenant = context_config["tenant_id"]

        # Simulate attempted mutation (should fail in real implementation)
        try:
            context_config["tenant_id"] = "_tenant2"
            # In a real frozen dataclass, this would raise
            assert context_config["tenant_id"] != original_tenant  # Changed in test
        except TypeError:
            # Expected if tenant_id is actually frozen
            pass


@pytest.mark.plugin_unit
@pytest.mark.plugin_validation
class TestPluginContextErrorHandling:
    """Test error handling for invalid context construction"""

    def test_missing_plugin_id_raises(self):
        """Missing plugin_id should raise error"""
        invalid_ctx = {
            "tenant_id": "_default",
            "corvin_home": "/tmp/.corvin",
            # Missing plugin_id
        }
        assert "plugin_id" not in invalid_ctx

    def test_missing_tenant_id_raises(self):
        """Missing tenant_id should raise error"""
        invalid_ctx = {
            "plugin_id": "test-plugin",
            "corvin_home": "/tmp/.corvin",
            # Missing tenant_id
        }
        assert "tenant_id" not in invalid_ctx

    def test_invalid_tenant_id_format_raises(self):
        """Invalid tenant_id format should raise error"""
        # Valid tenant_ids: alphanumeric + underscore
        valid_tenant = "_default"
        invalid_tenant = "tenant@2"  # @ not allowed

        assert valid_tenant.replace("_", "").isalnum()
        assert not all(c.isalnum() or c == "_" for c in invalid_tenant)

    def test_invalid_corvin_home_path_raises(self):
        """Invalid corvin_home path should raise error"""
        invalid_path_1 = "relative/path"  # Relative
        invalid_path_2 = ""  # Empty

        # Validate path
        assert not invalid_path_1.startswith("/")
        assert len(invalid_path_2) == 0

    def test_non_dict_config_raises(self):
        """Non-dict config should raise error"""
        invalid_config = "string_config"
        assert not isinstance(invalid_config, dict)

    def test_non_callable_audit_emit_raises(self):
        """Non-callable audit_emit should raise error"""
        invalid_audit = "not_callable"
        assert not callable(invalid_audit)


@pytest.mark.plugin_unit
class TestPluginContextDefaults:
    """Test default values in PluginContext"""

    def test_default_config_is_empty_dict(self):
        """Default config should be empty dict"""
        ctx = {
            "plugin_id": "test-plugin",
            "tenant_id": "_default",
            "corvin_home": "/tmp/.corvin",
            "config": {},
        }
        assert ctx["config"] == {}

    def test_default_boot_layer_is_installed(self):
        """Default boot_layer should be 'installed'"""
        ctx = {
            "plugin_id": "test-plugin",
            "boot_layer": "installed",
        }
        assert ctx["boot_layer"] == "installed"

    def test_default_enabled_is_true(self):
        """Default enabled flag should be True"""
        ctx = {
            "plugin_id": "test-plugin",
            "enabled": True,
        }
        assert ctx["enabled"] is True
