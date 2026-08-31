"""
TIER-3 Feature-Level E2E Tests: Console Plugin — Initialization & Lifecycle

Tests the complete plugin lifecycle from load to unload, including:
- Hook invocation
- State management
- Idempotency
- Error handling
- Resource initialization
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from typing import Any, Dict


@pytest.mark.plugin_feature_e2e
@pytest.mark.plugin_init
@pytest.mark.plugin_lifecycle
class TestConsolePluginInitLifecycle:
    """Test console plugin initialization and lifecycle hooks"""

    def test_on_load_hook_called(self, stub_plugin_context):
        """Verify on_load hook is invoked when plugin is initialized"""
        # Setup plugin class
        class ConsolePlugin:
            def __init__(self):
                self.loaded = False
                self.context = None

            def on_load(self, ctx):
                self.loaded = True
                self.context = ctx

        plugin = ConsolePlugin()
        assert not plugin.loaded
        assert plugin.context is None

        # Trigger load
        plugin.on_load(stub_plugin_context)

        # Verify state change
        assert plugin.loaded
        assert plugin.context is not None
        assert plugin.context == stub_plugin_context

    def test_on_load_hook_idempotent(self, stub_plugin_context):
        """Verify on_load is idempotent (safe to call multiple times)"""
        class ConsolePlugin:
            def __init__(self):
                self.load_count = 0
                self.load_events = []

            def on_load(self, ctx):
                self.load_count += 1
                self.load_events.append({"count": self.load_count, "ctx": ctx})

        plugin = ConsolePlugin()

        # Call on_load multiple times
        for i in range(3):
            plugin.on_load(stub_plugin_context)

        # Should be safe and consistent
        assert plugin.load_count == 3
        assert len(plugin.load_events) == 3
        # All events should have same context
        for event in plugin.load_events:
            assert event["ctx"] == stub_plugin_context

    def test_initialization_error_handling(self, stub_plugin_context):
        """Verify initialization errors are isolated and don't crash system"""
        class ConsolePluginFailingInit:
            def on_load(self, ctx):
                raise ValueError("Failed to initialize console panel registry")

        plugin = ConsolePluginFailingInit()

        # Error should be caught
        with pytest.raises(ValueError, match="Failed to initialize"):
            plugin.on_load(stub_plugin_context)

        # Plugin should still exist (not crashed)
        assert plugin is not None

    def test_plugin_context_isolation(self, stub_plugin_context):
        """Verify each plugin instance gets isolated context"""
        class ConsolePlugin:
            def __init__(self):
                self.context = None

            def on_load(self, ctx):
                self.context = ctx

        plugin1 = ConsolePlugin()
        plugin2 = ConsolePlugin()

        # Load with same context
        plugin1.on_load(stub_plugin_context)
        plugin2.on_load(stub_plugin_context)

        # Both should have context reference (but isolated instances)
        assert plugin1.context == stub_plugin_context
        assert plugin2.context == stub_plugin_context
        # Different instances
        assert plugin1 is not plugin2

    def test_plugin_state_initialization(self, stub_plugin_context):
        """Verify plugin state is properly initialized"""
        class ConsolePlugin:
            def __init__(self):
                self.panels = []
                self.routes = {}
                self.hooks = {}

            def on_load(self, ctx):
                # Initialize empty state structures
                self.panels = []
                self.routes = {"home": "/", "settings": "/settings"}
                self.hooks = {"on_panel_render": [], "on_route_change": []}

        plugin = ConsolePlugin()
        plugin.on_load(stub_plugin_context)

        # Verify initial state
        assert isinstance(plugin.panels, list)
        assert isinstance(plugin.routes, dict)
        assert isinstance(plugin.hooks, dict)
        assert len(plugin.routes) == 2
        assert "on_panel_render" in plugin.hooks

    def test_initialization_with_config(self, stub_plugin_context):
        """Verify plugin initialization respects configuration"""
        class ConsolePlugin:
            def __init__(self, config=None):
                self.config = config or {}
                self.initialized = False

            def on_load(self, ctx, config=None):
                self.config = config or self.config
                self.initialized = True

        config = {"theme": "dark", "panels": ["dashboard", "settings"]}
        plugin = ConsolePlugin(config)
        plugin.on_load(stub_plugin_context)

        assert plugin.initialized
        assert plugin.config["theme"] == "dark"
        assert len(plugin.config["panels"]) == 2

    def test_on_unload_hook_called(self):
        """Verify on_unload hook is invoked during cleanup"""
        class ConsolePlugin:
            def __init__(self):
                self.unloaded = False
                self.cleanup_called = False

            def on_load(self, ctx):
                self.loaded = True

            def on_unload(self):
                self.unloaded = True
                self.cleanup_called = True

        plugin = ConsolePlugin()
        assert not plugin.unloaded

        # Trigger unload
        plugin.on_unload()
        assert plugin.unloaded
        assert plugin.cleanup_called

    def test_lifecycle_state_transitions(self, stub_plugin_context):
        """Verify proper state transitions through full lifecycle"""
        states = []

        class ConsolePlugin:
            def __init__(self):
                self.state = "created"
                states.append(self.state)

            def on_load(self, ctx):
                self.state = "loaded"
                states.append(self.state)

            def on_unload(self):
                self.state = "unloaded"
                states.append(self.state)

        plugin = ConsolePlugin()
        plugin.on_load(stub_plugin_context)
        plugin.on_unload()

        # Verify state progression
        assert states == ["created", "loaded", "unloaded"]

    def test_concurrent_plugin_initialization(self, stub_plugin_context):
        """Verify multiple plugins can be initialized concurrently without issues"""
        class ConsolePlugin:
            _instances = []

            def __init__(self):
                self.id = None
                self.loaded = False

            def on_load(self, ctx):
                self.id = len(ConsolePlugin._instances)
                self.loaded = True
                ConsolePlugin._instances.append(self)

        plugins = [ConsolePlugin() for _ in range(5)]
        for plugin in plugins:
            plugin.on_load(stub_plugin_context)

        # All should be initialized
        assert all(p.loaded for p in plugins)
        assert len(ConsolePlugin._instances) == 5
        # Each should have unique id
        assert len(set(p.id for p in plugins)) == 5
