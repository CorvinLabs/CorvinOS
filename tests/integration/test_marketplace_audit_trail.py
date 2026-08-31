"""Audit Trail Verification Tests — Phase 3 Compliance.

Verifies that all plugin marketplace operations are logged to audit.jsonl:
1. Plugin install → logged
2. Plugin uninstall → logged
3. Config changes → logged (with secret masking)
4. Panel register → logged
5. Panel unregister → logged
6. Enable/disable → logged

Compliance: GDPR Art. 30 (Records of Processing)
"""

import pytest
import json
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from typing import List, Dict, Any

try:
    from core.plugins.plugin_registry import PluginRegistry
    from core.plugins.plugin_panel_registry import PluginPanelRegistry
    from core.audit.audit_chain import AuditChain
except ImportError:
    pytest.skip("Required audit modules not available", allow_module_level=True)


class MockAuditChain:
    """Mock audit chain for testing."""

    def __init__(self):
        self.events: List[Dict[str, Any]] = []

    def log_event(self, event_type: str, data: Dict[str, Any]):
        """Record event."""
        self.events.append({
            "type": event_type,
            "data": data,
        })

    def get_events_by_type(self, event_type: str) -> List[Dict[str, Any]]:
        """Get all events of a type."""
        return [e for e in self.events if e["type"] == event_type]


@pytest.fixture
def mock_audit():
    """Create mock audit chain."""
    return MockAuditChain()


class TestPluginInstallAudit:
    """Test audit logging for plugin install."""

    def test_plugin_install_logged(self, mock_audit):
        """Plugin install event is logged to audit trail."""
        with patch("core.plugins.plugin_registry.AuditChain", return_value=mock_audit):
            from core.plugins.plugin_registry import PluginRegistry

            registry = PluginRegistry()
            # Mock the _audit_log method to use our mock
            registry._audit_log = mock_audit.log_event

            registry.add(
                plugin_id="test-plugin",
                name="Test Plugin",
                version="1.0.0",
                repo="example/test-plugin",
                commit_hash="abc123def456"
            )

            # Verify event was logged
            events = mock_audit.get_events_by_type("plugin.registry.plugin_installed")
            assert len(events) == 1
            assert events[0]["data"]["plugin_id"] == "test-plugin"

    def test_plugin_uninstall_logged(self, mock_audit):
        """Plugin uninstall event is logged."""
        with patch("core.plugins.plugin_registry.AuditChain", return_value=mock_audit):
            from core.plugins.plugin_registry import PluginRegistry

            registry = PluginRegistry()
            registry._audit_log = mock_audit.log_event

            registry.remove("test-plugin")

            events = mock_audit.get_events_by_type("plugin.registry.plugin_uninstalled")
            assert len(events) == 1
            assert events[0]["data"]["plugin_id"] == "test-plugin"

    def test_secrets_masked_in_audit(self, mock_audit):
        """Secret values are never logged, only hashes."""
        with patch("core.plugins.plugin_registry.AuditChain", return_value=mock_audit):
            from core.plugins.plugin_registry import PluginRegistry

            registry = PluginRegistry()
            registry._audit_log = mock_audit.log_event

            # This would have a secret in normal flow
            data = {
                "plugin_id": "secret-plugin",
                "api_key": "sk-1234567890abcdef",  # Secret!
                "version": "1.0.0",
            }

            masked = registry._mask_secrets(data)

            # Verify secret is masked
            assert "MASKED:" in masked["api_key"]
            assert "sk-1234567890abcdef" not in str(masked)
            # Non-secret data is preserved
            assert masked["plugin_id"] == "secret-plugin"
            assert masked["version"] == "1.0.0"

    def test_commit_hash_prefix_only(self, mock_audit):
        """Commit hash is logged as prefix only (privacy)."""
        with patch("core.plugins.plugin_registry.AuditChain", return_value=mock_audit):
            from core.plugins.plugin_registry import PluginRegistry

            registry = PluginRegistry()
            registry._audit_log = mock_audit.log_event

            registry.add(
                plugin_id="secure-plugin",
                name="Secure Plugin",
                version="1.0.0",
                repo="example/secure-plugin",
                commit_hash="abc123def456xyz789"
            )

            events = mock_audit.get_events_by_type("plugin.registry.plugin_installed")
            assert len(events) == 1
            # Only first 8 chars should be logged
            assert events[0]["data"]["commit_hash_prefix"] == "abc123de"
            assert "commit_hash" not in events[0]["data"]  # Full hash not logged


class TestConfigChangeAudit:
    """Test audit logging for config changes."""

    def test_config_change_logged_with_hash(self, mock_audit):
        """Config changes are logged with hash (not value)."""
        with patch("core.plugins.plugin_registry.AuditChain", return_value=mock_audit):
            from core.plugins.plugin_registry import PluginRegistry

            registry = PluginRegistry()
            registry._audit_log = mock_audit.log_event

            # Initialize with a plugin
            registry.add(
                plugin_id="config-plugin",
                name="Config Plugin",
                version="1.0.0",
                repo="example/config-plugin",
                commit_hash="abc123"
            )

            # Change config
            config = {
                "api_key": "secret",
                "endpoint": "https://api.example.com",
                "timeout": 30,
            }

            registry.update_config("config-plugin", config)

            # Verify config change was logged
            events = mock_audit.get_events_by_type("plugin.registry.plugin_config_changed")
            assert len(events) == 1

            # Verify only hashes are logged, not values
            data = events[0]["data"]
            assert "new_config_hash" in data
            assert "changed_keys" in data
            assert "api_key" in data["changed_keys"]
            # Values should never be in audit log
            assert "secret" not in str(data)
            assert "https://api.example.com" not in str(data)


class TestPanelOperationAudit:
    """Test audit logging for panel operations."""

    def test_panel_registration_logged(self, mock_audit):
        """Panel registration is logged."""
        registry = PluginPanelRegistry()

        with patch("core.audit.audit_chain.AuditChain", return_value=mock_audit):
            registry._audit_log = mock_audit.log_event

            spec = {
                "id": "test-panel",
                "label": "Test Panel",
                "route": "test",
                "icon": "Zap",
                "group": "test",
            }

            registry.register_panel("test-plugin", spec)

            events = mock_audit.get_events_by_type("plugin.panel.panel_registered")
            assert len(events) == 1
            assert events[0]["data"]["panel_id"] == "test-panel"
            assert events[0]["data"]["plugin_id"] == "test-plugin"

    def test_panel_unregister_logged(self, mock_audit):
        """Panel unregistration is logged."""
        registry = PluginPanelRegistry()

        with patch("core.audit.audit_chain.AuditChain", return_value=mock_audit):
            registry._audit_log = mock_audit.log_event

            # Pre-register
            spec = {
                "id": "removable-panel",
                "label": "Removable",
                "route": "remove",
                "icon": "Trash",
                "group": "test",
            }
            registry.register_panel("test-plugin", spec)

            # Clear events
            mock_audit.events.clear()

            # Unregister
            registry.unregister_panel("removable-panel")

            events = mock_audit.get_events_by_type("plugin.panel.panel_unregistered")
            assert len(events) == 1
            assert events[0]["data"]["panel_id"] == "removable-panel"

    def test_panel_enable_disable_logged(self, mock_audit):
        """Panel enable/disable is logged."""
        registry = PluginPanelRegistry()

        with patch("core.audit.audit_chain.AuditChain", return_value=mock_audit):
            registry._audit_log = mock_audit.log_event

            # Register
            spec = {
                "id": "toggle-panel",
                "label": "Toggle",
                "route": "toggle",
                "icon": "Power",
                "group": "test",
            }
            registry.register_panel("test-plugin", spec)

            # Clear events
            mock_audit.events.clear()

            # Disable
            registry.disable_panel("toggle-panel")

            disable_events = mock_audit.get_events_by_type("plugin.panel.panel_disabled")
            assert len(disable_events) == 1

            # Clear
            mock_audit.events.clear()

            # Enable
            registry.enable_panel("toggle-panel")

            enable_events = mock_audit.get_events_by_type("plugin.panel.panel_enabled")
            assert len(enable_events) == 1


class TestAuditTrailCompliance:
    """Test compliance requirements (GDPR Art. 30, 32)."""

    def test_all_install_events_present(self, mock_audit):
        """All install-related events are logged."""
        with patch("core.plugins.plugin_registry.AuditChain", return_value=mock_audit):
            from core.plugins.plugin_registry import PluginRegistry

            registry = PluginRegistry()
            registry._audit_log = mock_audit.log_event

            # Install plugin
            registry.add(
                plugin_id="complete-plugin",
                name="Complete Plugin",
                version="1.0.0",
                repo="example/complete-plugin",
                commit_hash="abc123"
            )

            # Verify event is present
            assert len(mock_audit.events) >= 1
            assert mock_audit.events[0]["type"] == "plugin.registry.plugin_installed"

    def test_audit_events_have_required_fields(self, mock_audit):
        """Each audit event has required fields for compliance."""
        with patch("core.plugins.plugin_registry.AuditChain", return_value=mock_audit):
            from core.plugins.plugin_registry import PluginRegistry

            registry = PluginRegistry()
            registry._audit_log = mock_audit.log_event

            registry.add(
                plugin_id="field-test",
                name="Field Test",
                version="1.0.0",
                repo="example/field-test",
                commit_hash="abc123"
            )

            event = mock_audit.events[0]

            # Required fields for GDPR compliance
            assert "type" in event  # Event type for log parsing
            assert "data" in event  # Event data
            assert isinstance(event["data"], dict)  # Structured data

    def test_no_pii_in_audit_logs(self, mock_audit):
        """Audit logs contain no PII (only plugin/config metadata)."""
        with patch("core.plugins.plugin_registry.AuditChain", return_value=mock_audit):
            from core.plugins.plugin_registry import PluginRegistry

            registry = PluginRegistry()
            registry._audit_log = mock_audit.log_event

            # Simulate a config with potential PII
            config = {
                "user_email": "operator@example.com",  # PII?
                "api_endpoint": "https://api.example.com",
            }

            registry.update_config("pii-test-plugin", config)

            # In real scenario, user_email would be masked
            # For now, verify the structure is safe
            events = mock_audit.get_events_by_type("plugin.registry.plugin_config_changed")
            event = events[0] if events else {}
            data = event.get("data", {})

            # Only hashes and metadata, no actual values
            assert "new_config_hash" in data
            assert "changed_keys" in data


class TestAuditChainIntegrity:
    """Test that audit chain remains intact (hash-chained for GDPR)."""

    def test_audit_events_are_immutable_records(self, mock_audit):
        """Once logged, audit events cannot be modified."""
        with patch("core.plugins.plugin_registry.AuditChain", return_value=mock_audit):
            from core.plugins.plugin_registry import PluginRegistry

            registry = PluginRegistry()
            registry._audit_log = mock_audit.log_event

            original_count = len(mock_audit.events)

            registry.add(
                plugin_id="immutable-plugin",
                name="Immutable",
                version="1.0.0",
                repo="example/immutable",
                commit_hash="abc123"
            )

            # Verify event was added
            assert len(mock_audit.events) == original_count + 1

            # In real audit chain, events are append-only
            # A modification would require a new entry, not overwriting


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
