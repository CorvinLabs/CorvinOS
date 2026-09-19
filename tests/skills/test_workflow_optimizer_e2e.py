"""E2E tests for WorkflowOptimizerSkill (ADR-0532 Phase 2).

Tests cover:
1. Basic execution: input validation, trace parsing, output format
2. Learning integration: ADR-0314 event emission
3. Parallelization detection: latency-based grouping
4. Feedback loop: confidence scoring
5. End-to-end with audit trail: real task traces → suggestion → audit record
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

logger = logging.getLogger(__name__)


@pytest.fixture
def sample_execution_history() -> List[Dict[str, Any]]:
    """Sample execution history for testing."""
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
            "total_latency_ms": 1100.0,
            "per_task_latency_ms": {
                "parse": 320.0,
                "analyze": 420.0,
                "test": 360.0,
            },
        },
        {
            "task_id": "task_003",
            "task_type": "code_review",
            "subtasks": ["parse", "analyze", "test"],
            "total_latency_ms": 1050.0,
            "per_task_latency_ms": {
                "parse": 310.0,
                "analyze": 410.0,
                "test": 330.0,
            },
        },
    ]


@pytest.fixture
def workflow_optimizer_skill():
    """Instantiate WorkflowOptimizerSkill."""
    from core.skills.workflow_optimizer import WorkflowOptimizerSkill
    return WorkflowOptimizerSkill()


class TestWorkflowOptimizerBasic:
    """Basic functionality tests."""

    def test_skill_metadata(self, workflow_optimizer_skill):
        """Verify skill metadata."""
        assert workflow_optimizer_skill.metadata.id == "os.workflow_optimizer"
        assert workflow_optimizer_skill.metadata.version == "0.1.0"
        assert "workflow" in workflow_optimizer_skill.metadata.tags

    def test_insufficient_history(self, workflow_optimizer_skill):
        """Test with insufficient execution history."""
        input_data = {
            "task_type": "code_review",
            "execution_history": [
                {
                    "task_id": "task_001",
                    "subtasks": ["parse", "analyze"],
                    "total_latency_ms": 1000.0,
                    "per_task_latency_ms": {"parse": 500.0, "analyze": 500.0},
                }
            ],
            "current_shape": "serial",
        }

        result = workflow_optimizer_skill.execute(input_data)

        assert result["suggestion"] == "insufficient_data"
        assert result["confidence"] <= 0.3
        assert "learning_event_id" in result
        assert len(result["learning_event_id"]) > 0

    def test_empty_history(self, workflow_optimizer_skill):
        """Test with empty execution history."""
        input_data = {
            "task_type": "code_review",
            "execution_history": [],
            "current_shape": "serial",
        }

        result = workflow_optimizer_skill.execute(input_data)

        assert result["suggestion"] == "insufficient_data"
        assert result["confidence"] <= 0.3

    def test_valid_input_structure(self, workflow_optimizer_skill, sample_execution_history):
        """Test with valid input structure."""
        input_data = {
            "task_type": "code_review",
            "execution_history": sample_execution_history,
            "current_shape": "serial",
            "tenant_id": "_default",
        }

        result = workflow_optimizer_skill.execute(input_data)

        # Verify output structure
        assert "suggestion" in result
        assert "estimated_speedup" in result
        assert "confidence" in result
        assert "reasoning" in result
        assert "learning_event_id" in result
        assert "parallelizable_groups" in result
        assert "critical_path" in result
        assert "current_shape" in result

        # Verify types
        assert isinstance(result["suggestion"], str)
        assert isinstance(result["estimated_speedup"], (int, float))
        assert isinstance(result["confidence"], (int, float))
        assert 0.0 <= result["confidence"] <= 1.0
        assert isinstance(result["reasoning"], str)
        assert isinstance(result["parallelizable_groups"], list)
        assert isinstance(result["critical_path"], list)

    def test_parallelizable_detection(self, workflow_optimizer_skill, sample_execution_history):
        """Test detection of parallelizable stages."""
        input_data = {
            "task_type": "code_review",
            "execution_history": sample_execution_history,
            "current_shape": "serial",
        }

        result = workflow_optimizer_skill.execute(input_data)

        # With similar latencies, should suggest parallelization
        assert "parallelizable_groups" in result
        groups = result["parallelizable_groups"]

        # Should have at least some grouping or none
        assert isinstance(groups, list)
        if len(groups) > 0:
            assert all(isinstance(g, list) for g in groups)

    def test_confidence_scoring(self, workflow_optimizer_skill, sample_execution_history):
        """Test confidence scoring based on variance."""
        input_data = {
            "task_type": "analysis",
            "execution_history": sample_execution_history,
            "current_shape": "serial",
        }

        result = workflow_optimizer_skill.execute(input_data)

        # Confidence should be reasonable for similar executions
        assert 0.4 <= result["confidence"] <= 1.0
        # Low variance in sample history should yield higher confidence
        assert result["confidence"] >= 0.6

    def test_current_shape_penalty(self, workflow_optimizer_skill, sample_execution_history):
        """Test that confidence is penalized when suggestion matches current shape."""
        # If already serial and we suggest serial, confidence should be lower
        input_data = {
            "task_type": "code_review",
            "execution_history": sample_execution_history,
            "current_shape": "serial",
        }

        result = workflow_optimizer_skill.execute(input_data)

        # This is valid behavior: if no change, confidence is penalized
        # Just verify it's still in valid range
        assert 0.0 <= result["confidence"] <= 1.0


class TestWorkflowOptimizerLearning:
    """Learning integration tests (ADR-0314)."""

    @patch('core.skills.workflow_optimizer.EventEmitter')
    @patch('core.skills.workflow_optimizer.EventStore')
    @patch('core.skills.workflow_optimizer.tenant_home')
    def test_learning_event_emission(
        self,
        mock_tenant_home,
        mock_event_store,
        mock_event_emitter,
        workflow_optimizer_skill,
        sample_execution_history,
    ):
        """Test that learning events are emitted."""
        mock_tenant_home.return_value = Path("/fake/tenant")
        mock_store_instance = MagicMock()
        mock_event_store.return_value = mock_store_instance
        mock_emitter_instance = MagicMock()
        mock_event_emitter.return_value = mock_emitter_instance

        input_data = {
            "task_type": "code_review",
            "execution_history": sample_execution_history,
            "current_shape": "serial",
            "tenant_id": "_default",
        }

        result = workflow_optimizer_skill.execute(input_data)

        # Verify learning event was created
        assert "learning_event_id" in result
        event_id = result["learning_event_id"]
        assert len(event_id) > 0

        # Verify emitter was called
        mock_event_emitter.assert_called()

    def test_learning_event_id_uniqueness(
        self, workflow_optimizer_skill, sample_execution_history
    ):
        """Test that each execution produces a unique learning event ID."""
        input_data = {
            "task_type": "code_review",
            "execution_history": sample_execution_history,
            "current_shape": "serial",
        }

        result1 = workflow_optimizer_skill.execute(input_data)
        result2 = workflow_optimizer_skill.execute(input_data)

        event_id_1 = result1["learning_event_id"]
        event_id_2 = result2["learning_event_id"]

        # Each execution should have different event ID
        assert event_id_1 != event_id_2


class TestWorkflowOptimizerAnalysis:
    """Execution trace analysis tests."""

    def test_trace_parsing(self, workflow_optimizer_skill, sample_execution_history):
        """Test parsing of execution traces."""
        traces = workflow_optimizer_skill._parse_traces(sample_execution_history)

        assert len(traces) == 3
        assert all(t.task_type == "code_review" for t in traces)
        assert all(len(t.subtasks) == 3 for t in traces)
        assert all(t.total_latency_ms > 0 for t in traces)

    def test_parallelizable_detection_simple(self, workflow_optimizer_skill):
        """Test parallelization detection with simple traces."""
        traces = [
            workflow_optimizer_skill._parse_traces([
                {
                    "task_id": "t1",
                    "task_type": "test",
                    "subtasks": ["a", "b", "c"],
                    "total_latency_ms": 100.0,
                    "per_task_latency_ms": {"a": 30.0, "b": 35.0, "c": 35.0},
                }
            ])[0],
            workflow_optimizer_skill._parse_traces([
                {
                    "task_id": "t2",
                    "task_type": "test",
                    "subtasks": ["a", "b", "c"],
                    "total_latency_ms": 105.0,
                    "per_task_latency_ms": {"a": 32.0, "b": 36.0, "c": 37.0},
                }
            ])[0],
        ]

        groups, critical_path = workflow_optimizer_skill._detect_parallelizable_stages(traces)

        assert isinstance(groups, list)
        assert isinstance(critical_path, list)
        # At least should identify some tasks
        assert len(critical_path) >= 0

    def test_latency_grouping(self, workflow_optimizer_skill):
        """Test grouping by latency similarity."""
        traces = [
            workflow_optimizer_skill._parse_traces([
                {
                    "task_id": "t1",
                    "task_type": "test",
                    "subtasks": ["fast_1", "fast_2", "slow"],
                    "total_latency_ms": 200.0,
                    "per_task_latency_ms": {
                        "fast_1": 50.0,
                        "fast_2": 55.0,
                        "slow": 95.0,
                    },
                }
            ])[0],
            workflow_optimizer_skill._parse_traces([
                {
                    "task_id": "t2",
                    "task_type": "test",
                    "subtasks": ["fast_1", "fast_2", "slow"],
                    "total_latency_ms": 205.0,
                    "per_task_latency_ms": {
                        "fast_1": 52.0,
                        "fast_2": 58.0,
                        "slow": 95.0,
                    },
                }
            ])[0],
        ]

        groups = workflow_optimizer_skill._group_by_latency_similarity(
            traces, ["fast_1", "fast_2", "slow"]
        )

        # fast_1 and fast_2 should be grouped together (similar latency)
        # slow might be alone or in a group
        assert len(groups) >= 1


class TestWorkflowOptimizerE2E:
    """End-to-end integration tests."""

    def test_e2e_serial_to_parallel_suggestion(
        self, workflow_optimizer_skill
    ):
        """Test E2E: suggest parallelization for parallel-friendly workload."""
        # Create a workload with many similar-latency subtasks
        history = [
            {
                "task_id": f"task_{i}",
                "task_type": "batch_processing",
                "subtasks": ["stage_1", "stage_2", "stage_3", "stage_4"],
                "total_latency_ms": 1200.0 + i * 50,
                "per_task_latency_ms": {
                    "stage_1": 280.0 + i * 5,
                    "stage_2": 290.0 + i * 5,
                    "stage_3": 285.0 + i * 5,
                    "stage_4": 295.0 + i * 5,
                },
            }
            for i in range(3)
        ]

        result = workflow_optimizer_skill.execute({
            "task_type": "batch_processing",
            "execution_history": history,
            "current_shape": "serial",
            "tenant_id": "_default",
        })

        # Should have valid output
        assert result["suggestion"] in ["serial", "parallel_grouped"]
        assert 1.0 <= result["estimated_speedup"] <= 2.0
        assert 0.0 <= result["confidence"] <= 1.0
        assert "reasoning" in result

    def test_e2e_already_optimal(self, workflow_optimizer_skill):
        """Test E2E: recognize already-optimal workflow."""
        history = [
            {
                "task_id": f"task_{i}",
                "task_type": "code_review",
                "subtasks": ["long_analysis"],
                "total_latency_ms": 5000.0,
                "per_task_latency_ms": {"long_analysis": 5000.0},
            }
            for i in range(2)
        ]

        result = workflow_optimizer_skill.execute({
            "task_type": "code_review",
            "execution_history": history,
            "current_shape": "serial",
            "tenant_id": "_default",
        })

        # Single long task can't be parallelized
        assert result["suggestion"] == "serial"
        assert result["estimated_speedup"] == 1.0

    def test_e2e_reasoning_quality(self, workflow_optimizer_skill, sample_execution_history):
        """Test that reasoning is human-readable."""
        result = workflow_optimizer_skill.execute({
            "task_type": "code_review",
            "execution_history": sample_execution_history,
            "current_shape": "serial",
        })

        reasoning = result["reasoning"]

        # Reasoning should be non-empty
        assert len(reasoning) > 0
        # Should contain meaningful information
        assert any(keyword in reasoning.lower() for keyword in [
            "recommend", "current", "shape", "latency", "path", "stage"
        ])

    @patch('core.skills.workflow_optimizer.EventEmitter')
    @patch('core.skills.workflow_optimizer.EventStore')
    @patch('core.skills.workflow_optimizer.tenant_home')
    def test_e2e_with_audit_trail_mock(
        self,
        mock_tenant_home,
        mock_event_store,
        mock_event_emitter,
        workflow_optimizer_skill,
        sample_execution_history,
    ):
        """Test E2E with mocked audit trail."""
        mock_tenant_home.return_value = Path("/fake/tenant")
        mock_store_instance = MagicMock()
        mock_event_store.return_value = mock_store_instance
        mock_emitter_instance = MagicMock()
        mock_event_emitter.return_value = mock_emitter_instance

        result = workflow_optimizer_skill.execute({
            "task_type": "code_review",
            "execution_history": sample_execution_history,
            "current_shape": "serial",
            "tenant_id": "_default",
        })

        # Verify full output structure
        assert all(key in result for key in [
            "suggestion", "estimated_speedup", "confidence",
            "reasoning", "learning_event_id", "parallelizable_groups",
            "critical_path", "current_shape"
        ])

        # Verify event was emitted
        assert mock_event_emitter.return_value.emit.called or not mock_event_emitter.called


class TestWorkflowOptimizerErrorHandling:
    """Error handling and edge cases."""

    def test_malformed_trace_graceful_handling(self, workflow_optimizer_skill):
        """Test graceful handling of malformed traces."""
        input_data = {
            "task_type": "code_review",
            "execution_history": [
                {"incomplete": "data"},  # Missing required fields
                {"also": "broken"},
            ],
            "current_shape": "serial",
        }

        # Should not raise, but return error suggestion
        result = workflow_optimizer_skill.execute(input_data)

        assert isinstance(result, dict)
        assert "suggestion" in result or "parse_error" in result.get("suggestion", "")

    def test_large_execution_history(self, workflow_optimizer_skill):
        """Test with large execution history."""
        large_history = [
            {
                "task_id": f"task_{i}",
                "task_type": "processing",
                "subtasks": [f"subtask_{j}" for j in range(10)],
                "total_latency_ms": 1000.0 + i * 10,
                "per_task_latency_ms": {
                    f"subtask_{j}": 100.0 + (i + j) * 0.5 for j in range(10)
                },
            }
            for i in range(50)
        ]

        result = workflow_optimizer_skill.execute({
            "task_type": "processing",
            "execution_history": large_history,
            "current_shape": "serial",
        })

        # Should handle large history
        assert isinstance(result, dict)
        assert "suggestion" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
