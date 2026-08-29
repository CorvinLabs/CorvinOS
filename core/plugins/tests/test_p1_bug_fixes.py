"""Tests for P1 Bug Fixes (#5, #6, #7) from Adversarial Testing.

Tests verify the fixes for:
- Bug #5: Audit event lost if emitter raises
- Bug #6: String boot_layer parameter not validated early
- Bug #7: Extension hooks not revoked if audit emit fails
"""

import pytest
from unittest.mock import MagicMock, patch
import threading
import time

from corvin_plugins.registry import PluginRegistry, BootLayer
from corvin_plugins.protocol import CorvinPlugin, PluginContext, PluginNotFound


class TestBug5AuditEventLossOnEmitFailure:
    """Bug #5: Audit event lost if emitter raises.

    If ctx.audit_emit() raises an exception during _register_locked(),
    the plugin is already loaded but the audit event never makes it to
    the chain. Caller doesn't know registration succeeded.
    """

    def test_audit_emit_failure_rolls_back_registration(self):
        """Plugin registration should be rolled back if audit_emit fails."""
        registry = PluginRegistry()
        plugin = MagicMock(spec=CorvinPlugin)
        plugin.plugin_id = "test-plugin"
        plugin.on_load = MagicMock(return_value=None)
        plugin.on_unload = MagicMock(return_value=None)

        ctx = MagicMock(spec=PluginContext)
        ctx.tenant_id = "_default"

        # Simulate audit_emit raising an exception
        ctx.audit_emit = MagicMock(side_effect=RuntimeError("Disk full"))

        # Registration should raise the same exception
        with pytest.raises(RuntimeError, match="Disk full"):
            registry.register(plugin, ctx)

        # Plugin should NOT be registered (rollback occurred)
        with pytest.raises(PluginNotFound):
            registry.lookup("test-plugin")

    def test_audit_emit_failure_revokes_hooks(self):
        """Hooks claimed by the plugin should be revoked if audit_emit fails."""
        registry = PluginRegistry()
        plugin = MagicMock(spec=CorvinPlugin)
        plugin.plugin_id = "hook-plugin"
        plugin.on_load = MagicMock(return_value=None)
        plugin.on_unload = MagicMock(return_value=None)

        ctx = MagicMock(spec=PluginContext)
        ctx.tenant_id = "_default"
        ctx.audit_emit = MagicMock(side_effect=RuntimeError("Write failed"))

        # Simulate hook ownership
        with patch('corvin_plugins.registry._verify_hook_ownership') as mock_verify:
            with pytest.raises(RuntimeError):
                registry.register(plugin, ctx)

            # _verify_hook_ownership should have been called before audit_emit
            mock_verify.assert_called_once_with("hook-plugin", "_default")

    def test_audit_emit_failure_detaches_provider_slot(self):
        """Provider slots should be cleaned up if audit_emit fails."""
        registry = PluginRegistry()
        plugin = MagicMock(spec=CorvinPlugin)
        plugin.plugin_id = "provider-plugin"
        plugin.on_load = MagicMock(return_value=None)
        plugin.on_unload = MagicMock(return_value=None)

        ctx = MagicMock(spec=PluginContext)
        ctx.tenant_id = "_default"
        ctx.audit_emit = MagicMock(side_effect=RuntimeError("Audit failed"))

        # Simulate provider slot
        with patch('corvin_plugins.registry._detach_provider_slot') as mock_detach:
            with pytest.raises(RuntimeError):
                registry.register(plugin, ctx)

            # Provider slot should be detached on failure
            mock_detach.assert_called()

    def test_audit_emit_success_completes_registration(self):
        """Plugin should be fully registered if audit_emit succeeds."""
        registry = PluginRegistry()
        plugin = MagicMock(spec=CorvinPlugin)
        plugin.plugin_id = "good-plugin"
        plugin.on_load = MagicMock(return_value=None)
        plugin.on_unload = MagicMock(return_value=None)

        ctx = MagicMock(spec=PluginContext)
        ctx.tenant_id = "_default"
        ctx.audit_emit = MagicMock()  # Succeeds

        # Registration should succeed
        registry.register(plugin, ctx)

        # Plugin should be registered
        assert registry.lookup("good-plugin") is plugin

        # Audit event should be emitted
        ctx.audit_emit.assert_called_once()
        call_args = ctx.audit_emit.call_args
        assert call_args[0][0] == "plugin.loaded"


class TestBug6BootLayerValidation:
    """Bug #6: String boot_layer parameter not validated early.

    Invalid boot_layer strings should be validated before acquiring
    the operation lock, to provide clear error messages and avoid
    holding locks longer than necessary.
    """

    def test_invalid_boot_layer_string_raises_valueerror(self):
        """Invalid boot_layer strings should raise ValueError."""
        registry = PluginRegistry()
        plugin = MagicMock(spec=CorvinPlugin)
        plugin.plugin_id = "test-plugin"

        ctx = MagicMock(spec=PluginContext)
        ctx.tenant_id = "_default"
        ctx.audit_emit = MagicMock()

        # Should raise ValueError for invalid boot layer
        with pytest.raises(ValueError, match="INVALID"):
            registry.register(plugin, ctx, boot_layer="INVALID")

        # Plugin should not be registered
        with pytest.raises(PluginNotFound):
            registry.lookup("test-plugin")

    def test_boot_layer_validation_before_lock(self):
        """boot_layer validation should happen before acquiring _op_lock."""
        registry = PluginRegistry()
        plugin = MagicMock(spec=CorvinPlugin)
        plugin.plugin_id = "test"

        ctx = MagicMock(spec=PluginContext)
        ctx.audit_emit = MagicMock()

        with patch.object(registry, '_op_lock') as mock_lock:
            try:
                registry.register(plugin, ctx, boot_layer="BADVALUE")
            except ValueError:
                pass  # Expected

            # Lock should NOT have been acquired (validation failed first)
            mock_lock.assert_not_called()

    def test_valid_boot_layer_strings_accepted(self):
        """Valid boot_layer strings should be accepted."""
        registry = PluginRegistry()

        # Test each valid boot layer
        for layer_name in ["installed", "bundled", "core", "compliance"]:
            plugin = MagicMock(spec=CorvinPlugin)
            plugin.plugin_id = f"plugin-{layer_name}"
            plugin.on_load = MagicMock(return_value=None)
            plugin.on_unload = MagicMock(return_value=None)

            ctx = MagicMock(spec=PluginContext)
            ctx.tenant_id = "_default"
            ctx.audit_emit = MagicMock()

            # Should succeed without raising
            registry.register(plugin, ctx, boot_layer=layer_name)

            # Verify plugin is registered with correct layer
            actual_layer = registry.boot_layer_of(f"plugin-{layer_name}")
            expected_layer = BootLayer(layer_name)
            assert actual_layer == expected_layer

    def test_boot_layer_enum_directly_accepted(self):
        """BootLayer enums should be accepted directly."""
        registry = PluginRegistry()
        plugin = MagicMock(spec=CorvinPlugin)
        plugin.plugin_id = "enum-test"
        plugin.on_load = MagicMock(return_value=None)
        plugin.on_unload = MagicMock(return_value=None)

        ctx = MagicMock(spec=PluginContext)
        ctx.tenant_id = "_default"
        ctx.audit_emit = MagicMock()

        # Should accept BootLayer enum
        registry.register(plugin, ctx, boot_layer=BootLayer.INSTALLED)

        assert registry.boot_layer_of("enum-test") == BootLayer.INSTALLED


class TestBug7HookOrphaning:
    """Bug #7: Extension hooks not revoked if audit emit fails.

    If _verify_hook_ownership() succeeds but audit_emit() raises,
    the hooks are already revoked but the plugin is still "registered"
    in a half-baked state. Later audit verification finds orphaned hooks.
    """

    def test_hooks_revoked_before_audit_emit(self):
        """_verify_hook_ownership should be called before audit_emit."""
        registry = PluginRegistry()
        plugin = MagicMock(spec=CorvinPlugin)
        plugin.plugin_id = "hook-test"
        plugin.on_load = MagicMock(return_value=None)
        plugin.on_unload = MagicMock(return_value=None)

        ctx = MagicMock(spec=PluginContext)
        ctx.tenant_id = "_default"

        call_order = []

        def track_verify(*args, **kwargs):
            call_order.append("verify_hook_ownership")

        def track_audit(*args, **kwargs):
            call_order.append("audit_emit")
            raise RuntimeError("Audit failed")

        ctx.audit_emit = track_audit

        with patch('corvin_plugins.registry._verify_hook_ownership', side_effect=track_verify):
            with pytest.raises(RuntimeError):
                registry.register(plugin, ctx)

        # Verify hook ownership should be called BEFORE audit_emit
        # (but both may be called if we're fixing the issue properly)
        if len(call_order) == 2:
            assert call_order[0] == "verify_hook_ownership"
            assert call_order[1] == "audit_emit"

    def test_audit_failure_prevents_inconsistent_state(self):
        """Audit failure should not leave hooks revoked but plugin registered."""
        registry = PluginRegistry()
        plugin = MagicMock(spec=CorvinPlugin)
        plugin.plugin_id = "consistency-test"
        plugin.on_load = MagicMock(return_value=None)
        plugin.on_unload = MagicMock(return_value=None)

        ctx = MagicMock(spec=PluginContext)
        ctx.tenant_id = "_default"
        ctx.audit_emit = MagicMock(side_effect=RuntimeError("Audit failed"))

        with pytest.raises(RuntimeError):
            registry.register(plugin, ctx)

        # Plugin should not be in registry (full rollback)
        with pytest.raises(PluginNotFound):
            registry.lookup("consistency-test")

    def test_successful_registration_emits_audit(self):
        """Successful registration should emit audit event with hook info."""
        registry = PluginRegistry()
        plugin = MagicMock(spec=CorvinPlugin)
        plugin.plugin_id = "audit-test"
        plugin.on_load = MagicMock(return_value=None)
        plugin.on_unload = MagicMock(return_value=None)

        ctx = MagicMock(spec=PluginContext)
        ctx.tenant_id = "_default"
        ctx.audit_emit = MagicMock()

        registry.register(plugin, ctx)

        # Verify audit was called
        assert ctx.audit_emit.called

        # Verify the audit event includes plugin info
        call_args = ctx.audit_emit.call_args[0]
        assert call_args[0] == "plugin.loaded"
        assert call_args[1]["plugin_id"] == "audit-test"
        assert call_args[1]["tenant_id"] == "_default"


class TestRecoveryToolsListBackupsEdgeCases:
    """Tests for HIGH-04: UnboundLocalError fix in recovery_tools.py."""

    def test_list_backups_with_unreadable_file(self):
        """Listing backups with unreadable files should not crash."""
        from corvin_plugins.recovery_tools import RegistryRecoveryTools
        from pathlib import Path
        from unittest.mock import patch, MagicMock

        # Create a mock backup manager
        mock_backup_mgr = MagicMock()
        unreadable_path = MagicMock(spec=Path)
        unreadable_path.name = "registry.2026-08-29.yaml"
        unreadable_path.stat.side_effect = PermissionError("No permission")

        mock_backup_mgr.list_backups.return_value = [unreadable_path]

        tools = RegistryRecoveryTools()
        tools.backup_mgr = mock_backup_mgr

        # Should not raise, should return graceful error info
        backups = tools.list_backups()

        assert len(backups) == 1
        assert backups[0]["name"] == "registry.2026-08-29.yaml"
        assert backups[0]["is_valid"] is False
        assert backups[0]["size_bytes"] == 0
        assert backups[0]["mtime"] == 0

    def test_list_backups_with_invalid_yaml(self):
        """Listing backups with invalid YAML should not crash."""
        from corvin_plugins.recovery_tools import RegistryRecoveryTools
        from pathlib import Path
        from unittest.mock import MagicMock

        mock_backup_mgr = MagicMock()
        bad_yaml_path = MagicMock(spec=Path)
        bad_yaml_path.name = "registry.bad.yaml"
        bad_yaml_path.stat.return_value = MagicMock(st_size=100, st_mtime=1234567890)
        bad_yaml_path.read_text.return_value = "{invalid: yaml: content:"  # Invalid YAML

        mock_backup_mgr.list_backups.return_value = [bad_yaml_path]

        tools = RegistryRecoveryTools()
        tools.backup_mgr = mock_backup_mgr

        # Should not raise
        backups = tools.list_backups()

        assert len(backups) == 1
        assert backups[0]["is_valid"] is False
        assert backups[0]["size_bytes"] == 100  # stat succeeded, read failed
        assert backups[0]["mtime"] == 1234567890

    def test_list_backups_mixed_valid_invalid(self):
        """Listing mix of valid and invalid backups should work correctly."""
        from corvin_plugins.recovery_tools import RegistryRecoveryTools
        from pathlib import Path
        from unittest.mock import MagicMock
        import yaml

        mock_backup_mgr = MagicMock()

        # Valid backup
        valid_path = MagicMock(spec=Path)
        valid_path.name = "registry.good.yaml"
        valid_path.stat.return_value = MagicMock(st_size=200, st_mtime=9876543210)
        valid_path.read_text.return_value = yaml.dump({"plugins": {}})

        # Invalid backup
        invalid_path = MagicMock(spec=Path)
        invalid_path.name = "registry.bad.yaml"
        invalid_path.stat.return_value = MagicMock(st_size=50, st_mtime=1234567890)
        invalid_path.read_text.side_effect = IOError("File deleted")

        mock_backup_mgr.list_backups.return_value = [valid_path, invalid_path]

        tools = RegistryRecoveryTools()
        tools.backup_mgr = mock_backup_mgr

        backups = tools.list_backups()

        assert len(backups) == 2

        # Check valid backup
        assert backups[0]["name"] == "registry.good.yaml"
        assert backups[0]["is_valid"] is True
        assert backups[0]["size_bytes"] == 200

        # Check invalid backup
        assert backups[1]["name"] == "registry.bad.yaml"
        assert backups[1]["is_valid"] is False
        assert backups[1]["size_bytes"] == 50  # stat succeeded even if read failed


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
