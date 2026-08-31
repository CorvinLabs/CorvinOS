"""Tests for data models in the Inspection Layer.

Unit tests for TaskNode, TaskGraph, ForgedSkillMetadata, ForgedToolMetadata,
SkillToolDependencyGraph, and category health metrics.

Test Strategy:
- Immutability: All data models are frozen dataclasses
- Tenant isolation: All models require tenant_id
- Graph properties: DAG algorithms (critical path, cycles, transitive closure)
- Metric aggregation: Error rates, latency percentiles, status calculation
"""

import pytest
from datetime import datetime, timedelta
from ..data_models import (
    TaskStatus,
    TaskNode,
    TaskGraph,
    ForgedSkillMetadata,
    ForgedToolMetadata,
    SkillToolDependencyGraph,
    CategoryStatus,
    CategoryHealthMetrics,
    ErrorPattern,
    EventSummary,
    ToolStatus,
)


class TestTaskNode:
    """Tests for TaskNode data model."""

    @pytest.fixture
    def sample_task(self):
        """Create a sample task for testing."""
        return TaskNode(
            task_id="task-001",
            name="Analyze module",
            status=TaskStatus.RUNNING,
            phase="analysis",
            iteration=1,
            parent_id=None,
            children_ids=[],
            dependencies=[],
            created_at=datetime.utcnow(),
            started_at=datetime.utcnow(),
            completed_at=None,
            estimated_duration=timedelta(minutes=5),
            actual_duration=None,
            error_message=None,
            owner="agent-1",
            tenant_id="tenant-default",
        )

    def test_task_immutability(self, sample_task):
        """TaskNode should be frozen and immutable."""
        with pytest.raises(AttributeError):
            sample_task.status = TaskStatus.DONE

    def test_task_duration_calculation(self, sample_task):
        """duration_ms() should return milliseconds or None."""
        # Task not yet completed
        assert sample_task.duration_ms() is None

        # Task with duration
        completed_task = TaskNode(
            task_id="task-002",
            name="Complete task",
            status=TaskStatus.DONE,
            phase="testing",
            iteration=1,
            parent_id=None,
            children_ids=[],
            dependencies=[],
            created_at=datetime.utcnow(),
            started_at=datetime.utcnow(),
            completed_at=datetime.utcnow() + timedelta(seconds=5),
            estimated_duration=None,
            actual_duration=timedelta(seconds=5),
            error_message=None,
            owner="agent-1",
            tenant_id="tenant-default",
        )
        assert 4900 < completed_task.duration_ms() < 5100  # ~5000ms with tolerance

    def test_task_status_checks(self, sample_task):
        """Task should provide helper methods for status checking."""
        assert not sample_task.is_blocked()
        assert not sample_task.is_terminal()

        blocked_task = sample_task.__class__(
            **{**sample_task.__dict__, "status": TaskStatus.BLOCKED}
        )
        assert blocked_task.is_blocked()

        done_task = sample_task.__class__(
            **{**sample_task.__dict__, "status": TaskStatus.DONE}
        )
        assert done_task.is_terminal()

    def test_task_with_error(self):
        """Task with FAILED status should include error message."""
        failed_task = TaskNode(
            task_id="task-fail",
            name="Failing task",
            status=TaskStatus.FAILED,
            phase="testing",
            iteration=1,
            parent_id=None,
            children_ids=[],
            dependencies=[],
            created_at=datetime.utcnow(),
            started_at=datetime.utcnow(),
            completed_at=datetime.utcnow(),
            estimated_duration=None,
            actual_duration=timedelta(seconds=1),
            error_message="AssertionError: test failed",
            owner="agent-1",
            tenant_id="tenant-default",
        )
        assert failed_task.error_message is not None
        assert "test failed" in failed_task.error_message


class TestTaskGraph:
    """Tests for TaskGraph DAG operations."""

    @pytest.fixture
    def linear_graph(self):
        """Create a linear dependency chain: A → B → C → D."""
        tasks = {
            "A": TaskNode(
                task_id="A",
                name="Task A",
                status=TaskStatus.DONE,
                phase="analysis",
                iteration=1,
                parent_id=None,
                children_ids=[],
                dependencies=[],
                created_at=datetime.utcnow(),
                started_at=datetime.utcnow(),
                completed_at=datetime.utcnow(),
                estimated_duration=None,
                actual_duration=timedelta(seconds=1),
                error_message=None,
                owner="agent-1",
                tenant_id="tenant-default",
            ),
            "B": TaskNode(
                task_id="B",
                name="Task B",
                status=TaskStatus.DONE,
                phase="implementation",
                iteration=1,
                parent_id=None,
                children_ids=[],
                dependencies=["A"],
                created_at=datetime.utcnow(),
                started_at=datetime.utcnow(),
                completed_at=datetime.utcnow(),
                estimated_duration=None,
                actual_duration=timedelta(seconds=1),
                error_message=None,
                owner="agent-1",
                tenant_id="tenant-default",
            ),
            "C": TaskNode(
                task_id="C",
                name="Task C",
                status=TaskStatus.RUNNING,
                phase="testing",
                iteration=1,
                parent_id=None,
                children_ids=[],
                dependencies=["B"],
                created_at=datetime.utcnow(),
                started_at=datetime.utcnow(),
                completed_at=None,
                estimated_duration=None,
                actual_duration=None,
                error_message=None,
                owner="agent-1",
                tenant_id="tenant-default",
            ),
            "D": TaskNode(
                task_id="D",
                name="Task D",
                status=TaskStatus.PENDING,
                phase="testing",
                iteration=1,
                parent_id=None,
                children_ids=[],
                dependencies=["C"],
                created_at=datetime.utcnow(),
                started_at=None,
                completed_at=None,
                estimated_duration=None,
                actual_duration=None,
                error_message=None,
                owner="agent-1",
                tenant_id="tenant-default",
            ),
        }
        return TaskGraph(tasks=tasks, tenant_id="tenant-default", session_id="session-1")

    @pytest.fixture
    def parallel_graph(self):
        """Create a graph with parallelism: A → (B, C), (B, C) → D."""
        tasks = {
            "A": TaskNode(
                task_id="A", name="Task A", status=TaskStatus.DONE,
                phase="analysis", iteration=1, parent_id=None, children_ids=[],
                dependencies=[], created_at=datetime.utcnow(), started_at=datetime.utcnow(),
                completed_at=datetime.utcnow(), estimated_duration=None,
                actual_duration=timedelta(seconds=1), error_message=None,
                owner="agent-1", tenant_id="tenant-default",
            ),
            "B": TaskNode(
                task_id="B", name="Task B", status=TaskStatus.DONE,
                phase="implementation", iteration=1, parent_id=None, children_ids=[],
                dependencies=["A"], created_at=datetime.utcnow(), started_at=datetime.utcnow(),
                completed_at=datetime.utcnow(), estimated_duration=None,
                actual_duration=timedelta(seconds=2), error_message=None,
                owner="agent-1", tenant_id="tenant-default",
            ),
            "C": TaskNode(
                task_id="C", name="Task C", status=TaskStatus.DONE,
                phase="implementation", iteration=1, parent_id=None, children_ids=[],
                dependencies=["A"], created_at=datetime.utcnow(), started_at=datetime.utcnow(),
                completed_at=datetime.utcnow(), estimated_duration=None,
                actual_duration=timedelta(seconds=3), error_message=None,
                owner="agent-1", tenant_id="tenant-default",
            ),
            "D": TaskNode(
                task_id="D", name="Task D", status=TaskStatus.PENDING,
                phase="testing", iteration=1, parent_id=None, children_ids=[],
                dependencies=["B", "C"], created_at=datetime.utcnow(), started_at=None,
                completed_at=None, estimated_duration=None, actual_duration=None,
                error_message=None, owner="agent-1", tenant_id="tenant-default",
            ),
        }
        return TaskGraph(tasks=tasks, tenant_id="tenant-default", session_id="session-2")

    def test_graph_immutability(self, linear_graph):
        """TaskGraph should be frozen and immutable."""
        with pytest.raises(AttributeError):
            linear_graph.tasks = {}

    def test_dag_construction(self, linear_graph):
        """get_dag() should build correct adjacency list."""
        dag = linear_graph.get_dag()
        assert dag["A"] == ["B"]
        assert dag["B"] == ["C"]
        assert dag["C"] == ["D"]
        assert dag["D"] == []

    def test_critical_path_linear(self, linear_graph):
        """Critical path in linear graph should be A → B → C → D."""
        critical = linear_graph.get_critical_path()
        assert len(critical) == 4
        assert [t.task_id for t in critical] == ["A", "B", "C", "D"]

    def test_critical_path_parallel(self, parallel_graph):
        """Critical path with parallelism should be A → C → D (longest branch)."""
        critical = parallel_graph.get_critical_path()
        task_ids = [t.task_id for t in critical]
        # C is slower (3s) than B (2s), so critical path is A → C → D
        assert task_ids == ["A", "C", "D"]

    def test_blocked_tasks(self, linear_graph):
        """Blocked tasks should be those with incomplete dependencies."""
        blocked = linear_graph.get_blocked_tasks()
        blocked_ids = {t.task_id for t in blocked}
        # C and D are blocked on incomplete dependencies
        assert "C" in blocked_ids
        assert "D" in blocked_ids
        # A and B are not blocked
        assert "A" not in blocked_ids
        assert "B" not in blocked_ids

    def test_get_dag_empty(self):
        """get_dag() on empty graph should work."""
        graph = TaskGraph(tasks={}, tenant_id="tenant-default", session_id="session-3")
        dag = graph.get_dag()
        assert dag == {}

    def test_critical_path_empty(self):
        """Critical path on empty graph should be empty."""
        graph = TaskGraph(tasks={}, tenant_id="tenant-default", session_id="session-4")
        critical = graph.get_critical_path()
        assert critical == []


class TestForgedSkillMetadata:
    """Tests for skill metadata."""

    @pytest.fixture
    def sample_skill(self):
        """Create a sample skill."""
        return ForgedSkillMetadata(
            skill_id="corvinOS_unified_context",
            name="Unified Context Bridge",
            version="1.0.0",
            created_at=datetime.utcnow(),
            last_used=datetime.utcnow() - timedelta(seconds=30),
            usage_count=1247,
            success_rate=0.982,
            avg_latency_ms=23.5,
            p95_latency_ms=45.2,
            p99_latency_ms=98.7,
            cost_estimate=1.5,
            depends_on_tools=["context_bus", "memory_store"],
            depends_on_skills=["learning_feedback", "audit_verify"],
            tags=["learning", "core"],
            owner="agent-1",
            tenant_id="tenant-default",
        )

    def test_skill_immutability(self, sample_skill):
        """Skill metadata should be frozen."""
        with pytest.raises(AttributeError):
            sample_skill.usage_count = 2000

    def test_skill_performance_check(self, sample_skill):
        """is_performant() should check P95 latency."""
        assert sample_skill.is_performant(p95_threshold_ms=100)
        assert not sample_skill.is_performant(p95_threshold_ms=40)

    def test_skill_reliability_check(self, sample_skill):
        """is_reliable() should check success rate."""
        assert sample_skill.is_reliable(success_threshold=0.98)
        assert not sample_skill.is_reliable(success_threshold=0.99)

    def test_skill_last_used_seconds_ago(self, sample_skill):
        """last_used_seconds_ago() should calculate time delta."""
        seconds_ago = sample_skill.last_used_seconds_ago()
        assert 25 < seconds_ago < 35  # ~30 seconds ago with tolerance

    def test_skill_never_used(self):
        """Skill with no last_used should return None."""
        skill = ForgedSkillMetadata(
            skill_id="test",
            name="Test Skill",
            version="0.1.0",
            created_at=datetime.utcnow(),
            last_used=None,
            usage_count=0,
            success_rate=0.0,
            avg_latency_ms=0.0,
            p95_latency_ms=0.0,
            p99_latency_ms=0.0,
            cost_estimate=0.0,
            depends_on_tools=[],
            depends_on_skills=[],
            tags=[],
            owner="test-owner",
            tenant_id="tenant-default",
        )
        assert skill.last_used_seconds_ago() is None


class TestSkillToolDependencyGraph:
    """Tests for dependency graph analysis."""

    @pytest.fixture
    def sample_graph(self):
        """Create a sample skill-tool dependency graph."""
        skills = {
            "skill_A": ForgedSkillMetadata(
                skill_id="skill_A", name="Skill A", version="1.0",
                created_at=datetime.utcnow(), last_used=None, usage_count=100,
                success_rate=0.95, avg_latency_ms=20, p95_latency_ms=50, p99_latency_ms=100,
                cost_estimate=1.0, depends_on_tools=["tool_1", "tool_2"],
                depends_on_skills=[], tags=[], owner="agent", tenant_id="tenant-default",
            ),
            "skill_B": ForgedSkillMetadata(
                skill_id="skill_B", name="Skill B", version="1.0",
                created_at=datetime.utcnow(), last_used=None, usage_count=50,
                success_rate=0.90, avg_latency_ms=30, p95_latency_ms=70, p99_latency_ms=150,
                cost_estimate=2.0, depends_on_tools=["tool_2"],
                depends_on_skills=["skill_A"], tags=[], owner="agent", tenant_id="tenant-default",
            ),
        }
        tools = {
            "tool_1": ForgedToolMetadata(
                tool_id="tool_1", name="Tool 1", implementation="mcp", version="1.0",
                created_at=datetime.utcnow(), last_used=None, usage_count=150,
                success_rate=1.0, avg_latency_ms=10, p95_latency_ms=20, avg_cost_per_call=0.5,
                used_by_skills=["skill_A"], used_by_tools=[], status=ToolStatus.AVAILABLE,
                tags=[], tenant_id="tenant-default",
            ),
            "tool_2": ForgedToolMetadata(
                tool_id="tool_2", name="Tool 2", implementation="http", version="1.0",
                created_at=datetime.utcnow(), last_used=None, usage_count=250,
                success_rate=0.98, avg_latency_ms=15, p95_latency_ms=40, avg_cost_per_call=0.8,
                used_by_skills=["skill_A", "skill_B"], used_by_tools=[], status=ToolStatus.AVAILABLE,
                tags=[], tenant_id="tenant-default",
            ),
        }
        return SkillToolDependencyGraph(skills=skills, tools=tools, tenant_id="tenant-default")

    def test_transitive_dependencies(self, sample_graph):
        """Transitive dependencies should include all tools used."""
        deps = sample_graph.get_transitive_dependencies("skill_B")
        # skill_B depends on skill_A, which depends on tool_1 and tool_2
        # skill_B also directly depends on tool_2
        assert "tool_1" in deps
        assert "tool_2" in deps

    def test_circular_dependency_none(self, sample_graph):
        """Acyclic graph should have no circular dependencies."""
        cycles = sample_graph.find_circular_dependencies()
        assert len(cycles) == 0

    def test_critical_tools(self, sample_graph):
        """Critical tools should be those with high usage."""
        critical = sample_graph.get_critical_tools(usage_threshold=100)
        # tool_2 has 250 uses, tool_1 has 150 uses
        assert critical == ["tool_2", "tool_1"]

    def test_critical_tools_high_threshold(self, sample_graph):
        """High threshold should filter out less-used tools."""
        critical = sample_graph.get_critical_tools(usage_threshold=200)
        assert critical == ["tool_2"]


class TestCategoryHealthMetrics:
    """Tests for category health metrics."""

    @pytest.fixture
    def healthy_category(self):
        """Create metrics for a healthy category."""
        return CategoryHealthMetrics(
            category="learning",
            event_count=1234,
            error_count=12,
            error_rate=0.0097,
            avg_latency_ms=22.5,
            p50_latency_ms=18.0,
            p95_latency_ms=45.2,
            p99_latency_ms=98.7,
            max_latency_ms=234.0,
            subcategories={"learning:confidence": 423, "learning:feedback": 567},
            recent_events=[],
            error_patterns=[],
            status=CategoryStatus.HEALTHY,
            tenant_id="tenant-default",
            timestamp=datetime.utcnow(),
        )

    def test_category_status_checks(self, healthy_category):
        """Category should provide status helper methods."""
        assert healthy_category.is_healthy()
        assert not healthy_category.is_degraded()
        assert not healthy_category.is_critical()

    def test_degraded_category(self):
        """Category with error rate > 5% should be degraded."""
        degraded = CategoryHealthMetrics(
            category="audit",
            event_count=100,
            error_count=10,
            error_rate=0.10,
            avg_latency_ms=50.0,
            p50_latency_ms=40.0,
            p95_latency_ms=80.0,
            p99_latency_ms=150.0,
            max_latency_ms=500.0,
            subcategories={},
            recent_events=[],
            error_patterns=[],
            status=CategoryStatus.DEGRADED,
            tenant_id="tenant-default",
            timestamp=datetime.utcnow(),
        )
        assert degraded.is_degraded()
        assert not degraded.is_healthy()

    def test_critical_category(self):
        """Category with very high error rate should be critical."""
        critical = CategoryHealthMetrics(
            category="core",
            event_count=100,
            error_count=50,
            error_rate=0.50,
            avg_latency_ms=500.0,
            p50_latency_ms=400.0,
            p95_latency_ms=1200.0,
            p99_latency_ms=2000.0,
            max_latency_ms=5000.0,
            subcategories={},
            recent_events=[],
            error_patterns=[],
            status=CategoryStatus.CRITICAL,
            tenant_id="tenant-default",
            timestamp=datetime.utcnow(),
        )
        assert critical.is_critical()
        assert not critical.is_healthy()

    def test_category_immutability(self, healthy_category):
        """Category metrics should be frozen."""
        with pytest.raises(AttributeError):
            healthy_category.error_rate = 0.5


class TestTenantIsolation:
    """Tests for tenant isolation across all models."""

    def test_task_node_tenant_isolation(self):
        """TaskNode must have tenant_id."""
        task = TaskNode(
            task_id="test", name="Test", status=TaskStatus.PENDING,
            phase="test", iteration=1, parent_id=None, children_ids=[],
            dependencies=[], created_at=datetime.utcnow(), started_at=None,
            completed_at=None, estimated_duration=None, actual_duration=None,
            error_message=None, owner="test", tenant_id="tenant-1",
        )
        assert task.tenant_id == "tenant-1"

    def test_skill_tenant_isolation(self):
        """ForgedSkillMetadata must have tenant_id."""
        skill = ForgedSkillMetadata(
            skill_id="test", name="Test", version="1.0",
            created_at=datetime.utcnow(), last_used=None, usage_count=0,
            success_rate=0.0, avg_latency_ms=0, p95_latency_ms=0, p99_latency_ms=0,
            cost_estimate=0, depends_on_tools=[], depends_on_skills=[],
            tags=[], owner="test", tenant_id="tenant-2",
        )
        assert skill.tenant_id == "tenant-2"

    def test_tool_tenant_isolation(self):
        """ForgedToolMetadata must have tenant_id."""
        tool = ForgedToolMetadata(
            tool_id="test", name="Test", implementation="mcp", version="1.0",
            created_at=datetime.utcnow(), last_used=None, usage_count=0,
            success_rate=0.0, avg_latency_ms=0, p95_latency_ms=0, avg_cost_per_call=0,
            used_by_skills=[], used_by_tools=[], status=ToolStatus.AVAILABLE,
            tags=[], tenant_id="tenant-3",
        )
        assert tool.tenant_id == "tenant-3"

    def test_category_tenant_isolation(self):
        """CategoryHealthMetrics must have tenant_id."""
        metrics = CategoryHealthMetrics(
            category="test", event_count=0, error_count=0, error_rate=0.0,
            avg_latency_ms=0, p50_latency_ms=0, p95_latency_ms=0, p99_latency_ms=0,
            max_latency_ms=0, subcategories={}, recent_events=[],
            error_patterns=[], status=CategoryStatus.HEALTHY,
            tenant_id="tenant-4", timestamp=datetime.utcnow(),
        )
        assert metrics.tenant_id == "tenant-4"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
