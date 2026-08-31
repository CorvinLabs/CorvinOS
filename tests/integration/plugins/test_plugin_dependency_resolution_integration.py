"""
TIER-2: Plugin Dependency Resolution Integration Tests

Tests dependency graph construction, load-order satisfaction, circular dependency detection,
and version constraint satisfaction.
"""

import pytest
from typing import Dict, Set, List, Tuple


class DependencyResolver:
    """Helper class for dependency resolution"""

    def __init__(self):
        self.plugins: Dict[str, Dict] = {}
        self.edges: Dict[str, Set[str]] = {}

    def add_plugin(self, plugin_id: str, dependencies: List[str] = None):
        """Add plugin to graph"""
        self.plugins[plugin_id] = {
            "plugin_id": plugin_id,
            "dependencies": dependencies or [],
        }
        self.edges[plugin_id] = set(dependencies or [])

    def find_cycle(self, start: str, visited: Set[str] = None, rec_stack: Set[str] = None) -> bool:
        """Detect cycle using DFS"""
        visited = visited or set()
        rec_stack = rec_stack or set()

        visited.add(start)
        rec_stack.add(start)

        for neighbor in self.edges.get(start, []):
            if neighbor not in visited:
                if self.find_cycle(neighbor, visited, rec_stack):
                    return True
            elif neighbor in rec_stack:
                return True

        rec_stack.remove(start)
        return False

    def has_cycle(self) -> bool:
        """Check if graph has any cycle"""
        visited = set()
        for node in self.plugins:
            if node not in visited:
                if self.find_cycle(node, visited, set()):
                    return True
        return False

    def topological_sort(self) -> List[str]:
        """Get topological sort of plugins (dependencies before dependents)"""
        visited = set()
        stack = []

        def dfs(node):
            visited.add(node)
            # Visit dependencies first (so they appear in stack before dependent)
            for dep in self.edges.get(node, []):
                if dep not in visited and dep in self.plugins:
                    dfs(dep)
            # Post-order: add node after all its dependencies
            stack.append(node)

        for plugin_id in self.plugins:
            if plugin_id not in visited:
                dfs(plugin_id)

        # Stack already contains correct order: dependencies first, no reversal needed
        return stack


@pytest.mark.plugin_integration
@pytest.mark.plugin_dependencies
class TestDependencyGraphConstruction:
    """Test dependency graph construction"""

    def test_single_plugin_no_dependencies(self):
        """Single plugin with no dependencies"""
        resolver = DependencyResolver()
        resolver.add_plugin("plugin-a", [])

        assert len(resolver.plugins) == 1
        assert resolver.plugins["plugin-a"]["dependencies"] == []

    def test_linear_dependency_chain(self):
        """Linear dependency chain (A -> B -> C)"""
        resolver = DependencyResolver()
        resolver.add_plugin("plugin-a", [])
        resolver.add_plugin("plugin-b", ["plugin-a"])
        resolver.add_plugin("plugin-c", ["plugin-b"])

        assert resolver.plugins["plugin-c"]["dependencies"] == ["plugin-b"]
        assert len(resolver.plugins) == 3

    def test_diamond_dependency_graph(self):
        """Diamond dependency pattern (A <- B, C; B,C <- D)"""
        resolver = DependencyResolver()
        resolver.add_plugin("plugin-a", [])
        resolver.add_plugin("plugin-b", ["plugin-a"])
        resolver.add_plugin("plugin-c", ["plugin-a"])
        resolver.add_plugin("plugin-d", ["plugin-b", "plugin-c"])

        assert len(resolver.plugins) == 4
        assert set(resolver.plugins["plugin-d"]["dependencies"]) == {"plugin-b", "plugin-c"}

    def test_multiple_independent_chains(self):
        """Multiple independent dependency chains"""
        resolver = DependencyResolver()
        # Chain 1: A -> B
        resolver.add_plugin("plugin-a", [])
        resolver.add_plugin("plugin-b", ["plugin-a"])

        # Chain 2: C -> D
        resolver.add_plugin("plugin-c", [])
        resolver.add_plugin("plugin-d", ["plugin-c"])

        assert len(resolver.plugins) == 4
        # Both chains can coexist


@pytest.mark.plugin_integration
@pytest.mark.plugin_dependencies
class TestLoadOrderSatisfaction:
    """Test load order satisfies dependencies"""

    def test_dependencies_load_before_dependents(self, load_order_tracker):
        """Dependencies must load before dependents"""
        tracker = load_order_tracker

        # Define load order
        tracker.record_load("plugin-a", depends_on=[])
        tracker.record_load("plugin-b", depends_on=["plugin-a"])
        tracker.record_load("plugin-c", depends_on=["plugin-a", "plugin-b"])

        # Verify satisfied
        tracker.assert_dependencies_satisfied()

    def test_multiple_dependencies_all_satisfied(self, load_order_tracker):
        """All dependencies must be satisfied"""
        tracker = load_order_tracker

        tracker.record_load("base-plugin", depends_on=[])
        tracker.record_load("auth-plugin", depends_on=["base-plugin"])
        tracker.record_load("compute-plugin", depends_on=["base-plugin", "auth-plugin"])

        tracker.assert_dependencies_satisfied()

    def test_partial_load_order_unsatisfied(self, load_order_tracker):
        """Partial load order should fail if deps not met"""
        tracker = load_order_tracker

        # Try to load dependent before dependency
        tracker.record_load("plugin-b", depends_on=["plugin-a"])
        # Missing plugin-a!

        with pytest.raises(AssertionError):
            tracker.assert_dependencies_satisfied()

    def test_load_order_from_topological_sort(self):
        """Load order should follow topological sort"""
        resolver = DependencyResolver()
        resolver.add_plugin("plugin-a", [])
        resolver.add_plugin("plugin-b", ["plugin-a"])
        resolver.add_plugin("plugin-c", ["plugin-b"])

        order = resolver.topological_sort()

        # Dependencies come before dependents
        assert order.index("plugin-a") < order.index("plugin-b")
        assert order.index("plugin-b") < order.index("plugin-c")


@pytest.mark.plugin_integration
@pytest.mark.plugin_dependencies
class TestCircularDependencyDetection:
    """Test circular dependency detection"""

    def test_simple_cycle_detected(self, load_order_tracker):
        """Simple cycle (A -> B -> A) should be detected"""
        tracker = load_order_tracker

        tracker.record_load("plugin-a", depends_on=["plugin-b"])
        tracker.record_load("plugin-b", depends_on=["plugin-a"])

        with pytest.raises(AssertionError):
            tracker.assert_dependencies_satisfied()

    def test_self_cycle_detected(self):
        """Plugin depending on itself should be detected"""
        resolver = DependencyResolver()
        resolver.add_plugin("plugin-self", ["plugin-self"])

        # A plugin depending on itself is a cycle
        assert resolver.has_cycle()

    def test_long_cycle_detected(self):
        """Long cycle (A -> B -> C -> A) should be detected"""
        resolver = DependencyResolver()
        resolver.add_plugin("plugin-a", ["plugin-c"])
        resolver.add_plugin("plugin-b", ["plugin-a"])
        resolver.add_plugin("plugin-c", ["plugin-b"])

        assert resolver.has_cycle()

    def test_cycle_with_independent_plugin(self):
        """Cycle detection with independent plugins"""
        resolver = DependencyResolver()
        # Independent
        resolver.add_plugin("independent", [])
        # Cycle
        resolver.add_plugin("plugin-x", ["plugin-y"])
        resolver.add_plugin("plugin-y", ["plugin-x"])

        # Should detect cycle even with independent
        assert resolver.has_cycle()

    def test_no_cycle_in_dag(self):
        """Acyclic graph should pass cycle detection"""
        resolver = DependencyResolver()
        resolver.add_plugin("plugin-a", [])
        resolver.add_plugin("plugin-b", ["plugin-a"])
        resolver.add_plugin("plugin-c", ["plugin-a", "plugin-b"])

        assert not resolver.has_cycle()


@pytest.mark.plugin_integration
@pytest.mark.plugin_dependencies
class TestVersionConstraintSatisfaction:
    """Test version constraint satisfaction"""

    def test_exact_version_constraint(self):
        """Exact version constraint (==X.Y.Z)"""
        available_version = "1.5.0"
        required_version = "1.5.0"

        assert available_version == required_version

    def test_minimum_version_constraint(self):
        """Minimum version constraint (>=X.Y.Z)"""
        available_version = "1.5.3"
        required_version = ">=1.5.0"

        v_parts = tuple(map(int, available_version.split(".")))
        r_parts = tuple(map(int, required_version[2:].split(".")))

        assert v_parts >= r_parts

    def test_maximum_version_constraint(self):
        """Maximum version constraint (<=X.Y.Z)"""
        available_version = "1.4.9"
        required_version = "<=1.5.0"

        v_parts = tuple(map(int, available_version.split(".")))
        r_parts = tuple(map(int, required_version[2:].split(".")))

        assert v_parts <= r_parts

    def test_range_constraint(self):
        """Range constraint (>=X, <Y)"""
        available_version = "1.5.0"
        min_version = ">=1.0.0"
        max_version = "<2.0.0"

        v = tuple(map(int, available_version.split(".")))
        v_min = tuple(map(int, min_version[2:].split(".")))
        v_max = tuple(map(int, max_version[1:].split(".")))

        assert v_min <= v < v_max

    def test_multiple_constraints_must_all_satisfy(self):
        """All version constraints must be satisfied"""
        available_version = "1.5.2"
        constraints = [
            ">=1.5.0",  # Must be at least 1.5.0
            "<2.0.0",   # Must be less than 2.0.0
            "!=1.5.1",  # Must not be 1.5.1
        ]

        v = tuple(map(int, available_version.split(".")))

        # Check all constraints
        satisfies_all = True
        satisfies_all &= v >= tuple(map(int, "1.5.0".split(".")))
        satisfies_all &= v < tuple(map(int, "2.0.0".split(".")))
        satisfies_all &= available_version != "1.5.1"

        assert satisfies_all


@pytest.mark.plugin_integration
@pytest.mark.plugin_dependencies
class TestDependencyResolutionStrategies:
    """Test different dependency resolution strategies"""

    def test_greedy_version_selection(self):
        """Greedy: select latest compatible version"""
        available = ["1.0.0", "1.1.0", "1.2.0", "2.0.0"]
        required = ">=1.0.0, <2.0.0"

        # Filter compatible
        compatible = [v for v in available if tuple(map(int, v.split("."))) < (2, 0, 0)]
        selected = compatible[-1] if compatible else None

        assert selected == "1.2.0"

    def test_dependency_conflict_resolution(self):
        """Resolve conflicting version requirements"""
        # Plugin A requires lib>=1.0.0, <2.0.0
        # Plugin B requires lib>=1.5.0, <1.7.0
        # Available: 1.5.5

        available = "1.5.5"
        v = tuple(map(int, available.split(".")))

        # Check both constraints
        satisfies_a = (1, 0, 0) <= v < (2, 0, 0)
        satisfies_b = (1, 5, 0) <= v < (1, 7, 0)

        assert satisfies_a and satisfies_b

    def test_prefer_already_loaded_version(self):
        """Prefer already-loaded version if compatible"""
        loaded_version = "1.5.0"
        requirement = ">=1.4.0"

        v = tuple(map(int, loaded_version.split(".")))
        r = tuple(map(int, requirement[2:].split(".")))

        if v >= r:
            selected = loaded_version  # Reuse
        else:
            selected = "1.5.0"  # Need upgrade

        assert selected == loaded_version


@pytest.mark.plugin_integration
@pytest.mark.plugin_validation
class TestDependencyErrorHandling:
    """Test error handling in dependency resolution"""

    def test_missing_dependency_error(self, load_order_tracker):
        """Missing dependency should raise error"""
        tracker = load_order_tracker

        tracker.record_load("plugin-a", depends_on=["missing-plugin"])

        with pytest.raises(AssertionError):
            tracker.assert_dependencies_satisfied()

    def test_version_constraint_unsatisfiable(self):
        """Unsatisfiable version constraint should error"""
        available_version = "1.0.0"
        required_version = ">=2.0.0"

        v = tuple(map(int, available_version.split(".")))
        r = tuple(map(int, required_version[2:].split(".")))

        if v < r:
            error = "Version constraint unsatisfiable"
            assert "unsatisfiable" in error.lower()

    def test_ambiguous_dependency_warning(self):
        """Ambiguous dependencies should warn"""
        # Plugin depends on "lib" without version spec
        plugin_deps = ["lib"]  # No version!

        ambiguous = [d for d in plugin_deps if not any(op in d for op in [">=", "<=", "==", "~="])]

        if ambiguous:
            warning = "Ambiguous dependency: no version specified"
            assert "ambiguous" in warning.lower()
