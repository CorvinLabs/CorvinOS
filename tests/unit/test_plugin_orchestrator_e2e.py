"""E2E tests for Plugin Orchestrator (ADR-0612)."""

import pytest

from core.plugins.corvin_plugins.capability_registry import CapabilityRegistry
from core.plugins.corvin_plugins.manifest_capabilities import (
    Capability,
    CapabilityType,
    PluginCapabilitiesManifest,
)
from core.skills.orchestration.orchestrator import PluginOrchestrator, SelectionMethod


@pytest.fixture
def setup_orchestrator():
    """Setup orchestrator with test plugins."""
    # Register plugins
    registry = CapabilityRegistry()

    cap = Capability(
        id="context.semantic",
        type=CapabilityType.CONTEXT_SOURCE,
        description="Semantic retrieval",
    )
    manifest = PluginCapabilitiesManifest(
        plugin_id="semantic-context",
        plugin_version="1.3.4",
        capabilities=[cap],
    )
    registry.register_manifest(manifest)

    # Create orchestrator (mock: would use real registry)
    orchestrator = PluginOrchestrator()
    orchestrator.registry = registry

    yield orchestrator


class TestPluginOrchestrator:
    """Test orchestration functionality."""

    def test_invoke_deterministic(self, setup_orchestrator):
        """Invoke with deterministic selection."""
        orchestrator = setup_orchestrator

        result = orchestrator.invoke_with_orchestration(
            skill_id="os.context_adapter",
            capability_id="context.semantic",
            input_data={"query": "test"},
            selection_method="deterministic",
            allowed_plugins=["semantic-context"],
        )

        assert result.status == "ok"
        assert result.plugin_id == "semantic-context"
        assert result.capability_id == "context.semantic"
        assert result.selection_method == "deterministic"
        assert result.latency_ms > 0

    def test_invoke_llm_guided(self, setup_orchestrator):
        """Invoke with LLM-guided selection."""
        orchestrator = setup_orchestrator

        result = orchestrator.invoke_with_orchestration(
            skill_id="os.context_adapter",
            capability_id="context.semantic",
            input_data={"query": "test"},
            selection_method="llm_guided",
            allowed_plugins=["semantic-context"],
        )

        assert result.status == "ok"
        assert result.plugin_id == "semantic-context"

    def test_invoke_learned(self, setup_orchestrator):
        """Invoke with learned selection."""
        orchestrator = setup_orchestrator

        result = orchestrator.invoke_with_orchestration(
            skill_id="os.context_adapter",
            capability_id="context.semantic",
            input_data={"query": "test"},
            selection_method="learned",
            allowed_plugins=["semantic-context"],
        )

        assert result.status == "ok"
        assert result.plugin_id == "semantic-context"

    def test_invoke_no_matching_plugin(self, setup_orchestrator):
        """Invocation fails when no plugin matches."""
        orchestrator = setup_orchestrator

        result = orchestrator.invoke_with_orchestration(
            skill_id="os.context_adapter",
            capability_id="context.semantic",
            input_data={"query": "test"},
            selection_method="deterministic",
            allowed_plugins=["nonexistent"],
        )

        assert result.status == "failed"
        assert result.error is not None

    def test_invocation_id_unique(self, setup_orchestrator):
        """Each invocation gets unique ID."""
        orchestrator = setup_orchestrator

        result1 = orchestrator.invoke_with_orchestration(
            skill_id="os.context_adapter",
            capability_id="context.semantic",
            input_data={"query": "test"},
            selection_method="deterministic",
            allowed_plugins=["semantic-context"],
        )
        result2 = orchestrator.invoke_with_orchestration(
            skill_id="os.context_adapter",
            capability_id="context.semantic",
            input_data={"query": "test"},
            selection_method="deterministic",
            allowed_plugins=["semantic-context"],
        )

        assert result1.invocation_id != result2.invocation_id
        assert result1.invocation_id.startswith("inv_")
        assert result2.invocation_id.startswith("inv_")
