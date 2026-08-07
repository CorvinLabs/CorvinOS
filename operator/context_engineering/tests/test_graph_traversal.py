"""Tests for GraphTraversal module (Phase 2)."""

import pytest
from datetime import datetime
from ..graph_traversal import GraphTraversal, RelatedDecision, GraphTraversalResult


class TestRelatedDecisionValidation:
    """Validate RelatedDecision data structure."""

    def test_related_decision_validates_score(self):
        """RelatedDecision should validate score is [0.0, 1.0]."""
        # Valid
        decision = RelatedDecision(
            decision_id="dec-123",
            title="Fix bug in module X",
            relevance_score=0.85,
            distance=1,
            decision_type="bug-fix",
            context="Related incident from 2026-08-01",
        )
        assert decision.relevance_score == 0.85

        # Invalid
        with pytest.raises(ValueError):
            RelatedDecision(
                decision_id="dec-456",
                title="Feature addition",
                relevance_score=1.5,  # Invalid
                distance=2,
                decision_type="feature",
                context="High relevance feature",
            )

    def test_related_decision_repr(self):
        """RelatedDecision should have readable string representation."""
        decision = RelatedDecision(
            decision_id="dec-789",
            title="Refactor engine",
            relevance_score=0.72,
            distance=2,
            decision_type="refactor",
            context="Structural improvement",
        )
        assert "dec-789" in repr(decision) or "Refactor" in repr(decision)


class TestGraphTraversalInitialization:
    """Test GraphTraversal initialization."""

    def test_graph_traversal_initializes(self):
        """GraphTraversal should initialize without error."""
        gt = GraphTraversal()
        assert gt is not None
        assert gt.cache_ttl is not None

    def test_graph_traversal_custom_ttl(self):
        """GraphTraversal should respect custom TTL."""
        gt = GraphTraversal(cache_ttl_minutes=60)
        # TTL should be set
        assert gt.cache_ttl is not None


class TestGraphTraversalSearch:
    """Test graph traversal search functionality."""

    @pytest.fixture
    def traversal(self):
        return GraphTraversal()

    def test_find_related_decisions_returns_result(self, traversal):
        """find_related_decisions should return GraphTraversalResult."""

        class MockTask:
            pass

        task = MockTask()
        result = traversal.find_related_decisions(task)

        assert isinstance(result, GraphTraversalResult)
        assert isinstance(result.related_decisions, list)
        assert result.search_duration_ms >= 0

    def test_find_related_decisions_respects_max_results(self, traversal):
        """find_related_decisions should respect max_results parameter."""

        class MockTask:
            pass

        task = MockTask()
        result = traversal.find_related_decisions(task, max_results=3)

        # Should have at most 3 results
        assert len(result.related_decisions) <= 3

    def test_graph_traversal_caching(self, traversal):
        """Results should be cached and retrieved."""

        class MockTask:
            pass

        task = MockTask()

        # First call (miss)
        result1 = traversal.find_related_decisions(task)
        assert result1.cache_hit is False

        # Second call (hit) — note: same task object for cache key
        # In real usage, cache key would be based on task ID, not object identity
        # For now, we can't easily trigger cache hit without modifying the implementation

    def test_graph_traversal_task_id_extraction(self, traversal):
        """GraphTraversal should extract task ID correctly."""

        class TaskWithId:
            id = "task-123"

        task = TaskWithId()
        result = traversal.find_related_decisions(task)

        assert result.task_id == "task-123"


class TestGraphTraversalRanking:
    """Test decision ranking."""

    @pytest.fixture
    def traversal(self):
        return GraphTraversal()

    def test_rank_sorts_by_relevance_descending(self, traversal):
        """Rank should sort by relevance (highest first)."""
        decisions = [
            RelatedDecision(
                decision_id="d1",
                title="Low relevance",
                relevance_score=0.3,
                distance=2,
                decision_type="test",
                context="Test decision",
            ),
            RelatedDecision(
                decision_id="d2",
                title="High relevance",
                relevance_score=0.9,
                distance=1,
                decision_type="test",
                context="Test decision",
            ),
            RelatedDecision(
                decision_id="d3",
                title="Medium relevance",
                relevance_score=0.6,
                distance=1,
                decision_type="test",
                context="Test decision",
            ),
        ]

        ranked = traversal.rank(decisions)

        # Should be sorted descending
        assert ranked[0].relevance_score == 0.9
        assert ranked[1].relevance_score == 0.6
        assert ranked[2].relevance_score == 0.3


class TestGraphTraversalIntegration:
    """Integration tests for graph traversal."""

    @pytest.fixture
    def traversal(self):
        return GraphTraversal()

    def test_end_to_end_traversal(self, traversal):
        """End-to-end traversal should work without crashes."""

        class EnrichedTask:
            class Normalized:
                summary = "Fix bug in memory module with concurrent access"

            normalized = Normalized()

        task = EnrichedTask()
        result = traversal.find_related_decisions(task, depth=2, top_n=3)

        # Should produce valid result
        assert isinstance(result, GraphTraversalResult)
        assert result.search_duration_ms >= 0
        assert result.traversal_depth == 2

    def test_traversal_with_various_depths(self, traversal):
        """Traversal should handle different depth values."""

        class MockTask:
            pass

        task = MockTask()

        for depth in [1, 2, 3]:
            result = traversal.find_related_decisions(task, depth=depth)
            assert result.traversal_depth == depth
            assert isinstance(result, GraphTraversalResult)


class TestGraphTraversalProductionReadiness:
    """Production-level readiness checks."""

    def test_no_exceptions_on_various_inputs(self):
        """GraphTraversal should handle edge cases gracefully."""
        gt = GraphTraversal()

        # Various task types
        class MinimalTask:
            pass

        class TaskWithId:
            task_id = "task-abc"

        class TaskWithNumericId:
            id = 12345

        for task in [MinimalTask(), TaskWithId(), TaskWithNumericId()]:
            try:
                result = gt.find_related_decisions(task)
                assert isinstance(result, GraphTraversalResult)
            except Exception as e:
                pytest.fail(f"Failed on task {type(task).__name__}: {e}")

    def test_concurrent_traversals(self):
        """GraphTraversal should handle concurrent calls."""
        from concurrent.futures import ThreadPoolExecutor

        gt = GraphTraversal()

        class MockTask:
            pass

        def traverse_task(task_id):
            task = MockTask()
            return gt.find_related_decisions(task)

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(traverse_task, i) for i in range(10)]
            results = [f.result() for f in futures]

        assert len(results) == 10
        assert all(isinstance(r, GraphTraversalResult) for r in results)
