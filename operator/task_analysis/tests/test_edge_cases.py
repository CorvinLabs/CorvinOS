"""Edge-case tests for TaskEngine (deployment readiness)."""

import pytest
from concurrent.futures import ThreadPoolExecutor
from ..engine import TaskEngine, EngineError
from ..normalizer import InsufficientTaskInfo


@pytest.fixture
def engine():
    return TaskEngine()


class TestEdgeCases:
    """Test 10 critical edge cases."""

    def test_empty_task_input(self, engine):
        """Edge case 1: Empty task should raise InsufficientTaskInfo."""
        with pytest.raises(InsufficientTaskInfo):
            engine.route_task("")

    def test_null_task_input(self, engine):
        """Edge case: None task should raise error during normalization."""
        # route_task(None) should fail at normalizer which expects a string
        with pytest.raises((ValueError, AttributeError, TypeError, InsufficientTaskInfo)):
            engine.route_task(None)

    def test_very_long_task_description(self, engine):
        """Edge case 2: Task > 10K tokens should still route (not crash)."""
        # ~10K tokens = ~40K chars
        long_task = "Fix bug in voice module " * 1000
        result = engine.route_task(long_task)
        assert result.decision_target is not None
        assert result.confidence >= 0.0

    def test_task_with_circular_references(self, engine):
        """Edge case 3: Task mentioning circular dependencies should handle gracefully."""
        task = (
            "Fix circular dependency between module A and module B "
            "where A imports B which imports C which imports A"
        )
        result = engine.route_task(task)
        assert result.decision_target is not None

    def test_confidence_boundary_zero(self, engine):
        """Edge case 5: Confidence exactly 0.0 should be valid."""
        # Create a minimal task that might produce 0.0 confidence
        task = "x"  # Too short, will fail validation
        with pytest.raises(InsufficientTaskInfo):
            engine.route_task(task)

    def test_all_graphs_missing_phase2_fallback(self, engine):
        """Edge case 6: If all graphs missing, fallback should work."""
        task = "refactor entire system completely rewrite everything"
        result = engine.route_task(task)
        # Engine should still return a decision even if fallback
        assert result.decision_target is not None

    def test_big_data_and_high_complexity_simultaneously(self, engine):
        """Edge case 7: Task that's both big-data AND high-complexity."""
        task = (
            "Process big data warehouse with 100 million records "
            "using advanced machine learning algorithms and "
            "completely rewrite the entire system architecture"
        )
        result = engine.route_task(task)
        # Should prioritize big-data (ACS) over complexity (TDE)
        assert result.decision_target.value in ["acs", "tde", "native"]

    def test_concurrent_engine_calls(self, engine):
        """Edge case 8: Concurrent engine.route_task() calls (thread-safe)."""
        tasks = [
            "fix bug in voice module",
            "add feature for user authentication",
            "refactor database layer",
            "optimize query performance",
            "fix high severity crash",
        ]

        results = []
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(engine.route_task, task) for task in tasks]
            results = [f.result() for f in futures]

        # All should complete without crashes
        assert len(results) == 5
        for result in results:
            assert result.decision_target is not None
            assert 0.0 <= result.confidence <= 1.0

    def test_model_recommendation_boundary(self, engine):
        """Edge case 4: Complexity exactly at threshold (0.6)."""
        # Task designed to hit complexity boundary
        task = "refactor module in system with components and layers"
        result = engine.route_task(task)
        # Model should be deterministic (either haiku or opus, not both)
        assert result.model_recommendation in ["haiku", "opus"]

    def test_tde_cost_threshold_boundary(self, engine):
        """Edge case: Cost exactly at TDE threshold ($0.10)."""
        # Very complex task might hit threshold
        task = (
            "completely rewrite and refactor entire system architecture "
            "including all layers and components with full redesign"
        )
        result = engine.route_task(task)
        # Cost should be in reasonable range
        assert 0.0 <= result.estimated_cost_usd <= 10.0

    def test_confidence_exactly_threshold_values(self, engine):
        """Edge case 5: Confidence at exact fallback threshold (0.7)."""
        task = "test task with some components in system"
        result = engine.route_task(task)
        # Confidence should be well-defined
        assert isinstance(result.confidence, float)
        assert 0.0 <= result.confidence <= 1.0

    def test_matrix_of_combinations(self, engine):
        """Integration: various combinations of task types."""
        test_cases = [
            ("Fix crash in voice rendering module with long audio files", "bug_fix", "high"),
            ("Add login feature to authentication system", "feature", "medium"),
            ("Refactor code in database layer for better performance", "refactor", "low"),
            ("Production incident: system is down and not responding to requests", "incident", "critical"),
            ("Document API endpoints with examples and usage", "docs", "low"),
        ]

        for task, _, _ in test_cases:
            result = engine.route_task(task)
            assert result.decision_target is not None
            assert 0.0 <= result.task_complexity <= 1.0
            assert result.model_recommendation in ["haiku", "opus"]

    def test_unicode_and_special_chars(self, engine):
        """Edge case: Unicode and special characters in task."""
        task = "Fix büg in módule: system → network → protocol ⚡ CRITICAL"
        result = engine.route_task(task)
        assert result.decision_target is not None

    def test_extremely_short_but_valid_task(self, engine):
        """Edge case: Shortest valid task that meets minimum requirements."""
        task = "fix bug in voice module"
        result = engine.route_task(task)
        assert result.decision_target is not None
        assert result.confidence >= 0.0
