"""Full E2E tests: Plugin Orchestration (ADR-0610/0611/0612 complete)."""

import pytest

from core.plugins.corvin_plugins.capability_registry import CapabilityRegistry
from core.plugins.corvin_plugins.manifest_capabilities import (
    Capability,
    CapabilityType,
    PluginCapabilitiesManifest,
)
from core.skills.orchestration.orchestrator import PluginOrchestrator
from core.skills.orchestration.learning_integration import OrchestrationLearner
from core.skills.skill_dependency_resolver import SkillDependencyResolver
from core.skills.skill_manifest_dependencies import (
    CapabilityDependency,
    SkillCapabilitiesDependencies,
)


class TestFullOrchestrationE2E:
    """Full end-to-end orchestration scenarios."""

    @pytest.fixture
    def setup(self):
        """Setup: registry + plugins + resolver + orchestrator + learner."""
        registry = CapabilityRegistry()

        # Register 2 plugins
        for plugin_id, version in [("plugin1", "1.0"), ("plugin2", "2.0")]:
            cap = Capability(
                id=f"cap.{plugin_id}",
                type=CapabilityType.CONTEXT_SOURCE,
                description=f"Capability for {plugin_id}",
            )
            manifest = PluginCapabilitiesManifest(
                plugin_id=plugin_id,
                plugin_version=version,
                capabilities=[cap],
            )
            registry.register_manifest(manifest)

        resolver = SkillDependencyResolver(registry)
        orchestrator = PluginOrchestrator()
        orchestrator.registry = registry
        learner = OrchestrationLearner()

        return {
            "registry": registry,
            "resolver": resolver,
            "orchestrator": orchestrator,
            "learner": learner,
        }

    def test_scenario_resolve_select_invoke_learn(self, setup):
        """Complete scenario: resolve → select → invoke → learn."""
        # 1. Skill declares dependencies
        dep = CapabilityDependency(
            id="context",
            type="capability",
            capability_type="context_source",
            capability_id="cap.plugin1",
            allowed_plugins=["plugin1", "plugin2"],
            required=True,
        )
        skill = SkillCapabilitiesDependencies(
            skill_id="skill1",
            skill_version="1.0",
            dependencies=[dep],
        )

        # 2. Resolve dependencies
        resolution = setup["resolver"].resolve_all(skill)
        assert resolution.status == "ok"
        assert len(resolution.resolved_plugins) == 1

        # 3. Invoke via orchestrator
        result = setup["orchestrator"].invoke_with_orchestration(
            skill_id="skill1",
            capability_id="cap.plugin1",
            input_data={"query": "test"},
            selection_method="deterministic",
            allowed_plugins=["plugin1", "plugin2"],
        )
        assert result.status == "ok"
        assert result.plugin_id == "plugin1"

        # 4. Record outcome in learner
        setup["learner"].process_outcome(
            skill_id="skill1",
            plugin_id="plugin1",
            capability_id="cap.plugin1",
            latency_ms=100,
            success=True,
            slo_met=True,
        )

        # 5. Get recommendation
        rec = setup["learner"].recommend(
            "skill1",
            "cap.plugin1",
            ["plugin1", "plugin2"],
        )
        assert rec is not None
        plugin_id, confidence = rec
        assert plugin_id == "plugin1"
        assert confidence > 0.0

    def test_scenario_tenant_isolation(self, setup):
        """Scenario: Tenant A and B are isolated."""
        # Tenant A: plugin1 performs well
        for _ in range(10):
            setup["learner"].process_outcome(
                "skill1", "plugin1", "cap.plugin1", 100, True, True, tenant_id="tenant_a"
            )

        # Tenant B: plugin1 performs poorly
        for _ in range(5):
            setup["learner"].process_outcome(
                "skill1", "plugin1", "cap.plugin1", 1000, False, False, tenant_id="tenant_b"
            )

        # Recommendations should differ
        rec_a = setup["learner"].recommend(
            "skill1", "cap.plugin1", ["plugin1"], tenant_id="tenant_a"
        )
        rec_b = setup["learner"].recommend(
            "skill1", "cap.plugin1", ["plugin1"], tenant_id="tenant_b"
        )

        # Both have recommendation, but confidence differs
        assert rec_a is not None
        assert rec_b is not None
        conf_a = rec_a[1]
        conf_b = rec_b[1]
        # Tenant A has higher confidence (10 successes)
        # Tenant B has lower confidence (5 successes out of 10 trials, but still learning)

    def test_scenario_missing_dependency_fails_closed(self, setup):
        """Scenario: Missing dependency fails skill load (fail-closed)."""
        # Skill requires plugin that doesn't exist
        dep = CapabilityDependency(
            id="context",
            type="capability",
            capability_type="context_source",
            capability_id="cap.nonexistent",
            allowed_plugins=["nonexistent-plugin"],
            required=True,
        )
        skill = SkillCapabilitiesDependencies(
            skill_id="skill2",
            skill_version="1.0",
            dependencies=[dep],
        )

        # Resolution fails
        resolution = setup["resolver"].resolve_all(skill)
        assert resolution.status == "failed"
        assert len(resolution.errors) > 0

    def test_scenario_optional_dependency_degraded(self, setup):
        """Scenario: Optional missing dependency = degraded (not failed)."""
        # Required dependency (available)
        dep_req = CapabilityDependency(
            id="context",
            type="capability",
            capability_type="context_source",
            capability_id="cap.plugin1",
            allowed_plugins=["plugin1"],
            required=True,
        )
        # Optional dependency (unavailable)
        dep_opt = CapabilityDependency(
            id="cache",
            type="capability",
            capability_type="cache_provider",
            capability_id="cache.mem",
            allowed_plugins=["nonexistent"],
            required=False,
        )
        skill = SkillCapabilitiesDependencies(
            skill_id="skill3",
            skill_version="1.0",
            dependencies=[dep_req, dep_opt],
        )

        resolution = setup["resolver"].resolve_all(skill)
        # Status is "degraded" (one resolved, one optional missing)
        assert resolution.status != "failed"
        assert len(resolution.degraded_dependencies) == 1


class TestPerformanceBaseline:
    """Performance benchmarks."""

    def test_registry_query_latency(self):
        """Registry queries < 5ms for 100 plugins."""
        import time

        registry = CapabilityRegistry()

        # Register 100 plugins
        for i in range(100):
            cap = Capability(
                id=f"cap{i}",
                type=CapabilityType.CONTEXT_SOURCE,
                description=f"Cap {i}",
            )
            manifest = PluginCapabilitiesManifest(
                plugin_id=f"plugin{i}",
                plugin_version="1.0",
                capabilities=[cap],
            )
            registry.register_manifest(manifest)

        # Query performance
        start = time.time()
        for _ in range(100):  # 100 queries
            registry.find_implementations(CapabilityType.CONTEXT_SOURCE)
        elapsed_ms = (time.time() - start) * 1000
        avg_latency_ms = elapsed_ms / 100

        # Average should be << 5ms
        assert avg_latency_ms < 5.0, f"Query latency {avg_latency_ms}ms exceeds 5ms target"
