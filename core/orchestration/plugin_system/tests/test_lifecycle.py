"""Test suite for Plugin Lifecycle Manager (ADR-0XXX k=3)."""

import tempfile
from pathlib import Path

import pytest

from core.orchestration.plugin_system.managers.lifecycle_manager import PluginLifecycleManager
from core.orchestration.plugin_system.models import (
    AuditEvent,
    Plugin,
    PluginAlreadyExists,
    PluginNotFound,
    PluginRegistry,
    PluginTier,
)


class TestPluginLifecycleManager:
    """Tests for plugin lifecycle (Install/Enable/Disable/Uninstall)."""

    @pytest.fixture
    def setup(self):
        """Setup for each test."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            registry_path = tmppath / "registry.yaml"
            state_path = tmppath / "state"

            registry = PluginRegistry(path=registry_path)

            # Audit event sink
            audit_events = []
            def audit_emit(event: AuditEvent):
                audit_events.append(event)

            manager = PluginLifecycleManager(
                registry=registry,
                audit_emit=audit_emit,
                base_state_path=state_path
            )

            yield {
                "manager": manager,
                "registry": registry,
                "audit_events": audit_events,
                "state_path": state_path
            }

    def test_install_plugin(self, setup):
        """Test installing a plugin."""
        manager = setup["manager"]
        registry = setup["registry"]
        audit_events = setup["audit_events"]

        plugin = Plugin(
            id="ai-review",
            version="2.0.1",
            name="AI Code Review",
            tier=PluginTier.B
        )

        # Install
        installed = manager.install(plugin, user_id="user@example.com")

        # Verify metadata
        assert installed.installed_at is not None
        assert installed.installed_by == "user@example.com"
        assert installed.id in registry.plugins

        # Verify persistence
        assert registry.path.exists()

        # Verify audit event
        assert len(audit_events) == 1
        assert audit_events[0].event_type == "plugin_installed"
        assert audit_events[0].plugin_id == "ai-review/2.0.1"

    def test_install_duplicate_fails(self, setup):
        """Test that installing duplicate plugin fails."""
        manager = setup["manager"]

        plugin1 = Plugin(id="test", version="1.0.0", name="Test")
        plugin2 = Plugin(id="test", version="2.0.0", name="Test v2")

        manager.install(plugin1)

        with pytest.raises(PluginAlreadyExists):
            manager.install(plugin2)

    def test_enable_plugin(self, setup):
        """Test enabling a plugin."""
        manager = setup["manager"]
        audit_events = setup["audit_events"]

        plugin = Plugin(id="test", version="1.0.0", name="Test", enabled=False)
        manager.install(plugin)

        # Enable
        enabled = manager.enable("test", user_id="user@example.com")

        # Verify state
        assert enabled.enabled is True
        assert enabled.enabled_at is not None

        # Verify audit
        events_enabled = [e for e in audit_events if e.event_type == "plugin_enabled"]
        assert len(events_enabled) == 1

    def test_config_change(self, setup):
        """Test changing plugin configuration."""
        manager = setup["manager"]
        audit_events = setup["audit_events"]

        plugin = Plugin(
            id="test",
            version="1.0.0",
            name="Test",
            settings_schema={
                "type": "object",
                "properties": {"model": {"type": "string"}}
            },
            settings={"model": "haiku"}
        )
        manager.install(plugin)

        # Change config
        updated = manager.config_change(
            "test",
            {"model": "sonnet"},
            user_id="user@example.com"
        )

        # Verify
        assert updated.settings["model"] == "sonnet"

        # Verify audit
        events_changed = [e for e in audit_events if e.event_type == "plugin_config_changed"]
        assert len(events_changed) == 1
        assert events_changed[0].details["old_config"]["model"] == "haiku"
        assert events_changed[0].details["new_config"]["model"] == "sonnet"

    def test_disable_plugin(self, setup):
        """Test disabling a plugin."""
        manager = setup["manager"]

        plugin = Plugin(id="test", version="1.0.0", name="Test", enabled=True)
        manager.install(plugin)
        manager.enable("test")

        # Disable
        disabled = manager.disable("test", user_id="user@example.com")

        # Verify
        assert disabled.enabled is False

    def test_uninstall_plugin(self, setup):
        """Test uninstalling a plugin."""
        manager = setup["manager"]
        registry = setup["registry"]

        plugin = Plugin(id="test", version="1.0.0", name="Test")
        manager.install(plugin)

        # Uninstall
        manager.uninstall("test", user_id="user@example.com")

        # Verify removed
        with pytest.raises(PluginNotFound):
            registry.get("test")

    def test_e2e_install_enable_config_disable_uninstall(self, setup):
        """E2E: Full lifecycle workflow."""
        manager = setup["manager"]
        registry = setup["registry"]
        audit_events = setup["audit_events"]

        plugin = Plugin(
            id="ai-review",
            version="2.0.1",
            name="AI Code Review",
            settings_schema={"type": "object", "properties": {}},
            settings={"model": "haiku"}
        )

        # 1. Install
        manager.install(plugin, user_id="user@example.com")
        assert registry.get("ai-review").id == "ai-review"

        # 2. Enable
        manager.enable("ai-review", user_id="user@example.com")
        assert registry.get("ai-review").enabled is True

        # 3. Config change
        manager.config_change("ai-review", {"model": "sonnet"}, user_id="user@example.com")
        assert registry.get("ai-review").settings["model"] == "sonnet"

        # 4. Disable
        manager.disable("ai-review", user_id="user@example.com")
        assert registry.get("ai-review").enabled is False

        # 5. Uninstall
        manager.uninstall("ai-review", user_id="user@example.com")
        with pytest.raises(PluginNotFound):
            registry.get("ai-review")

        # Verify audit trail
        assert len(audit_events) >= 5  # install, enable, config_change, disable, uninstall


if __name__ == "__main__":
    pytest.main([__file__, "-xvs"])
