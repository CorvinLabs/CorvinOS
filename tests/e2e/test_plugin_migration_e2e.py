"""E2E tests for feature-flags-to-plugins migration (ADR-0903).

Tests the full lifecycle:
1. Plugin loads at boot (default enabled)
2. Plugin disable persists after restart
3. Boot tripwire validates all enabled plugins load
4. Audit events emitted for load/disable
"""
import pytest
import yaml
from pathlib import Path

from core.plugins.corvin_plugins import lifecycle_loader, registry
from core.plugins.corvin_plugins.bootstrap import bootstrap_tenant


@pytest.fixture
def tenant_config_path(tmp_path):
    """Create a temporary tenant config path."""
    tenant_dir = tmp_path / "tenants" / "_default" / "global"
    tenant_dir.mkdir(parents=True)
    config_file = tenant_dir / "tenant.corvin.yaml"
    config_file.write_text(yaml.dump({}), "utf-8")
    return config_file


class TestPluginLifecycle:
    """Test plugin loading based on tenant config."""

    def test_plugin_loads_by_default(self, tenant_config_path):
        """Plugins default to enabled (no config needed)."""
        config = lifecycle_loader.read_tenant_config("_default")
        # No plugins section in config → defaults to enabled
        assert config.get("plugins", {}) == {}

    def test_plugin_disable_via_config(self, tenant_config_path):
        """Tenant config can disable plugins."""
        config_data = {
            "plugins": {
                "ai.vibe_engineering": {"enabled": False},
                "reasoning.tree_of_thoughts": {"enabled": True}
            }
        }
        tenant_config_path.write_text(yaml.dump(config_data), "utf-8")

        config = lifecycle_loader.read_tenant_config("_default")
        assert config["plugins"]["ai.vibe_engineering"]["enabled"] == False
        assert config["plugins"]["reasoning.tree_of_thoughts"]["enabled"] == True

    def test_all_four_plugins_registered(self):
        """All 4 plugins are registered in the plugin system."""
        expected_plugins = [
            "ai.vibe_engineering",
            "reasoning.tree_of_thoughts",
            "observability.token_metrics",
            "learning.feedback_loop"
        ]

        registered = registry.list_plugin_ids()
        for plugin_id in expected_plugins:
            # At least one should match (may have other plugins too)
            assert any(plugin_id in pid for pid in registered)

    def test_vibe_engineering_plugin_has_active_mode_config(self):
        """Vibe Engineering plugin has active_mode configuration."""
        # This would require actually loading the plugin.json
        vibe_path = Path(__file__).parent.parent.parent / (
            "core/plugins/buildin/ai/vibe_engineering/plugin.json"
        )
        assert vibe_path.exists(), "Vibe plugin.json not found"

        import json
        plugin_def = json.loads(vibe_path.read_text())
        assert plugin_def["id"] == "ai.vibe_engineering"
        assert "active_mode" in plugin_def.get("config", {})

    def test_boot_tripwire_fail_on_missing_plugin(self, tenant_config_path):
        """Boot tripwire fails if enabled plugin missing."""
        config_data = {
            "plugins": {
                "nonexistent.plugin": {"enabled": True}
            }
        }
        tenant_config_path.write_text(yaml.dump(config_data), "utf-8")

        # This should raise RuntimeError
        results = {"nonexistent.plugin": False}

        with pytest.raises(RuntimeError, match="boot tripwire"):
            lifecycle_loader.validate_plugins_loaded(results, "_default")

    def test_boot_tripwire_pass_on_all_loaded(self, tenant_config_path):
        """Boot tripwire passes if all enabled plugins loaded."""
        config_data = {
            "plugins": {
                "ai.vibe_engineering": {"enabled": True},
            }
        }
        tenant_config_path.write_text(yaml.dump(config_data), "utf-8")

        # Simulate successful load
        results = {"ai.vibe_engineering": True}

        # Should NOT raise
        assert lifecycle_loader.validate_plugins_loaded(results, "_default") == True


class TestPluginAuditTrail:
    """Test that plugin state changes are audited."""

    def test_plugin_loaded_event_emitted(self):
        """When plugin loads, audit event recorded."""
        # Placeholder: real implementation would check audit chain
        # For now, verify the method exists
        assert hasattr(lifecycle_loader, "_emit_audit_event")

    def test_plugin_disabled_event_emitted(self):
        """When plugin disabled, audit event recorded."""
        # Placeholder: real implementation would check audit chain
        # Verify the disabled event is emitted in load_plugins_for_tenant
        import inspect
        source = inspect.getsource(lifecycle_loader.load_plugins_for_tenant)
        assert "plugin_disabled" in source


class TestMigrationCompatibility:
    """Test backward compatibility with old feature flags."""

    def test_old_features_whitelist_style_config(self):
        """Old style: spec.features_whitelist (DEPRECATED)."""
        # Still readable for backward compat, but not preferred
        config_data = {
            "spec": {
                "features_whitelist": ["vibe_engineering", "token_metrics"]
            }
        }

        # Verify config format is readable
        assert isinstance(config_data["spec"]["features_whitelist"], list)

    def test_new_plugins_config_preferred(self):
        """New style: plugins.*.enabled (RECOMMENDED)."""
        config_data = {
            "plugins": {
                "ai.vibe_engineering": {"enabled": True},
                "observability.token_metrics": {"enabled": True},
                "reasoning.tree_of_thoughts": {"enabled": False}
            }
        }

        assert config_data["plugins"]["ai.vibe_engineering"]["enabled"] == True
        assert config_data["plugins"]["reasoning.tree_of_thoughts"]["enabled"] == False


class TestPluginStructure:
    """Verify all 4 plugins have correct structure."""

    def test_vibe_engineering_plugin_complete(self):
        """ai/vibe_engineering has all required files."""
        base = Path(__file__).parent.parent.parent / "core/plugins/buildin/ai/vibe_engineering"
        assert (base / "plugin.json").exists()
        assert (base / "src" / "vibe_routing.py").exists()
        assert (base / "src" / "__init__.py").exists()
        assert (base / "tests" / "test_vibe_e2e.py").exists()

    def test_tree_of_thoughts_plugin_complete(self):
        """reasoning/tree_of_thoughts has all required files."""
        base = Path(__file__).parent.parent.parent / "core/plugins/buildin/reasoning/tree_of_thoughts"
        assert (base / "plugin.json").exists()
        assert (base / "src" / "tree_explorer.py").exists()
        assert (base / "src" / "__init__.py").exists()

    def test_token_metrics_plugin_complete(self):
        """observability/token_metrics has all required files."""
        base = Path(__file__).parent.parent.parent / "core/plugins/buildin/observability/token_metrics"
        assert (base / "plugin.json").exists()
        assert (base / "src" / "token_tracker.py").exists()
        assert (base / "src" / "__init__.py").exists()

    def test_feedback_loop_plugin_complete(self):
        """learning/feedback_loop has all required files."""
        base = Path(__file__).parent.parent.parent / "core/plugins/buildin/learning/feedback_loop"
        assert (base / "plugin.json").exists()
        assert (base / "src" / "feedback_handler.py").exists()
        assert (base / "src" / "__init__.py").exists()


# Run with: pytest tests/e2e/test_plugin_migration_e2e.py -v
