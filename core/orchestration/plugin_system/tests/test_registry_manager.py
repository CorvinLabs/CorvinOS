"""Test suite for Plugin Registry Manager (ADR-0XXX k=2).

Focus: Plugin Registry YAML save/load + dependency resolver
"""

import tempfile
from pathlib import Path

import pytest

from core.orchestration.plugin_system.models import (
    CircularDependencyError,
    DependencyConflictError,
    DependencyResolver,
    Plugin,
    PluginAlreadyExists,
    PluginNotFound,
    PluginRegistry,
    PluginTier,
    PluginType,
)


class TestPluginRegistry:
    """Tests for PluginRegistry YAML persistence."""

    def test_registry_init_empty(self):
        """Test creating an empty registry."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "registry.yaml"
            registry = PluginRegistry(path=path)

            assert registry.path == path
            assert len(registry.plugins) == 0

    def test_registry_add_plugin(self):
        """Test adding a plugin to registry."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "registry.yaml"
            registry = PluginRegistry(path=path)

            plugin = Plugin(
                id="ai-review",
                version="2.0.1",
                name="AI Code Review",
                plugin_type=PluginType.SKILL
            )

            registry.add(plugin)
            assert "ai-review" in registry.plugins
            assert registry.get("ai-review").version == "2.0.1"

    def test_registry_add_duplicate_fails(self):
        """Test that adding duplicate plugin ID raises error."""
        registry = PluginRegistry(path=Path("/tmp/test.yaml"))

        plugin1 = Plugin(id="test", version="1.0.0", name="Test")
        plugin2 = Plugin(id="test", version="2.0.0", name="Test v2")

        registry.add(plugin1)
        with pytest.raises(PluginAlreadyExists):
            registry.add(plugin2)

    def test_registry_save_to_yaml(self):
        """Test saving registry to YAML file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "registry.yaml"
            registry = PluginRegistry(path=path)

            plugin = Plugin(
                id="ai-review",
                version="2.0.1",
                name="AI Code Review",
                enabled=True,
                settings={"model": "sonnet"}
            )

            registry.add(plugin)
            registry.save()

            # Verify file was created
            assert path.exists()

            # Verify content is YAML
            with open(path) as f:
                content = f.read()
                assert "ai-review" in content
                assert "2.0.1" in content

    def test_registry_load_from_yaml(self):
        """Test loading registry from YAML file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "registry.yaml"

            # Save a registry
            registry1 = PluginRegistry(path=path)
            plugin = Plugin(
                id="ai-review",
                version="2.0.1",
                name="AI Code Review",
                tier=PluginTier.B,
                enabled=True
            )
            registry1.add(plugin)
            registry1.save()

            # Load it back
            registry2 = PluginRegistry.load(path)

            assert len(registry2.plugins) == 1
            assert registry2.get("ai-review").version == "2.0.1"
            assert registry2.get("ai-review").tier == PluginTier.B

    def test_registry_get_nonexistent(self):
        """Test getting non-existent plugin raises error."""
        registry = PluginRegistry(path=Path("/tmp/test.yaml"))

        with pytest.raises(PluginNotFound):
            registry.get("nonexistent")

    def test_registry_remove(self):
        """Test removing plugin from registry."""
        registry = PluginRegistry(path=Path("/tmp/test.yaml"))

        plugin = Plugin(id="test", version="1.0.0", name="Test")
        registry.add(plugin)
        assert "test" in registry.plugins

        registry.remove("test")
        assert "test" not in registry.plugins


class TestDependencyResolver:
    """Tests for dependency resolution + topological sort."""

    def test_resolver_no_dependencies(self):
        """Test plugins with no dependencies."""
        plugins = {
            "a": Plugin(id="a", version="1.0.0", name="A", dependencies=[]),
            "b": Plugin(id="b", version="1.0.0", name="B", dependencies=[])
        }

        resolver = DependencyResolver(plugins)
        order = resolver.topological_sort()

        assert len(order) == 2
        assert set(order) == {"a", "b"}

    def test_resolver_linear_chain(self):
        """Test linear dependency chain: a -> b -> c."""
        plugins = {
            "a": Plugin(id="a", version="1.0.0", name="A", dependencies=[]),
            "b": Plugin(id="b", version="1.0.0", name="B", dependencies=["a>=1.0.0"]),
            "c": Plugin(id="c", version="1.0.0", name="C", dependencies=["b>=1.0.0"])
        }

        resolver = DependencyResolver(plugins)
        order = resolver.topological_sort()

        # a must come before b, b must come before c
        assert order.index("a") < order.index("b")
        assert order.index("b") < order.index("c")

    def test_resolver_version_mismatch(self):
        """Test version conflict detection."""
        plugins = {
            "postgres": Plugin(id="postgres", version="1.0.0", name="Postgres"),
            "tool": Plugin(id="tool", version="1.0.0", name="Tool", dependencies=["postgres>=2.0.0"])
        }

        resolver = DependencyResolver(plugins)

        with pytest.raises(DependencyConflictError) as exc_info:
            resolver.topological_sort()

        assert "postgres" in str(exc_info.value).lower()

    def test_resolver_missing_dependency(self):
        """Test missing dependency detection."""
        plugins = {
            "tool": Plugin(id="tool", version="1.0.0", name="Tool", dependencies=["nonexistent>=1.0.0"])
        }

        resolver = DependencyResolver(plugins)

        with pytest.raises(DependencyConflictError):
            resolver.topological_sort()

    def test_resolver_circular(self):
        """Test circular dependency detection."""
        plugins = {
            "a": Plugin(id="a", version="1.0.0", name="A", dependencies=["b"]),
            "b": Plugin(id="b", version="1.0.0", name="B", dependencies=["a"])
        }

        resolver = DependencyResolver(plugins)

        with pytest.raises(CircularDependencyError):
            resolver.topological_sort()

    def test_resolver_complex_dag(self):
        """Test complex DAG with multiple dependency paths."""
        plugins = {
            "base": Plugin(id="base", version="1.0.0", name="Base"),
            "tool1": Plugin(id="tool1", version="1.0.0", name="Tool1", dependencies=["base>=1.0.0"]),
            "tool2": Plugin(id="tool2", version="1.0.0", name="Tool2", dependencies=["base>=1.0.0"]),
            "app": Plugin(id="app", version="1.0.0", name="App", dependencies=["tool1>=1.0.0", "tool2>=1.0.0"])
        }

        resolver = DependencyResolver(plugins)
        order = resolver.topological_sort()

        # base must come first
        # tool1 and tool2 must come after base
        # app must come last
        assert order[0] == "base"
        assert order[-1] == "app"
        assert order.index("tool1") > 0
        assert order.index("tool2") > 0


if __name__ == "__main__":
    pytest.main([__file__, "-xvs"])
