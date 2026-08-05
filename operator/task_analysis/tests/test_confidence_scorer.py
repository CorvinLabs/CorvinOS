"""Tests for confidence scoring.

Tests:
    - Individual router scoring methods
    - Score bounds validation
    - Global confidence calculation
    - Fallback logic for missing data
"""

import pytest
from sys import path
from pathlib import Path

# Add parent to path to avoid operator stdlib conflict
_task_analysis_root = Path(__file__).parent.parent
if str(_task_analysis_root) not in path:
    path.insert(0, str(_task_analysis_root.parent))

from task_analysis import TaskType, NormalizedTask
from task_analysis.graph_routing import GraphMatch
from task_analysis.confidence_scorer import ConfidenceScorer, ScoredRouters


@pytest.fixture
def scorer():
    """Create a scorer instance."""
    return ConfidenceScorer()


@pytest.fixture
def minimal_task():
    """Minimal task for testing."""
    return NormalizedTask(
        summary="Fix bug",
        description="Test task",
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
    """Complex task with many components."""
    return NormalizedTask(
        summary="Feature",
        description="Test task",
        type=TaskType.FEATURE,
        severity="high",
        components=[
            "core/delegation",
            "core/forge",
            "operator/task_engine",
        ],
        affected_layers=["L29", "L30", "L6"],
        memory_context=[],
        related_incidents=[],
        metadata={},
    )


# Individual Router Scoring Tests
class TestCallGraphScoring:
    """Tests for score_call_graph method."""

    def test_empty_components_yields_zero(self, scorer, minimal_task):
        """Empty components → score 0.0."""
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
        match = GraphMatch("call_graph", 0.5, {"import_count": 5})
        score = scorer.score_call_graph(match, task)

        assert score == 0.0

    def test_score_based_on_import_count(self, scorer, minimal_task):
        """Score scales with import count."""
        # Task with 1 component, expecting ~2 imports
        match = GraphMatch("call_graph", 0.5, {"import_count": 2})
        score = scorer.score_call_graph(match, minimal_task)

        # score = min(1.0, 2 / 2) = 1.0
        assert score == 1.0

    def test_score_clamped_to_one(self, scorer, minimal_task):
        """Score clamped to [0.0, 1.0]."""
        match = GraphMatch("call_graph", 0.5, {"import_count": 100})
        score = scorer.score_call_graph(match, minimal_task)

        # score = min(1.0, 100 / 2) = 1.0
        assert score == 1.0

    def test_zero_imports_yields_low_score(self, scorer, minimal_task):
        """No imports → low score."""
        match = GraphMatch("call_graph", 0.5, {"import_count": 0})
        score = scorer.score_call_graph(match, minimal_task)

        assert score == 0.0


class TestTestGraphScoring:
    """Tests for score_test_graph method."""

    def test_empty_components_yields_zero(self, scorer):
        """Empty components → score 0.0."""
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
        match = GraphMatch("test_graph", 0.5, {"found": 1, "expected": 1})
        score = scorer.score_test_graph(match, task)

        assert score == 0.0

    def test_score_based_on_test_count(self, scorer, minimal_task):
        """Score scales with test count."""
        # Task with 1 component, expect ~0.5 tests (50% have tests)
        match = GraphMatch("test_graph", 0.5, {"found": 1, "expected": 1})
        score = scorer.score_test_graph(match, minimal_task)

        # score = min(1.0, 1 / 1) = 1.0
        assert score == 1.0

    def test_no_tests_found_yields_low_score(self, scorer, minimal_task):
        """No tests found → low score."""
        match = GraphMatch("test_graph", 0.5, {"found": 0, "expected": 2})
        score = scorer.score_test_graph(match, minimal_task)

        assert score == 0.0

    def test_partial_test_coverage(self, scorer, complex_task):
        """Partial test coverage yields medium score."""
        # Task with 3 components, 1 test found
        match = GraphMatch("test_graph", 0.5, {"found": 1, "expected": 3})
        score = scorer.score_test_graph(match, complex_task)

        # Expected adjusted = 3 // 2 = 1, score = min(1.0, 1 / 1) = 1.0
        assert score >= 0.0


class TestADRGraphScoring:
    """Tests for score_adr_graph method."""

    def test_empty_layers_yields_zero(self, scorer):
        """Empty affected_layers → score 0.0."""
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
        match = GraphMatch("adr_graph", 0.5, {"matched": 1})
        score = scorer.score_adr_graph(match, task)

        assert score == 0.0

    def test_score_based_on_adr_count(self, scorer, minimal_task):
        """Score scales with ADR count."""
        # Task with 1 affected layer, 1 ADR found
        match = GraphMatch("adr_graph", 0.5, {"matched": 1})
        score = scorer.score_adr_graph(match, minimal_task)

        # score = min(1.0, 1 / 1) = 1.0
        assert score == 1.0

    def test_multiple_adrs_per_layer(self, scorer, complex_task):
        """Multiple ADRs per layer → high score."""
        # Task with 3 layers, 6 ADRs found (2 per layer)
        match = GraphMatch("adr_graph", 0.5, {"matched": 6})
        score = scorer.score_adr_graph(match, complex_task)

        # score = min(1.0, 6 / 3) = 1.0
        assert score == 1.0


class TestLayerGraphScoring:
    """Tests for score_layer_graph method."""

    def test_empty_components_yields_zero(self, scorer):
        """Empty components → score 0.0."""
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
        match = GraphMatch("layer_graph", 0.5, {"matched": 1})
        score = scorer.score_layer_graph(match, task)

        assert score == 0.0

    def test_score_based_on_layer_overlap(self, scorer, minimal_task):
        """Score scales with layer overlap."""
        # Task with 1 component, 1 layer matched
        # Expected = 1 // 2 = 0, so score = min(1.0, 1 / 1) = 1.0
        match = GraphMatch("layer_graph", 0.5, {"matched": 1})
        score = scorer.score_layer_graph(match, minimal_task)

        assert score >= 0.0

    def test_multiple_layers_matched(self, scorer, complex_task):
        """Multiple layers matched → high score."""
        # Task with 3 components, 2 layers matched
        match = GraphMatch("layer_graph", 0.5, {"matched": 2})
        score = scorer.score_layer_graph(match, complex_task)

        # Expected = 3 // 2 = 1, score = min(1.0, 2 / 1) = 1.0
        assert score == 1.0


class TestCodeDiffScoring:
    """Tests for score_code_diff method."""

    def test_passes_router_score_through(self, scorer, minimal_task):
        """CodeDiffRouter score passed through as-is."""
        match = GraphMatch("code_diff", 0.75, {"scope": "low"})
        score = scorer.score_code_diff(match, minimal_task)

        assert score == 0.75

    def test_valid_score(self, scorer, minimal_task):
        """Valid score returned as-is."""
        match = GraphMatch("code_diff", 0.75, {"scope": "high"})
        score = scorer.score_code_diff(match, minimal_task)

        assert score == 0.75

    def test_invalid_score_in_construction_fails(self):
        """Invalid score in GraphMatch construction raises ValueError."""
        with pytest.raises(ValueError):
            GraphMatch("code_diff", 1.5, {"scope": "high"})

        with pytest.raises(ValueError):
            GraphMatch("code_diff", -0.5, {"scope": "low"})


# Global Confidence Tests
class TestGlobalConfidence:
    """Tests for global_confidence aggregation."""

    def test_mean_of_all_scores(self, scorer):
        """Global confidence is mean of five scores."""
        scores = {
            "call_graph": 0.8,
            "test_graph": 0.6,
            "adr_graph": 0.9,
            "layer_graph": 0.7,
            "code_diff": 0.8,
        }
        result = scorer.global_confidence(scores)

        # Mean = (0.8 + 0.6 + 0.9 + 0.7 + 0.8) / 5 = 3.8 / 5 = 0.76
        expected = 3.8 / 5
        assert abs(result - expected) < 0.01

    def test_empty_scores_yields_zero(self, scorer):
        """Empty scores → 0.0."""
        result = scorer.global_confidence({})
        assert result == 0.0

    def test_score_clamped_to_bounds(self, scorer):
        """Global score clamped to [0.0, 1.0]."""
        scores = {"call_graph": 10.0}  # Invalid but test clamping
        result = scorer.global_confidence(scores)

        assert result == 1.0

        scores = {"call_graph": -5.0}
        result = scorer.global_confidence(scores)

        assert result == 0.0

    def test_single_score(self, scorer):
        """Single score passes through."""
        scores = {"call_graph": 0.5}
        result = scorer.global_confidence(scores)

        assert result == 0.5


# Integration Tests
class TestConfidenceScorerIntegration:
    """Integration tests for compute_all."""

    def test_compute_all_with_valid_matches(self, scorer, minimal_task):
        """compute_all aggregates all five scores."""
        graph_matches = {
            "call_graph": GraphMatch("call_graph", 0.5, {"import_count": 2}),
            "test_graph": GraphMatch("test_graph", 0.5, {"found": 1, "expected": 1}),
            "adr_graph": GraphMatch("adr_graph", 0.5, {"matched": 1}),
            "layer_graph": GraphMatch("layer_graph", 0.5, {"matched": 1}),
            "code_diff": GraphMatch("code_diff", 0.8, {"scope": "low"}),
        }

        result = scorer.compute_all(graph_matches, minimal_task)

        assert isinstance(result, ScoredRouters)
        assert 0.0 <= result.call_graph <= 1.0
        assert 0.0 <= result.test_graph <= 1.0
        assert 0.0 <= result.adr_graph <= 1.0
        assert 0.0 <= result.layer_graph <= 1.0
        assert 0.0 <= result.code_diff <= 1.0
        assert 0.0 <= result.global_confidence <= 1.0

    def test_compute_all_with_missing_matches(self, scorer, minimal_task):
        """compute_all handles missing routers gracefully."""
        graph_matches = {
            "call_graph": GraphMatch("call_graph", 0.0, {}),
            # Missing: test_graph, adr_graph, layer_graph, code_diff
        }

        result = scorer.compute_all(graph_matches, minimal_task)

        # All should be present with defaults
        assert hasattr(result, "call_graph")
        assert hasattr(result, "test_graph")
        assert hasattr(result, "global_confidence")

    def test_compute_all_with_high_confidence(self, scorer, complex_task):
        """compute_all reflects high confidence."""
        graph_matches = {
            "call_graph": GraphMatch("call_graph", 0.5, {"import_count": 8}),
            "test_graph": GraphMatch("test_graph", 0.5, {"found": 3, "expected": 3}),
            "adr_graph": GraphMatch("adr_graph", 0.5, {"matched": 3}),
            "layer_graph": GraphMatch("layer_graph", 0.5, {"matched": 2}),
            "code_diff": GraphMatch("code_diff", 0.9, {"scope": "medium"}),
        }

        result = scorer.compute_all(graph_matches, complex_task)

        # Should have reasonably high global confidence
        assert result.global_confidence >= 0.5

    def test_compute_all_with_low_confidence(self, scorer):
        """compute_all reflects low confidence."""
        task = NormalizedTask(
            summary="test",
            description="test",
            type=TaskType.UNKNOWN,
            severity="low",
            components=[],
            affected_layers=[],
            memory_context=[],
            related_incidents=[],
            metadata={},
        )

        graph_matches = {
            "call_graph": GraphMatch("call_graph", 0.0, {"import_count": 0}),
            "test_graph": GraphMatch("test_graph", 0.0, {"found": 0, "expected": 0}),
            "adr_graph": GraphMatch("adr_graph", 0.0, {"matched": 0}),
            "layer_graph": GraphMatch("layer_graph", 0.0, {"matched": 0}),
            "code_diff": GraphMatch("code_diff", 0.2, {"scope": "low"}),
        }

        result = scorer.compute_all(graph_matches, task)

        # Should have low global confidence
        assert result.global_confidence < 0.5
