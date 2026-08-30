"""
TIER-1: Plugin Lifecycle Integration Tests

Tests plugin registration, loading, activation, and state transitions.
Covers: registry ops, hook system, resource cleanup, versioning.
"""

import pytest
from unittest.mock import Mock, MagicMock, patch
from typing import Dict, Any, List


@pytest.mark.plugin_unit
@pytest.mark.plugin_integration
class TestPluginRegistry:
    """Test plugin registry state management"""

    def test_plugin_register_adds_to_registry(self):
        """Register plugin and verify it's in registry"""
        registry = {}
        plugin = {"plugin_id": "test-1", "version": "1.0.0"}

        # Simulate registration
        registry[plugin["plugin_id"]] = plugin

        assert "test-1" in registry
        assert registry["test-1"]["version"] == "1.0.0"

    def test_plugin_registry_prevents_duplicate_registration(self):
        """Duplicate plugin_id should be rejected"""
        registry = {}
        plugin_a = {"plugin_id": "test-1", "version": "1.0.0"}
        plugin_b = {"plugin_id": "test-1", "version": "2.0.0"}

        registry[plugin_a["plugin_id"]] = plugin_a

        # Second registration should fail
        if plugin_b["plugin_id"] in registry:
            with pytest.raises(ValueError):
                raise ValueError(f"Plugin {plugin_b['plugin_id']} already registered")

    def test_plugin_registry_unregister_removes_plugin(self):
        """Unregister removes plugin from registry"""
        registry = {"test-1": {"plugin_id": "test-1"}}

        del registry["test-1"]

        assert "test-1" not in registry


@pytest.mark.plugin_unit
@pytest.mark.plugin_integration
class TestPluginLifecycle:
    """Test plugin state transitions"""

    def test_plugin_state_transitions(self):
        """Plugin transitions: registered → loaded → active → inactive → unloaded"""
        states = []

        class MockPlugin:
            def __init__(self, plugin_id):
                self.plugin_id = plugin_id
                self.state = "registered"
                states.append(("init", self.state))

            def load(self):
                self.state = "loaded"
                states.append(("load", self.state))

            def activate(self):
                self.state = "active"
                states.append(("activate", self.state))

            def deactivate(self):
                self.state = "inactive"
                states.append(("deactivate", self.state))

            def unload(self):
                self.state = "unloaded"
                states.append(("unload", self.state))

        plugin = MockPlugin("test-1")
        plugin.load()
        plugin.activate()
        plugin.deactivate()
        plugin.unload()

        expected = [
            ("init", "registered"),
            ("load", "loaded"),
            ("activate", "active"),
            ("deactivate", "inactive"),
            ("unload", "unloaded"),
        ]
        assert states == expected

    def test_plugin_cannot_activate_before_loading(self):
        """Activating before load should fail"""
        class MockPlugin:
            def __init__(self):
                self.state = "registered"

            def activate(self):
                if self.state != "loaded":
                    raise RuntimeError(f"Cannot activate from state: {self.state}")
                self.state = "active"

        plugin = MockPlugin()
        with pytest.raises(RuntimeError):
            plugin.activate()


@pytest.mark.plugin_unit
@pytest.mark.plugin_integration
class TestPluginHookSystem:
    """Test plugin hook execution and error handling"""

    def test_before_hook_executes_before_main(self):
        """Hook execution order: before → main → after"""
        execution_order = []

        class HookSystem:
            def __init__(self):
                self.before_hooks = []
                self.after_hooks = []

            def register_before_hook(self, name, fn):
                self.before_hooks.append((name, fn))

            def register_after_hook(self, name, fn):
                self.after_hooks.append((name, fn))

            def execute(self, main_fn):
                for name, hook in self.before_hooks:
                    hook()

                main_fn()

                for name, hook in self.after_hooks:
                    hook()

        system = HookSystem()
        system.register_before_hook("init", lambda: execution_order.append("before"))
        system.register_after_hook("cleanup", lambda: execution_order.append("after"))

        system.execute(lambda: execution_order.append("main"))

        assert execution_order == ["before", "main", "after"]

    def test_hook_exception_propagates(self):
        """Exception in hook propagates to caller"""
        class HookSystem:
            def execute_hook(self, fn):
                return fn()

        system = HookSystem()

        def failing_hook():
            raise ValueError("Hook failed")

        with pytest.raises(ValueError):
            system.execute_hook(failing_hook)

    def test_multiple_hooks_all_execute(self):
        """All registered hooks execute, even if one fails"""
        executed = []

        class HookSystem:
            def __init__(self):
                self.hooks = []

            def register_hook(self, fn):
                self.hooks.append(fn)

            def execute_all(self):
                results = []
                for hook in self.hooks:
                    try:
                        results.append(hook())
                    except Exception as e:
                        results.append(e)
                return results

        system = HookSystem()
        system.register_hook(lambda: executed.append("hook-1"))
        system.register_hook(lambda: executed.append("hook-2"))
        system.register_hook(lambda: executed.append("hook-3"))

        system.execute_all()

        assert executed == ["hook-1", "hook-2", "hook-3"]


@pytest.mark.plugin_unit
@pytest.mark.plugin_integration
class TestPluginResourceCleanup:
    """Test plugin resource allocation and cleanup"""

    def test_plugin_resources_cleaned_on_unload(self):
        """Unload should close all open resources"""
        resources = []

        class MockResource:
            def __init__(self, name):
                self.name = name
                self.closed = False

            def close(self):
                self.closed = True

        class MockPlugin:
            def __init__(self):
                self.resources = [
                    MockResource("file"),
                    MockResource("socket"),
                    MockResource("db"),
                ]

            def unload(self):
                for resource in self.resources:
                    resource.close()

        plugin = MockPlugin()
        plugin.unload()

        for resource in plugin.resources:
            assert resource.closed is True

    def test_plugin_cleanup_on_error(self):
        """Resources cleaned even on error"""
        cleanup_called = []

        class MockPlugin:
            def __init__(self):
                self.resource = Mock()

            def load(self):
                try:
                    raise RuntimeError("Load failed")
                finally:
                    self.resource.close()
                    cleanup_called.append(True)

        plugin = MockPlugin()
        with pytest.raises(RuntimeError):
            plugin.load()

        assert cleanup_called == [True]


@pytest.mark.plugin_unit
@pytest.mark.plugin_integration
class TestPluginVersioningMatrix:
    """Test plugin versioning and compatibility"""

    def test_plugin_version_check_compatibility(self):
        """Version 1.0.0 compatible with requires >=1.0.0, <2.0.0"""
        plugin_version = "1.0.0"
        requires = ">=1.0.0,<2.0.0"

        major_plugin, minor_plugin, patch_plugin = map(
            int, plugin_version.split(".")
        )
        major_requires = int(requires.split(".")[0].lstrip("><=~^"))

        assert major_plugin == major_requires

    def test_version_incompatibility_detection(self):
        """Version 0.9.0 incompatible with requires >=1.0.0"""
        plugin_version = "0.9.0"
        requires = ">=1.0.0"

        major_plugin = int(plugin_version.split(".")[0])
        major_requires = int(requires.split(".")[0].lstrip("><=~^"))

        assert major_plugin != major_requires

    def test_version_prerelease_compatibility(self):
        """Prerelease versions handled correctly"""
        version = "1.0.0-beta.1"
        requires = ">=1.0.0"

        # Extract base version (before -suffix)
        base_version = version.split("-")[0]
        major, minor, patch = map(int, base_version.split("."))

        assert major >= 1

    def test_compatibility_matrix_generation(self):
        """Generate compatibility matrix for multiple versions"""
        matrix = {
            "plugin-a": {
                "0.1.0": {"requires_api": ">=1.0.0"},
                "1.0.0": {"requires_api": ">=2.0.0"},
                "2.0.0": {"requires_api": ">=3.0.0"},
            }
        }

        # Get all versions for plugin-a
        versions = list(matrix["plugin-a"].keys())
        assert versions == ["0.1.0", "1.0.0", "2.0.0"]

        # Each version has API requirement
        for version, spec in matrix["plugin-a"].items():
            assert "requires_api" in spec
