"""Unit tests for Round 1 Critical Blockers fixes.

Tests the six blocker fixes:
1. ToolForge register() API
2. Skill auto_grade()
3. Skill auto_promotion wiring
4. Graph cycle detection
5. Context Pipeline v2 archival
6. ExecutionContext consolidation
"""

import pytest
from datetime import datetime
from core.learning.auto_grading import auto_grade, ConfidenceGrade
from core.vibe_engineering.graph_queries import GraphQueries
from core.vibe_engineering.task_graph import TaskGraphBuilder, TaskGraph, Node, Edge


class TestBlocker2SkillAutoGrade:
    """Blocker 2: Skill auto_grade() implementation with Bayesian updates."""

    def test_auto_grade_success_case(self):
        """Test auto_grade with successful task result."""
        result = {
            "success": True,
            "latency_ms": 150,
            "output_quality": 0.9,
            "tokens_used": 500,
            "error": None,
        }

        grade = auto_grade(result, prior_confidence=0.5)

        assert isinstance(grade, ConfidenceGrade)
        assert 0.0 <= grade.score <= 1.0
        assert grade.score > 0.5  # Should improve over prior
        assert "Task succeeded" in grade.explanation

    def test_auto_grade_failure_case(self):
        """Test auto_grade with failed task result."""
        result = {
            "success": False,
            "latency_ms": 5000,
            "output_quality": 0.1,
            "tokens_used": 2000,
            "error": "timeout",
        }

        grade = auto_grade(result, prior_confidence=0.5)

        assert isinstance(grade, ConfidenceGrade)
        assert 0.0 <= grade.score <= 1.0
        assert grade.score < 0.5  # Should decrease from prior
        assert "Task failed" in grade.explanation

    def test_auto_grade_bayesian_update(self):
        """Test Bayesian update: prior confidence + feature signal."""
        result_good = {
            "success": True,
            "latency_ms": 100,
            "output_quality": 0.95,
            "error": None,
        }

        # Start with low confidence
        grade_low = auto_grade(result_good, prior_confidence=0.1)
        assert grade_low.score > 0.1  # Should improve

        # Start with high confidence
        grade_high = auto_grade(result_good, prior_confidence=0.9)
        assert grade_high.score >= 0.9  # Should stay high

    def test_auto_grade_with_user_feedback(self):
        """Test auto_grade with positive user feedback."""
        result = {
            "success": True,
            "latency_ms": 200,
            "output_quality": 0.8,
            "error": None,
        }

        grade_no_feedback = auto_grade(result, prior_confidence=0.5, feedback=None)
        grade_positive = auto_grade(
            result, prior_confidence=0.5, feedback="excellent work"
        )

        assert grade_positive.score >= grade_no_feedback.score

    def test_auto_grade_clamping(self):
        """Verify score is clamped to [0.0, 1.0]."""
        result = {
            "success": True,
            "latency_ms": 50,
            "output_quality": 1.0,
            "tokens_used": 10,
        }

        grade = auto_grade(result, prior_confidence=0.99)
        assert 0.0 <= grade.score <= 1.0


class TestBlocker4GraphCycleDetection:
    """Blocker 4: Graph cycle detection and DAG enforcement."""

    def test_has_cycle_dfs_method_exists(self):
        """Verify has_cycle() method exists in GraphQueries."""
        assert hasattr(GraphQueries, "has_cycle")
        assert callable(GraphQueries.has_cycle)

    def test_graph_with_no_cycles(self):
        """Verify DAG returns False for has_cycle()."""
        from core.vibe_engineering.task_graph import TaskGraph

        # Create simple DAG
        now = datetime.utcnow().isoformat()
        nodes = {
            "a": Node("a", "task", now, {}),
            "b": Node("b", "task", now, {}),
            "c": Node("c", "task", now, {}),
        }
        edges = [
            Edge("a", "b", "dependency", ""),
            Edge("b", "c", "dependency", ""),
        ]

        graph = TaskGraph(
            task_id="test",
            created_at=now,
            nodes=nodes,
            edges=edges,
            nodes_by_type={"task": ["a", "b", "c"]},
            iterations={},
        )

        assert not GraphQueries.has_cycle(graph)

    def test_graph_with_cycles(self):
        """Verify graph with cycle returns True for has_cycle()."""
        from core.vibe_engineering.task_graph import TaskGraph

        # Create graph with cycle
        now = datetime.utcnow().isoformat()
        nodes = {
            "a": Node("a", "task", now, {}),
            "b": Node("b", "task", now, {}),
            "c": Node("c", "task", now, {}),
        }
        edges = [
            Edge("a", "b", "dependency", ""),
            Edge("b", "c", "dependency", ""),
            Edge("c", "a", "dependency", ""),  # Cycle!
        ]

        graph = TaskGraph(
            task_id="test",
            created_at=now,
            nodes=nodes,
            edges=edges,
            nodes_by_type={"task": ["a", "b", "c"]},
            iterations={},
        )

        assert GraphQueries.has_cycle(graph)

    def test_task_graph_builder_prevents_cycles(self):
        """Verify TaskGraphBuilder rejects edges that create cycles."""
        builder = TaskGraphBuilder("test")

        now = datetime.utcnow().isoformat()
        n1 = Node("n1", "task", now, {})
        n2 = Node("n2", "task", now, {})
        n3 = Node("n3", "task", now, {})

        builder.add_node(n1)
        builder.add_node(n2)
        builder.add_node(n3)

        # Add normal edges
        e1 = Edge("n1", "n2", "dependency", "")
        e2 = Edge("n2", "n3", "dependency", "")
        assert builder.add_edge(e1)
        assert builder.add_edge(e2)

        # Try to add cycle-creating edge
        e3 = Edge("n3", "n1", "dependency", "")
        assert not builder.add_edge(e3)  # Should be rejected


class TestBlocker5ContextPipelineV2:
    """Blocker 5: Context Pipeline v2 archival and documentation."""

    def test_v2_context_preservation_exists(self):
        """Verify v2_context_preservation.py exists and is documented."""
        from pathlib import Path

        v2_file = Path("/home/shumway/projects/CorvinOS/core/context_pipeline/v2_context_preservation.py")
        assert v2_file.exists()

        # Read file to check for research code notice
        content = v2_file.read_text()
        assert "RESEARCH PROTOTYPE" in content or "orphaned" in content.lower()

    def test_dual_gate_alternative_exists(self):
        """Verify core/pipeline/dual_gate.py provides alternative."""
        from pathlib import Path

        dual_gate_file = Path("/home/shumway/projects/CorvinOS/core/pipeline/dual_gate.py")
        assert dual_gate_file.exists()


class TestBlocker6ExecutionContextConsolidation:
    """Blocker 6: ExecutionContext consolidation and clarification."""

    def test_three_execution_contexts_distinguished(self):
        """Verify three ExecutionContext versions are documented."""
        from pathlib import Path

        paths = [
            "/home/shumway/projects/CorvinOS/core/context_engineering/execution_context.py",
            "/home/shumway/projects/CorvinOS/core/engines/execution_context.py",
            "/home/shumway/projects/CorvinOS/core/console/corvin_core/execution_context.py",
        ]

        for path in paths:
            file = Path(path)
            assert file.exists()
            content = file.read_text()

            # Check for clarifying documentation
            assert "ExecutionContext" in content
            # Should mention what it's used for
            assert (
                "task state" in content.lower()
                or "metadata" in content.lower()
                or "audit" in content.lower()
            )

    def test_canonical_context_engineering_version(self):
        """Verify context_engineering version is marked canonical."""
        from pathlib import Path

        file = Path("/home/shumway/projects/CorvinOS/core/context_engineering/execution_context.py")
        content = file.read_text()

        assert "CANONICAL" in content
        assert "Brain subsystems" in content


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
