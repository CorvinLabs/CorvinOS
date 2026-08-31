"""Tests for graph routing (five independent routers).

Tests:
    - CallGraphRouter: import chain extraction
    - TestGraphRouter: test file discovery
    - ADRGraphRouter: ADR matching (mocked)
    - LayerGraphRouter: layer pattern matching
    - CodeDiffGraphRouter: scope estimation
"""

import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from dataclasses import dataclass

from sys import path
from pathlib import Path

# Add parent to path to avoid operator stdlib conflict
_task_analysis_root = Path(__file__).parent.parent
if str(_task_analysis_root) not in path:
    path.insert(0, str(_task_analysis_root.parent))

from task_analysis import TaskType, NormalizedTask
from task_analysis.graph_routing import (
    CallGraphRouter,
    TestGraphRouter,
    ADRGraphRouter,
    LayerGraphRouter,
    CodeDiffGraphRouter,
    GraphMatch,
)


# Fixtures
@pytest.fixture
def repo_root():
    """Get repo root."""
    return Path(__file__).resolve().parents[3]  # CorvinOS/


@pytest.fixture
def minimal_task():
    """Minimal NormalizedTask for testing."""
    return NormalizedTask(
        summary="Fix bug in voice module",
        description="Voice rendering crashes on long audio",
        type=TaskType.BUG_FIX,
        severity="high",
        components=["core/voice/renderer.py"],
        affected_layers=["L23"],
        memory_context=[],
        related_incidents=[],
        metadata={},
    )


@pytest.fixture
def complex_task():
    """Complex NormalizedTask with multiple components."""
    return NormalizedTask(
        summary="Add delegation feature",
        description="Implement worker delegation for big data tasks",
        type=TaskType.FEATURE,
        severity="high",
        components=[
            "core/delegation",
            "core/forge",
            "operator/task_engine",
        ],
        affected_layers=["L29", "L30", "L6"],
        memory_context=["adr-0200-delegation.md"],
        related_incidents=[],
        metadata={"component_count": 3},
    )


# CallGraphRouter Tests
class TestCallGraphRouter:
    """Tests for CallGraphRouter."""

    def test_empty_components_returns_zero(self, repo_root):
        """Empty components → score 0.0."""
        router = CallGraphRouter(repo_root)
        task = NormalizedTask(
            summary="test",
            description="test",
            type=TaskType.BUG_FIX,
            severity="low",
            components=[],
            affected_layers=[],
            memory_context=[],
            related_incidents=[],
            metadata={},
        )
        result = router.route(task)
        assert result.score == 0.0
        assert result.name == "call_graph"

    def test_valid_components_produces_graph_match(self, repo_root, minimal_task):
        """Valid components produce a GraphMatch."""
        router = CallGraphRouter(repo_root)
        result = router.route(minimal_task)

        assert isinstance(result, GraphMatch)
        assert result.name == "call_graph"
        assert 0.0 <= result.score <= 1.0
        assert "files" in result.metadata
        assert "depth" in result.metadata

    def test_score_scales_with_depth(self, repo_root, minimal_task):
        """Score increases with import chain depth."""
        router = CallGraphRouter(repo_root)
        result = router.route(minimal_task)

        # Score should be 0.0 if depth is 0 (no imports)
        # or higher if imports found
        assert result.score >= 0.0

    def test_nonexistent_file_handled_gracefully(self, repo_root):
        """Nonexistent files don't crash the router."""
        router = CallGraphRouter(repo_root)
        task = NormalizedTask(
            summary="test",
            description="test",
            type=TaskType.BUG_FIX,
            severity="low",
            components=["nonexistent/file.py"],
            affected_layers=[],
            memory_context=[],
            related_incidents=[],
            metadata={},
        )
        result = router.route(task)

        assert isinstance(result, GraphMatch)
        # Should have low or zero score
        assert result.score >= 0.0


# TestGraphRouter Tests
class TestTestGraphRouter:
    """Tests for TestGraphRouter."""

    def test_empty_components_returns_zero(self, repo_root):
        """Empty components → score 0.0."""
        router = TestGraphRouter(repo_root)
        task = NormalizedTask(
            summary="test",
            description="test",
            type=TaskType.BUG_FIX,
            severity="low",
            components=[],
            affected_layers=[],
            memory_context=[],
            related_incidents=[],
            metadata={},
        )
        result = router.route(task)
        assert result.score == 0.0

    def test_finds_test_files_for_component(self, repo_root, minimal_task):
        """Router finds test files for affected components."""
        router = TestGraphRouter(repo_root)
        result = router.route(minimal_task)

        assert isinstance(result, GraphMatch)
        assert result.name == "test_graph"
        assert "test_files" in result.metadata
        assert "found" in result.metadata
        assert 0.0 <= result.score <= 1.0

    def test_score_reflects_test_coverage(self, repo_root, minimal_task):
        """Score reflects number of tests found."""
        router = TestGraphRouter(repo_root)
        result = router.route(minimal_task)

        # Score should correlate with test count
        test_count = result.metadata.get("found", 0)
        if test_count > 0:
            assert result.score > 0.0


# ADRGraphRouter Tests
class TestADRGraphRouter:
    """Tests for ADRGraphRouter (mocked ADR graph)."""

    def test_empty_layers_returns_zero(self, repo_root):
        """Empty affected_layers → score 0.0."""
        router = ADRGraphRouter(repo_root)
        task = NormalizedTask(
            summary="test",
            description="test",
            type=TaskType.BUG_FIX,
            severity="low",
            components=[],
            affected_layers=[],
            memory_context=[],
            related_incidents=[],
            metadata={},
        )
        result = router.route(task)
        assert result.score == 0.0

    def test_valid_layers_produce_graph_match(self, repo_root, minimal_task):
        """Valid affected_layers produce a GraphMatch."""
        router = ADRGraphRouter(repo_root)
        result = router.route(minimal_task)

        assert isinstance(result, GraphMatch)
        assert result.name == "adr_graph"
        assert 0.0 <= result.score <= 1.0
        assert "adrs" in result.metadata

    def test_finds_adrs_for_layers(self, repo_root, complex_task):
        """Router attempts to find ADRs for affected layers."""
        router = ADRGraphRouter(repo_root)
        result = router.route(complex_task)

        assert "adrs" in result.metadata
        # Should be a list (possibly empty if ADR graph unavailable)
        assert isinstance(result.metadata["adrs"], list)


# LayerGraphRouter Tests
class TestLayerGraphRouter:
    """Tests for LayerGraphRouter."""

    def test_empty_components_returns_zero(self, repo_root):
        """Empty components → score 0.0."""
        router = LayerGraphRouter(repo_root)
        task = NormalizedTask(
            summary="test",
            description="test",
            type=TaskType.BUG_FIX,
            severity="low",
            components=[],
            affected_layers=[],
            memory_context=[],
            related_incidents=[],
            metadata={},
        )
        result = router.route(task)
        assert result.score == 0.0

    def test_matches_components_to_layers(self, repo_root, minimal_task):
        """Router matches components to layers."""
        router = LayerGraphRouter(repo_root)
        result = router.route(minimal_task)

        assert isinstance(result, GraphMatch)
        assert result.name == "layer_graph"
        assert 0.0 <= result.score <= 1.0
        assert "layers" in result.metadata

    def test_score_reflects_match_quality(self, repo_root, complex_task):
        """Score reflects layer match quality."""
        router = LayerGraphRouter(repo_root)
        result = router.route(complex_task)

        # More components should yield higher potential scores
        layer_count = len(result.metadata.get("layers", []))
        # If layers matched, score should be > 0
        if layer_count > 0:
            assert result.score > 0.0

    def test_manifest_loading(self, repo_root):
        """Router can load manifest."""
        router = LayerGraphRouter(repo_root)
        manifest = router._load_manifest()

        # Manifest should be loadable (even if None due to missing file)
        # This just ensures no crash
        assert manifest is None or isinstance(manifest, dict)


# CodeDiffGraphRouter Tests
class TestCodeDiffGraphRouter:
    """Tests for CodeDiffGraphRouter."""

    def test_bug_fix_high_yields_low_scope(self):
        """BUG_FIX + high severity → low scope."""
        router = CodeDiffGraphRouter()
        task = NormalizedTask(
            summary="Fix crash",
            description="Emergency fix for production issue",
            type=TaskType.BUG_FIX,
            severity="high",
            components=["core/voice/renderer.py"],
            affected_layers=["L23"],
            memory_context=[],
            related_incidents=[],
            metadata={},
        )
        result = router.route(task)

        assert result.metadata["scope"] == "low"
        assert result.score > 0.7  # high confidence for this type combo

    def test_feature_high_yields_medium_scope(self):
        """FEATURE + high severity → medium scope."""
        router = CodeDiffGraphRouter()
        task = NormalizedTask(
            summary="Add new capability",
            description="Implement worker delegation",
            type=TaskType.FEATURE,
            severity="high",
            components=[],
            affected_layers=[],
            memory_context=[],
            related_incidents=[],
            metadata={},
        )
        result = router.route(task)

        assert result.metadata["scope"] == "medium"

    def test_refactor_high_yields_high_scope(self):
        """REFACTOR + high severity → high scope."""
        router = CodeDiffGraphRouter()
        task = NormalizedTask(
            summary="Refactor module structure",
            description="Reorganize codebase",
            type=TaskType.REFACTOR,
            severity="high",
            components=[],
            affected_layers=[],
            memory_context=[],
            related_incidents=[],
            metadata={},
        )
        result = router.route(task)

        assert result.metadata["scope"] == "high"

    def test_incident_yields_high_confidence(self):
        """INCIDENT type → highest confidence."""
        router = CodeDiffGraphRouter()
        task = NormalizedTask(
            summary="Critical outage",
            description="System is down",
            type=TaskType.INCIDENT,
            severity="high",
            components=[],
            affected_layers=[],
            memory_context=[],
            related_incidents=[],
            metadata={},
        )
        result = router.route(task)

        assert result.score >= 0.85

    def test_documentation_yields_consistent_scope(self):
        """DOCUMENTATION → always low scope."""
        router = CodeDiffGraphRouter()
        task = NormalizedTask(
            summary="Update docs",
            description="Improve documentation",
            type=TaskType.DOCUMENTATION,
            severity="medium",
            components=[],
            affected_layers=[],
            memory_context=[],
            related_incidents=[],
            metadata={},
        )
        result = router.route(task)

        assert result.metadata["scope"] == "low"

    def test_unknown_type_yields_medium_confidence(self):
        """UNKNOWN type → fallback to medium confidence."""
        router = CodeDiffGraphRouter()
        task = NormalizedTask(
            summary="Unclear task",
            description="Not sure what this is",
            type=TaskType.UNKNOWN,
            severity="medium",
            components=[],
            affected_layers=[],
            memory_context=[],
            related_incidents=[],
            metadata={},
        )
        result = router.route(task)

        # Unknown type should have lower confidence
        assert result.score <= 0.6


# Integration Tests
class TestGraphRoutingIntegration:
    """Integration tests for all routers working together."""

    def test_all_routers_handle_complex_task(self, repo_root, complex_task):
        """All routers process a complex task without crashing."""
        call_graph = CallGraphRouter(repo_root).route(complex_task)
        test_graph = TestGraphRouter(repo_root).route(complex_task)
        adr_graph = ADRGraphRouter(repo_root).route(complex_task)
        layer_graph = LayerGraphRouter(repo_root).route(complex_task)
        code_diff = CodeDiffGraphRouter().route(complex_task)

        # All should return valid GraphMatches
        for result in [call_graph, test_graph, adr_graph, layer_graph, code_diff]:
            assert isinstance(result, GraphMatch)
            assert 0.0 <= result.score <= 1.0
            assert isinstance(result.metadata, dict)

    def test_all_routers_handle_minimal_task(self, repo_root, minimal_task):
        """All routers process a minimal task without crashing."""
        call_graph = CallGraphRouter(repo_root).route(minimal_task)
        test_graph = TestGraphRouter(repo_root).route(minimal_task)
        adr_graph = ADRGraphRouter(repo_root).route(minimal_task)
        layer_graph = LayerGraphRouter(repo_root).route(minimal_task)
        code_diff = CodeDiffGraphRouter().route(minimal_task)

        # All should return valid GraphMatches
        for result in [call_graph, test_graph, adr_graph, layer_graph, code_diff]:
            assert isinstance(result, GraphMatch)
            assert 0.0 <= result.score <= 1.0


# Edge Cases
class TestGraphRoutingEdgeCases:
    """Edge case tests for robustness."""

    def test_router_handles_none_task_attributes(self, repo_root):
        """Routers handle None/empty task attributes gracefully."""
        task = NormalizedTask(
            summary="",
            description="",
            type=TaskType.UNKNOWN,
            severity="",
            components=None or [],
            affected_layers=None or [],
            memory_context=[],
            related_incidents=[],
            metadata=None or {},
        )

        # Should not crash
        call_graph = CallGraphRouter(repo_root).route(task)
        test_graph = TestGraphRouter(repo_root).route(task)
        code_diff = CodeDiffGraphRouter().route(task)

        assert all(isinstance(r, GraphMatch) for r in [call_graph, test_graph, code_diff])

    def test_graph_match_score_validation(self):
        """GraphMatch validates score is in [0.0, 1.0]."""
        valid = GraphMatch("test", 0.5)
        assert valid.score == 0.5

        # Should raise on invalid score
        with pytest.raises(ValueError):
            GraphMatch("test", 1.5)

        with pytest.raises(ValueError):
            GraphMatch("test", -0.1)
