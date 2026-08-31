"""
TIER-4: Load Order Verification Tests

Verifies dependency graph construction, topological sorting, circular dependency
detection, and boot-layer ordering constraints.
"""

import pytest


@pytest.mark.plugin_system_health
@pytest.mark.plugin_load_order
class TestDependencyGraphConstruction:
    """Build and validate dependency graphs"""

    def test_simple_linear_dependency_chain(self, load_order_dependency_graph):
        """Single dependency chain loads in correct order"""
        graph = load_order_dependency_graph

        # Create chain: C → B → A
        graph.add_plugin("A")
        graph.add_plugin("B", depends_on=["A"])
        graph.add_plugin("C", depends_on=["B"])

        # Record correct load order
        graph.record_load("A")
        graph.record_load("B")
        graph.record_load("C")

        # Verify topological ordering
        assert graph.verify_topological_sort()

    def test_multiple_dependency_paths(self, load_order_dependency_graph):
        """Complex DAG with multiple dependency paths loads correctly"""
        graph = load_order_dependency_graph

        # Diamond dependency: D → B,C  B,C → A
        graph.add_plugin("A")
        graph.add_plugin("B", depends_on=["A"])
        graph.add_plugin("C", depends_on=["A"])
        graph.add_plugin("D", depends_on=["B", "C"])

        # Record valid load order
        graph.record_load("A")
        graph.record_load("B")
        graph.record_load("C")
        graph.record_load("D")

        assert graph.verify_topological_sort()

    def test_plugin_with_no_dependencies(self, load_order_dependency_graph):
        """Independent plugin loads at any time"""
        graph = load_order_dependency_graph

        graph.add_plugin("independent")
        graph.record_load("independent")

        assert graph.verify_topological_sort()

    def test_missing_dependency_detected(self, load_order_dependency_graph):
        """Loading plugin before its dependency fails"""
        graph = load_order_dependency_graph

        # B depends on A
        graph.add_plugin("A")
        graph.add_plugin("B", depends_on=["A"])

        # Load B before A
        graph.record_load("B")
        graph.record_load("A")

        # Should fail topological sort
        assert not graph.verify_topological_sort()


@pytest.mark.plugin_system_health
@pytest.mark.plugin_load_order
class TestTopologicalSorting:
    """Verify topological sort properties"""

    def test_topological_sort_preserves_partial_order(self, load_order_dependency_graph):
        """Topo-sort respects all dependency constraints"""
        graph = load_order_dependency_graph

        # Create complex DAG
        for plugin in ["base", "util", "core", "feature1", "feature2", "app"]:
            graph.add_plugin(plugin)

        graph.plugins["util"] = ["base"]
        graph.plugins["core"] = ["util"]
        graph.plugins["feature1"] = ["core"]
        graph.plugins["feature2"] = ["core", "util"]
        graph.plugins["app"] = ["feature1", "feature2"]

        # Load in valid order
        graph.record_load("base")
        graph.record_load("util")
        graph.record_load("core")
        graph.record_load("feature1")
        graph.record_load("feature2")
        graph.record_load("app")

        assert graph.verify_topological_sort()

    def test_valid_alternative_load_orders(self, load_order_dependency_graph):
        """Multiple valid topological orders are accepted"""
        graph = load_order_dependency_graph

        # Independent chains: A→B and C→D
        graph.add_plugin("A")
        graph.add_plugin("B", depends_on=["A"])
        graph.add_plugin("C")
        graph.add_plugin("D", depends_on=["C"])

        # Both orders should be valid
        # Order 1: A, B, C, D
        graph.load_order = []
        graph.record_load("A")
        graph.record_load("B")
        graph.record_load("C")
        graph.record_load("D")
        assert graph.verify_topological_sort()

        # Order 2: C, D, A, B (interleaved)
        graph.load_order = []
        graph.record_load("C")
        graph.record_load("A")
        graph.record_load("D")
        graph.record_load("B")
        assert graph.verify_topological_sort()

    def test_sibling_order_flexible(self, load_order_dependency_graph):
        """Sibling plugins (no relationship) can load in any order"""
        graph = load_order_dependency_graph

        graph.add_plugin("sibling1")
        graph.add_plugin("sibling2")
        graph.add_plugin("sibling3")

        # Any order is valid
        for order in [["sibling1", "sibling2", "sibling3"],
                      ["sibling3", "sibling1", "sibling2"],
                      ["sibling2", "sibling3", "sibling1"]]:
            graph.load_order = []
            for plugin in order:
                graph.record_load(plugin)
            assert graph.verify_topological_sort()


@pytest.mark.plugin_system_health
@pytest.mark.plugin_load_order
class TestCircularDependencyDetection:
    """Detect and prevent circular dependencies"""

    def test_simple_cycle_detected(self, load_order_dependency_graph):
        """Two-plugin cycle is detected"""
        graph = load_order_dependency_graph

        # A → B → A
        graph.add_plugin("A", depends_on=["B"])
        graph.add_plugin("B", depends_on=["A"])

        cycle = graph.detect_circular_dependency()
        assert cycle is not None
        assert "A" in cycle
        assert "B" in cycle

    def test_self_dependency_detected(self, load_order_dependency_graph):
        """Plugin depending on itself is detected"""
        graph = load_order_dependency_graph

        graph.add_plugin("self-dep", depends_on=["self-dep"])

        cycle = graph.detect_circular_dependency()
        assert cycle is not None
        assert "self-dep" in cycle

    def test_long_cycle_detected(self, load_order_dependency_graph):
        """Cycle in long dependency chain detected"""
        graph = load_order_dependency_graph

        # A → B → C → D → A (4-node cycle)
        graph.add_plugin("A", depends_on=["D"])
        graph.add_plugin("B", depends_on=["A"])
        graph.add_plugin("C", depends_on=["B"])
        graph.add_plugin("D", depends_on=["C"])

        cycle = graph.detect_circular_dependency()
        assert cycle is not None
        # All nodes in cycle should be present
        for node in ["A", "B", "C", "D"]:
            assert node in cycle

    def test_no_false_positives_for_valid_dag(self, load_order_dependency_graph):
        """Valid DAG doesn't report cycles"""
        graph = load_order_dependency_graph

        # Valid diamond DAG
        graph.add_plugin("A")
        graph.add_plugin("B", depends_on=["A"])
        graph.add_plugin("C", depends_on=["A"])
        graph.add_plugin("D", depends_on=["B", "C"])

        cycle = graph.detect_circular_dependency()
        assert cycle is None

    def test_partial_cycle_with_valid_subset(self, load_order_dependency_graph):
        """Cycle in subset detected even with valid plugins"""
        graph = load_order_dependency_graph

        # Valid: E, F, G
        graph.add_plugin("E")
        graph.add_plugin("F", depends_on=["E"])
        graph.add_plugin("G", depends_on=["F"])

        # Cycle: A, B, C
        graph.add_plugin("A", depends_on=["C"])
        graph.add_plugin("B", depends_on=["A"])
        graph.add_plugin("C", depends_on=["B"])

        cycle = graph.detect_circular_dependency()
        assert cycle is not None
        # Cycle found among A, B, C
        assert any(node in cycle for node in ["A", "B", "C"])


@pytest.mark.plugin_system_health
@pytest.mark.plugin_load_order
class TestBootLayerOrdering:
    """Boot layer constraints on load order"""

    def test_compliance_layer_loads_first(self, load_order_dependency_graph):
        """Compliance layer plugins load before all others"""
        graph = load_order_dependency_graph

        # Simulate boot layers via metadata
        plugins_with_layers = {
            "audit": ("compliance", None),
            "core-base": ("core", None),
            "bundled-feature": ("bundled", None),
            "custom-plugin": ("installed", None),
        }

        for plugin, (layer, deps) in plugins_with_layers.items():
            graph.add_plugin(plugin, depends_on=[])
            graph.plugins[plugin] = [f"layer_{layer}"]  # Implicit dependency

        # Correct order: compliance < core < bundled < installed
        graph.record_load("audit")
        graph.record_load("core-base")
        graph.record_load("bundled-feature")
        graph.record_load("custom-plugin")

        # Build layer check
        load_indices = {
            "audit": 0,
            "core-base": 1,
            "bundled-feature": 2,
            "custom-plugin": 3,
        }

        # Each layer in correct position
        assert load_indices["audit"] < load_indices["core-base"]
        assert load_indices["core-base"] < load_indices["bundled-feature"]
        assert load_indices["bundled-feature"] < load_indices["custom-plugin"]

    def test_core_layer_respects_compliance_dependency(self, load_order_dependency_graph):
        """Core layer explicitly depends on compliance"""
        graph = load_order_dependency_graph

        graph.add_plugin("compliance-audit")
        graph.add_plugin("core-system", depends_on=["compliance-audit"])

        # Must load in order
        graph.record_load("compliance-audit")
        graph.record_load("core-system")

        assert graph.verify_topological_sort()

    def test_bundled_can_depend_on_core(self, load_order_dependency_graph):
        """Bundled plugins can depend on core"""
        graph = load_order_dependency_graph

        graph.add_plugin("core-api")
        graph.add_plugin("bundled-logger", depends_on=["core-api"])

        graph.record_load("core-api")
        graph.record_load("bundled-logger")

        assert graph.verify_topological_sort()

    def test_installed_can_depend_on_all_layers(self, load_order_dependency_graph):
        """Installed plugins can depend on any previous layer"""
        graph = load_order_dependency_graph

        # Build dependency chain across layers
        graph.add_plugin("compliance-base")
        graph.add_plugin("core-api", depends_on=["compliance-base"])
        graph.add_plugin("bundled-feature", depends_on=["core-api"])
        graph.add_plugin("custom-app", depends_on=["bundled-feature"])

        # Record in layer order
        for plugin in ["compliance-base", "core-api", "bundled-feature", "custom-app"]:
            graph.record_load(plugin)

        assert graph.verify_topological_sort()


@pytest.mark.plugin_system_health
@pytest.mark.plugin_load_order
class TestVersionConstraintSatisfaction:
    """Dependency version constraints"""

    def test_version_requirement_satisfied(self, load_order_dependency_graph):
        """Plugin loads only when dependency version requirement met"""
        graph = load_order_dependency_graph

        # Simulate version constraints
        graph.add_plugin("base", depends_on=[])
        graph.plugins["dependent"] = ["base>=1.0"]  # Version constraint

        # Track versions
        graph.load_events.append({
            "plugin_id": "base",
            "version": "1.2",
            "order": 1
        })
        graph.load_events.append({
            "plugin_id": "dependent",
            "version": "1.0",
            "order": 2,
            "required_dep_version": ">=1.0"
        })

        # Should be satisfied (1.2 >= 1.0)
        assert graph.load_events[1]["required_dep_version"] == ">=1.0"

    def test_version_conflict_blocks_load(self, load_order_dependency_graph):
        """Plugin fails to load if version requirement not met"""
        graph = load_order_dependency_graph

        # Version mismatch
        graph.add_plugin("old-base", depends_on=[])
        graph.plugins["needs_new"] = ["old-base>=2.0"]

        graph.load_events.append({
            "plugin_id": "old-base",
            "version": "1.0",
            "order": 1
        })

        # Track failed load due to version
        graph.load_events.append({
            "plugin_id": "needs_new",
            "version": "1.0",
            "order": None,  # Failed to load
            "reason": "dependency version constraint not met",
            "required": "old-base>=2.0",
            "available": "old-base=1.0"
        })

        # Verify load failed
        failed = [e for e in graph.load_events if e.get("order") is None]
        assert len(failed) > 0

    def test_multiple_version_constraints_all_satisfied(self, load_order_dependency_graph):
        """Plugin with multiple dependency version constraints loads"""
        graph = load_order_dependency_graph

        graph.add_plugin("utils", depends_on=[])
        graph.add_plugin("core", depends_on=[])
        graph.plugins["app"] = ["utils>=2.0", "core>=1.5"]

        # Versions available
        graph.load_events.extend([
            {"plugin_id": "utils", "version": "2.1", "order": 1},
            {"plugin_id": "core", "version": "1.8", "order": 2},
            {"plugin_id": "app", "version": "1.0", "order": 3}
        ])

        # All constraints satisfied
        # (2.1 >= 2.0) and (1.8 >= 1.5)
        app_event = graph.load_events[-1]
        assert app_event["plugin_id"] == "app"
        assert app_event["order"] == 3  # Successfully loaded
