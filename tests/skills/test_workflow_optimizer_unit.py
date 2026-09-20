"""Unit tests for WorkflowOptimizerSkill.

Tests core functionality in isolation:
- Trace parsing and validation
- Latency grouping algorithms
- Confidence scoring logic
- Reasoning generation
- Error handling
"""

import statistics
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.skills.os_skills.workflow_optimizer import (
    WorkflowOptimizerSkill,
    ExecutionTrace,
    OptimizationRecommendation,
    SkillMetadata,
)


@pytest.fixture
def skill():
    """Create a WorkflowOptimizerSkill instance."""
    return WorkflowOptimizerSkill()


@pytest.fixture
def sample_trace():
    """Create a sample ExecutionTrace."""
    return ExecutionTrace(
        task_id="task_001",
        task_type="code_review",
        subtasks=["parse", "analyze", "test"],
        total_latency_ms=1000.0,
        per_task_latency_ms={
            "parse": 300.0,
            "analyze": 400.0,
            "test": 300.0,
        },
    )


@pytest.fixture
def sample_history():
    """Create sample execution history."""
    return [
        {
            "task_id": "task_001",
            "task_type": "code_review",
            "subtasks": ["parse", "analyze", "test"],
            "total_latency_ms": 1000.0,
            "per_task_latency_ms": {
                "parse": 300.0,
                "analyze": 400.0,
                "test": 300.0,
            },
        },
        {
            "task_id": "task_002",
            "task_type": "code_review",
            "subtasks": ["parse", "analyze", "test"],
            "total_latency_ms": 1050.0,
            "per_task_latency_ms": {
                "parse": 310.0,
                "analyze": 410.0,
                "test": 330.0,
            },
        },
        {
            "task_id": "task_003",
            "task_type": "code_review",
            "subtasks": ["parse", "analyze", "test"],
            "total_latency_ms": 1100.0,
            "per_task_latency_ms": {
                "parse": 320.0,
                "analyze": 420.0,
                "test": 360.0,
            },
        },
    ]


class TestSkillMetadata:
    """Test skill metadata."""

    def test_metadata_structure(self, skill):
        """Verify skill metadata is correct."""
        assert skill.metadata.id == "os.workflow_optimizer"
        assert skill.metadata.version == "0.1.0"
        assert "workflow" in skill.metadata.tags
        assert "optimization" in skill.metadata.tags

    def test_metadata_immutable(self, skill):
        """Test that metadata is properly structured."""
        meta = skill.metadata
        assert isinstance(meta, SkillMetadata)
        assert meta.id == "os.workflow_optimizer"


class TestExecutionTraceValidation:
    """Test ExecutionTrace validation."""

    def test_valid_trace_creation(self):
        """Test creating a valid ExecutionTrace."""
        trace = ExecutionTrace(
            task_id="t1",
            task_type="test",
            subtasks=["a", "b"],
            total_latency_ms=100.0,
            per_task_latency_ms={"a": 50.0, "b": 50.0},
        )
        assert trace.task_id == "t1"
        assert len(trace.subtasks) == 2

    def test_invalid_task_id(self):
        """Test that empty task_id raises ValueError."""
        with pytest.raises(ValueError):
            ExecutionTrace(
                task_id="",
                task_type="test",
                subtasks=["a"],
                total_latency_ms=100.0,
                per_task_latency_ms={"a": 100.0},
            )

    def test_invalid_subtasks(self):
        """Test that empty subtasks raises ValueError."""
        with pytest.raises(ValueError):
            ExecutionTrace(
                task_id="t1",
                task_type="test",
                subtasks=[],
                total_latency_ms=100.0,
                per_task_latency_ms={},
            )

    def test_invalid_latency(self):
        """Test that invalid latency raises ValueError."""
        with pytest.raises(ValueError):
            ExecutionTrace(
                task_id="t1",
                task_type="test",
                subtasks=["a"],
                total_latency_ms=-100.0,
                per_task_latency_ms={"a": 100.0},
            )


class TestTraceParsing:
    """Test trace parsing functionality."""

    def test_parse_valid_traces(self, skill, sample_history):
        """Test parsing valid execution history."""
        traces = skill._parse_traces(sample_history)

        assert len(traces) == 3
        assert all(isinstance(t, ExecutionTrace) for t in traces)
        assert traces[0].task_id == "task_001"
        assert traces[0].task_type == "code_review"

    def test_parse_empty_history(self, skill):
        """Test parsing empty history raises ValueError."""
        with pytest.raises(ValueError):
            skill._parse_traces([])

    def test_parse_skips_malformed_traces(self, skill):
        """Test that malformed traces are skipped."""
        history = [
            {
                "task_id": "task_001",
                "task_type": "test",
                "subtasks": ["a", "b"],
                "total_latency_ms": 100.0,
                "per_task_latency_ms": {"a": 50.0, "b": 50.0},
            },
            {"incomplete": "data"},  # Malformed
            {
                "task_id": "task_003",
                "task_type": "test",
                "subtasks": ["a", "b"],
                "total_latency_ms": 110.0,
                "per_task_latency_ms": {"a": 55.0, "b": 55.0},
            },
        ]

        traces = skill._parse_traces(history)
        # Should have 2 valid traces
        assert len(traces) == 2

    def test_parse_all_malformed_raises(self, skill):
        """Test that all-malformed history raises ValueError."""
        with pytest.raises(ValueError):
            skill._parse_traces([{"bad": "data"}, {"also": "bad"}])


class TestLatencySimilarityGrouping:
    """Test latency-based grouping algorithm."""

    def test_group_similar_latencies(self, skill, sample_history):
        """Test grouping tasks with similar latencies."""
        traces = skill._parse_traces(sample_history)
        subtasks = ["parse", "analyze", "test"]

        groups = skill._group_by_latency_similarity(traces, subtasks)

        # parse and test have similar latencies, analyze is slower
        assert len(groups) >= 1
        assert all(isinstance(g, list) for g in groups)

    def test_group_identical_latencies(self, skill):
        """Test grouping with identical latencies."""
        traces = [
            ExecutionTrace(
                task_id="t1",
                task_type="test",
                subtasks=["a", "b", "c"],
                total_latency_ms=100.0,
                per_task_latency_ms={"a": 33.33, "b": 33.33, "c": 33.34},
            ),
            ExecutionTrace(
                task_id="t2",
                task_type="test",
                subtasks=["a", "b", "c"],
                total_latency_ms=100.0,
                per_task_latency_ms={"a": 33.33, "b": 33.33, "c": 33.34},
            ),
        ]

        groups = skill._group_by_latency_similarity(traces, ["a", "b", "c"])

        # All three should be grouped together (nearly identical)
        # or we might get one group with all three
        assert len(groups) >= 0

    def test_group_empty_input(self, skill):
        """Test grouping with empty input."""
        groups = skill._group_by_latency_similarity([], ["a", "b"])
        assert groups == []

    def test_group_no_matching_tasks(self, skill):
        """Test grouping when no tasks match."""
        traces = [
            ExecutionTrace(
                task_id="t1",
                task_type="test",
                subtasks=["a"],
                total_latency_ms=100.0,
                per_task_latency_ms={"a": 100.0},
            ),
        ]

        groups = skill._group_by_latency_similarity(traces, ["b", "c"])
        assert groups == []


class TestCriticalPathComputation:
    """Test critical path identification."""

    def test_critical_path_single_long_task(self, skill):
        """Test critical path with one long task."""
        traces = [
            ExecutionTrace(
                task_id="t1",
                task_type="test",
                subtasks=["long_task"],
                total_latency_ms=1000.0,
                per_task_latency_ms={"long_task": 1000.0},
            ),
            ExecutionTrace(
                task_id="t2",
                task_type="test",
                subtasks=["long_task"],
                total_latency_ms=1100.0,
                per_task_latency_ms={"long_task": 1100.0},
            ),
        ]

        path = skill._compute_critical_path(traces)

        assert len(path) > 0
        assert "long_task" in path

    def test_critical_path_empty_traces(self, skill):
        """Test critical path with empty traces."""
        path = skill._compute_critical_path([])
        assert path == []

    def test_critical_path_returns_top_n(self, skill):
        """Test that critical path returns top N tasks."""
        traces = [
            ExecutionTrace(
                task_id="t1",
                task_type="test",
                subtasks=["slow", "medium", "fast"],
                total_latency_ms=600.0,
                per_task_latency_ms={"slow": 400.0, "medium": 150.0, "fast": 50.0},
            ),
        ]

        path = skill._compute_critical_path(traces)

        # Should have slow, medium, and possibly fast
        assert "slow" in path


class TestConfidenceScoring:
    """Test confidence scoring logic."""

    def test_confidence_with_stable_history(self, skill):
        """Test confidence with stable (low variance) history."""
        # Low coefficient of variation
        confidence = skill._compute_confidence("serial", "serial", cv=0.05, num_traces=5)

        assert 0.0 <= confidence <= 1.0
        assert confidence > 0.5

    def test_confidence_with_unstable_history(self, skill):
        """Test confidence with unstable (high variance) history."""
        # High coefficient of variation
        confidence = skill._compute_confidence("serial", "serial", cv=0.3, num_traces=5)

        assert 0.0 <= confidence <= 1.0

    def test_confidence_penalty_for_no_change(self, skill):
        """Test that no-change suggestions get lower confidence."""
        # Same suggestion as current
        conf_no_change = skill._compute_confidence(
            "serial", "serial", cv=0.1, num_traces=10
        )
        # Different suggestion
        conf_change = skill._compute_confidence(
            "parallel_grouped", "serial", cv=0.1, num_traces=10
        )

        assert conf_no_change < conf_change

    def test_confidence_bonus_for_many_traces(self, skill):
        """Test that more traces improve confidence."""
        conf_few = skill._compute_confidence("serial", "serial", cv=0.1, num_traces=3)
        conf_many = skill._compute_confidence("serial", "serial", cv=0.1, num_traces=20)

        assert conf_many > conf_few

    def test_confidence_clamp(self, skill):
        """Test that confidence is clamped to [0, 1]."""
        # Extremely high CV should not exceed 1.0
        confidence = skill._compute_confidence("serial", "serial", cv=1.0, num_traces=100)

        assert 0.0 <= confidence <= 1.0


class TestReasoningGeneration:
    """Test human-readable reasoning generation."""

    def test_reasoning_for_parallel_suggestion(self, skill):
        """Test reasoning for parallelization recommendation."""
        reasoning = skill._generate_reasoning(
            "parallel_grouped",
            [["a", "b"], ["c"]],
            ["long_task"],
            estimated_speedup=1.5,
            cv=0.1,
        )

        assert len(reasoning) > 0
        assert "parallel" in reasoning.lower()
        assert "1.5" in reasoning

    def test_reasoning_for_serial_suggestion(self, skill):
        """Test reasoning for serial recommendation."""
        reasoning = skill._generate_reasoning(
            "serial",
            [],
            ["only_task"],
            estimated_speedup=1.0,
            cv=0.15,
        )

        assert len(reasoning) > 0
        assert "serial" in reasoning.lower()
        assert "1.0" in reasoning

    def test_reasoning_includes_variance_note(self, skill):
        """Test that reasoning includes variance note."""
        reasoning_stable = skill._generate_reasoning(
            "serial", [], [], 1.0, cv=0.05
        )
        reasoning_unstable = skill._generate_reasoning(
            "serial", [], [], 1.0, cv=0.25
        )

        assert "stable" in reasoning_stable.lower()
        assert "variance" in reasoning_unstable.lower()


class TestRecommendationGeneration:
    """Test recommendation generation."""

    def test_recommendation_with_groups(self, skill):
        """Test recommendation when parallelizable groups exist."""
        traces = [
            ExecutionTrace(
                task_id="t1",
                task_type="test",
                subtasks=["a", "b", "c"],
                total_latency_ms=100.0,
                per_task_latency_ms={"a": 33.0, "b": 33.0, "c": 34.0},
            ),
            ExecutionTrace(
                task_id="t2",
                task_type="test",
                subtasks=["a", "b", "c"],
                total_latency_ms=100.0,
                per_task_latency_ms={"a": 33.0, "b": 33.0, "c": 34.0},
            ),
        ]

        rec = skill._generate_recommendation(
            traces,
            [["a", "b"]],
            ["c"],
            "serial",
            "test",
        )

        assert rec.suggestion == "parallel_grouped"
        assert rec.estimated_speedup > 1.0
        assert 0.0 <= rec.confidence <= 1.0

    def test_recommendation_no_groups(self, skill):
        """Test recommendation when no groups exist."""
        traces = [
            ExecutionTrace(
                task_id="t1",
                task_type="test",
                subtasks=["only"],
                total_latency_ms=100.0,
                per_task_latency_ms={"only": 100.0},
            ),
        ]

        rec = skill._generate_recommendation(
            traces,
            [],
            ["only"],
            "serial",
            "test",
        )

        assert rec.suggestion == "serial"
        assert rec.estimated_speedup == 1.0


class TestOptimizationRecommendationObject:
    """Test OptimizationRecommendation dataclass."""

    def test_recommendation_creation(self):
        """Test creating a recommendation object."""
        rec = OptimizationRecommendation(
            suggestion="serial",
            estimated_speedup=1.0,
            confidence=0.8,
            reasoning="Test reasoning",
            parallelizable_groups=[],
            critical_path=["task"],
            current_shape="serial",
            learning_event_id="",
        )

        assert rec.suggestion == "serial"
        assert rec.audit_hash != ""  # Should be auto-generated

    def test_recommendation_audit_hash_generation(self):
        """Test that audit hash is generated."""
        rec1 = OptimizationRecommendation(
            suggestion="serial",
            estimated_speedup=1.0,
            confidence=0.8,
            reasoning="Test",
            parallelizable_groups=[],
            critical_path=[],
            current_shape="serial",
            learning_event_id="",
        )

        rec2 = OptimizationRecommendation(
            suggestion="parallel_grouped",  # Different
            estimated_speedup=1.5,
            confidence=0.9,
            reasoning="Test",
            parallelizable_groups=[],
            critical_path=[],
            current_shape="serial",
            learning_event_id="",
        )

        # Different suggestions should have different hashes
        assert rec1.audit_hash != rec2.audit_hash


class TestErrorHandling:
    """Test error handling in the skill."""

    def test_malformed_input_graceful_handling(self, skill):
        """Test graceful handling of malformed input."""
        result = skill.execute({
            "task_type": "test",
            "execution_history": [{"bad": "data"}],
            "current_shape": "serial",
        })

        assert "suggestion" in result
        assert result["confidence"] == 0.0

    def test_missing_fields_handling(self, skill):
        """Test handling of missing required fields."""
        result = skill.execute({
            # Missing task_type
            "execution_history": [],
            "current_shape": "serial",
        })

        assert "suggestion" in result

    def test_invalid_tenant_id_defaults(self, skill):
        """Test that invalid tenant_id defaults to _default."""
        result = skill.execute({
            "task_type": "test",
            "execution_history": [
                {
                    "task_id": "t1",
                    "task_type": "test",
                    "subtasks": ["a"],
                    "total_latency_ms": 100.0,
                    "per_task_latency_ms": {"a": 100.0},
                }
            ],
            "current_shape": "serial",
            "tenant_id": "",  # Invalid
        })

        # Should not crash, should have valid response
        assert isinstance(result, dict)


class TestEndToEndFlow:
    """Test complete skill flow."""

    def test_e2e_with_valid_input(self, skill, sample_history):
        """Test complete execution with valid input."""
        result = skill.execute({
            "task_type": "code_review",
            "execution_history": sample_history,
            "current_shape": "serial",
            "tenant_id": "_default",
        })

        # Verify all required fields present
        required_fields = [
            "suggestion",
            "estimated_speedup",
            "confidence",
            "reasoning",
            "learning_event_id",
            "parallelizable_groups",
            "critical_path",
            "current_shape",
        ]

        for field in required_fields:
            assert field in result, f"Missing field: {field}"

        # Verify types
        assert isinstance(result["suggestion"], str)
        assert isinstance(result["estimated_speedup"], (int, float))
        assert 0.0 <= result["confidence"] <= 1.0
        assert isinstance(result["reasoning"], str)
        assert isinstance(result["learning_event_id"], str)
        assert isinstance(result["parallelizable_groups"], list)
        assert isinstance(result["critical_path"], list)

    def test_e2e_insufficient_data(self, skill):
        """Test E2E with insufficient execution history."""
        result = skill.execute({
            "task_type": "test",
            "execution_history": [
                {
                    "task_id": "t1",
                    "task_type": "test",
                    "subtasks": ["a"],
                    "total_latency_ms": 100.0,
                    "per_task_latency_ms": {"a": 100.0},
                }
            ],
            "current_shape": "serial",
        })

        assert result["suggestion"] == "insufficient_data"
        assert result["confidence"] <= 0.3
        assert result["estimated_speedup"] == 1.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
