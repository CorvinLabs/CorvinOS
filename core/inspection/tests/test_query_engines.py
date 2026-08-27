"""Tests for query engines.

Tests for TaskGraphQuery, SkillToolQuery, and CategoryQuery. Covers:
- Tenant isolation (cross-tenant queries return empty)
- Query correctness (filtering, aggregation, analysis)
- Concurrency safety (multiple registrations)
- Edge cases (empty registries, invalid queries)

Strategy:
- Per-engine test class with fixtures for sample data
- Tenant isolation verified explicitly
- Error handling for invalid tenant_id
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
    ToolStatus,
    CategoryStatus,
    CategoryHealthMetrics,
    EventSummary,
)
from ..query_engine import (
    QueryEngine,
    TaskGraphQuery,
    SkillToolQuery,
    CategoryQuery,
)


class TestQueryEngineBase:
    """Tests for QueryEngine base class."""

    def test_query_engine_tenant_validation(self):
        """QueryEngine should validate tenant_id on init."""
        # Valid tenant
        query = TaskGraphQuery(tenant_id="tenant-1")
        assert query.tenant_id == "tenant-1"

        # Invalid: empty string
        with pytest.raises(ValueError):
            TaskGraphQuery(tenant_id="")

        # Invalid: None
        with pytest.raises(ValueError):
            TaskGraphQuery(tenant_id=None)

        # Invalid: non-string
        with pytest.raises(ValueError):
            TaskGraphQuery(tenant_id=123)


class TestTaskGraphQuery:
    """Tests for TaskGraphQuery."""

    @pytest.fixture
    def query_engine(self):
        """Create a TaskGraphQuery for testing."""
        return TaskGraphQuery(tenant_id="tenant-1")

    @pytest.fixture
    def sample_graph(self):
        """Create a sample task graph."""
        tasks = {
            "task-1": TaskNode(
                task_id="task-1", name="Analysis", status=TaskStatus.DONE,
                phase="analysis", iteration=1, parent_id=None, children_ids=[],
                dependencies=[], created_at=datetime.utcnow(),
                started_at=datetime.utcnow(), completed_at=datetime.utcnow(),
                estimated_duration=None, actual_duration=timedelta(seconds=10),
                error_message=None, owner="agent-1", tenant_id="tenant-1",
            ),
            "task-2": TaskNode(
                task_id="task-2", name="Implementation", status=TaskStatus.RUNNING,
                phase="implementation", iteration=1, parent_id=None, children_ids=[],
                dependencies=["task-1"], created_at=datetime.utcnow(),
                started_at=datetime.utcnow(), completed_at=None,
                estimated_duration=None, actual_duration=None,
                error_message=None, owner="agent-1", tenant_id="tenant-1",
            ),
            "task-3": TaskNode(
                task_id="task-3", name="Testing", status=TaskStatus.PENDING,
                phase="testing", iteration=1, parent_id=None, children_ids=[],
                dependencies=["task-2"], created_at=datetime.utcnow(),
                started_at=None, completed_at=None,
                estimated_duration=None, actual_duration=None,
                error_message=None, owner="agent-1", tenant_id="tenant-1",
            ),
        }
        return TaskGraph(tasks=tasks, tenant_id="tenant-1", session_id="session-1")

    def test_register_and_retrieve_graph(self, query_engine, sample_graph):
        """Should register and retrieve task graphs."""
        query_engine.register_task_graph("session-1", sample_graph)
        retrieved = query_engine.get_task_graph("session-1")
        assert retrieved is not None
        assert retrieved.session_id == "session-1"
        assert len(retrieved.tasks) == 3

    def test_register_graph_tenant_mismatch(self, query_engine, sample_graph):
        """Should reject graphs with mismatched tenant_id."""
        bad_graph = TaskGraph(
            tasks=sample_graph.tasks,
            tenant_id="tenant-2",  # Different tenant
            session_id="session-1",
        )
        with pytest.raises(ValueError, match="Tenant mismatch"):
            query_engine.register_task_graph("session-1", bad_graph)

    def test_get_nonexistent_graph(self, query_engine):
        """Should return None for nonexistent session."""
        assert query_engine.get_task_graph("nonexistent") is None

    def test_get_single_task(self, query_engine, sample_graph):
        """Should retrieve a single task by ID."""
        query_engine.register_task_graph("session-1", sample_graph)
        task = query_engine.get_task("session-1", "task-1")
        assert task is not None
        assert task.name == "Analysis"

    def test_get_nonexistent_task(self, query_engine, sample_graph):
        """Should return None for nonexistent task."""
        query_engine.register_task_graph("session-1", sample_graph)
        assert query_engine.get_task("session-1", "nonexistent") is None

    def test_critical_path(self, query_engine, sample_graph):
        """Should compute critical path correctly."""
        query_engine.register_task_graph("session-1", sample_graph)
        critical = query_engine.get_critical_path("session-1")
        assert len(critical) == 3
        assert critical[0].task_id == "task-1"
        assert critical[1].task_id == "task-2"
        assert critical[2].task_id == "task-3"

    def test_blocked_tasks(self, query_engine, sample_graph):
        """Should identify blocked tasks."""
        query_engine.register_task_graph("session-1", sample_graph)
        blocked = query_engine.get_blocked_tasks("session-1")
        blocked_ids = {t.task_id for t in blocked}
        # task-2 is running (blocked on task-1 which is done)
        # task-3 is pending (blocked on task-2 which is running)
        assert "task-2" in blocked_ids
        assert "task-3" in blocked_ids
        assert "task-1" not in blocked_ids

    def test_task_dependencies(self, query_engine, sample_graph):
        """Should return direct dependencies of a task."""
        query_engine.register_task_graph("session-1", sample_graph)
        deps = query_engine.get_task_dependencies("session-1", "task-3")
        assert len(deps) == 1
        assert deps[0].task_id == "task-2"

    def test_task_dependents(self, query_engine, sample_graph):
        """Should return tasks that depend on a given task."""
        query_engine.register_task_graph("session-1", sample_graph)
        dependents = query_engine.get_task_dependents("session-1", "task-1")
        assert len(dependents) == 1
        assert dependents[0].task_id == "task-2"

    def test_tasks_by_status(self, query_engine, sample_graph):
        """Should filter tasks by status."""
        query_engine.register_task_graph("session-1", sample_graph)

        done_tasks = query_engine.get_tasks_by_status("session-1", "done")
        assert len(done_tasks) == 1
        assert done_tasks[0].task_id == "task-1"

        pending_tasks = query_engine.get_tasks_by_status("session-1", "pending")
        assert len(pending_tasks) == 1
        assert pending_tasks[0].task_id == "task-3"

    def test_tasks_by_phase(self, query_engine, sample_graph):
        """Should filter tasks by phase."""
        query_engine.register_task_graph("session-1", sample_graph)

        impl_tasks = query_engine.get_tasks_by_phase("session-1", "implementation")
        assert len(impl_tasks) == 1
        assert impl_tasks[0].task_id == "task-2"

        test_tasks = query_engine.get_tasks_by_phase("session-1", "testing")
        assert len(test_tasks) == 1
        assert test_tasks[0].task_id == "task-3"

    def test_health_check(self, query_engine):
        """Health check should return True (placeholder)."""
        assert query_engine.health_check() is True


class TestSkillToolQuery:
    """Tests for SkillToolQuery."""

    @pytest.fixture
    def query_engine(self):
        """Create a SkillToolQuery for testing."""
        return SkillToolQuery(tenant_id="tenant-1")

    @pytest.fixture
    def sample_skills(self):
        """Create sample skills."""
        return {
            "skill-A": ForgedSkillMetadata(
                skill_id="skill-A", name="Skill A", version="1.0",
                created_at=datetime.utcnow(), last_used=None, usage_count=100,
                success_rate=0.95, avg_latency_ms=20, p95_latency_ms=50,
                p99_latency_ms=100, cost_estimate=1.0,
                depends_on_tools=["tool-1"], depends_on_skills=[],
                tags=["learning", "core"], owner="agent", tenant_id="tenant-1",
            ),
            "skill-B": ForgedSkillMetadata(
                skill_id="skill-B", name="Skill B", version="2.0",
                created_at=datetime.utcnow(), last_used=None, usage_count=50,
                success_rate=0.90, avg_latency_ms=30, p95_latency_ms=70,
                p99_latency_ms=150, cost_estimate=2.0,
                depends_on_tools=["tool-2"], depends_on_skills=["skill-A"],
                tags=["plugin"], owner="agent", tenant_id="tenant-1",
            ),
        }

    @pytest.fixture
    def sample_tools(self):
        """Create sample tools."""
        return {
            "tool-1": ForgedToolMetadata(
                tool_id="tool-1", name="Tool 1", implementation="mcp", version="1.0",
                created_at=datetime.utcnow(), last_used=None, usage_count=150,
                success_rate=1.0, avg_latency_ms=10, p95_latency_ms=20,
                avg_cost_per_call=0.5, used_by_skills=["skill-A"],
                used_by_tools=[], status=ToolStatus.AVAILABLE,
                tags=[], tenant_id="tenant-1",
            ),
            "tool-2": ForgedToolMetadata(
                tool_id="tool-2", name="Tool 2", implementation="http", version="1.0",
                created_at=datetime.utcnow(), last_used=None, usage_count=100,
                success_rate=0.98, avg_latency_ms=15, p95_latency_ms=40,
                avg_cost_per_call=0.8, used_by_skills=["skill-B"],
                used_by_tools=[], status=ToolStatus.AVAILABLE,
                tags=[], tenant_id="tenant-1",
            ),
        }

    def test_register_skill(self, query_engine, sample_skills):
        """Should register skills."""
        skill = sample_skills["skill-A"]
        query_engine.register_skill(skill)
        retrieved = query_engine.get_skill("skill-A")
        assert retrieved is not None
        assert retrieved.name == "Skill A"

    def test_register_skill_tenant_mismatch(self, query_engine):
        """Should reject skills with mismatched tenant_id."""
        bad_skill = ForgedSkillMetadata(
            skill_id="bad", name="Bad", version="1.0",
            created_at=datetime.utcnow(), last_used=None, usage_count=0,
            success_rate=0.0, avg_latency_ms=0, p95_latency_ms=0, p99_latency_ms=0,
            cost_estimate=0, depends_on_tools=[], depends_on_skills=[],
            tags=[], owner="agent", tenant_id="tenant-2",  # Different tenant
        )
        with pytest.raises(ValueError, match="Tenant mismatch"):
            query_engine.register_skill(bad_skill)

    def test_register_tool(self, query_engine, sample_tools):
        """Should register tools."""
        tool = sample_tools["tool-1"]
        query_engine.register_tool(tool)
        retrieved = query_engine.get_tool("tool-1")
        assert retrieved is not None
        assert retrieved.name == "Tool 1"

    def test_register_tool_tenant_mismatch(self, query_engine):
        """Should reject tools with mismatched tenant_id."""
        bad_tool = ForgedToolMetadata(
            tool_id="bad", name="Bad", implementation="mcp", version="1.0",
            created_at=datetime.utcnow(), last_used=None, usage_count=0,
            success_rate=0.0, avg_latency_ms=0, p95_latency_ms=0, avg_cost_per_call=0,
            used_by_skills=[], used_by_tools=[], status=ToolStatus.AVAILABLE,
            tags=[], tenant_id="tenant-2",  # Different tenant
        )
        with pytest.raises(ValueError, match="Tenant mismatch"):
            query_engine.register_tool(bad_tool)

    def test_list_skills(self, query_engine, sample_skills):
        """Should list all skills."""
        for skill in sample_skills.values():
            query_engine.register_skill(skill)

        all_skills = query_engine.list_skills()
        assert len(all_skills) == 2
        assert "skill-A" in all_skills
        assert "skill-B" in all_skills

    def test_list_skills_by_tag(self, query_engine, sample_skills):
        """Should filter skills by tags."""
        for skill in sample_skills.values():
            query_engine.register_skill(skill)

        core_skills = query_engine.list_skills(tags=["core"])
        assert len(core_skills) == 1
        assert "skill-A" in core_skills

    def test_list_tools(self, query_engine, sample_tools):
        """Should list all tools."""
        for tool in sample_tools.values():
            query_engine.register_tool(tool)

        all_tools = query_engine.list_tools()
        assert len(all_tools) == 2

    def test_list_tools_by_status(self, query_engine, sample_tools):
        """Should filter tools by status."""
        for tool in sample_tools.values():
            query_engine.register_tool(tool)

        available_tools = query_engine.list_tools(status="available")
        assert len(available_tools) == 2

    def test_get_dependency_graph(self, query_engine, sample_skills, sample_tools):
        """Should build dependency graph."""
        for skill in sample_skills.values():
            query_engine.register_skill(skill)
        for tool in sample_tools.values():
            query_engine.register_tool(tool)

        graph = query_engine.get_dependency_graph()
        assert len(graph.skills) == 2
        assert len(graph.tools) == 2
        assert graph.tenant_id == "tenant-1"

    def test_get_skill_dependencies(self, query_engine, sample_skills, sample_tools):
        """Should compute transitive dependencies."""
        for skill in sample_skills.values():
            query_engine.register_skill(skill)
        for tool in sample_tools.values():
            query_engine.register_tool(tool)

        # skill-B depends on skill-A (tool-1) and tool-2
        deps = query_engine.get_skill_dependencies("skill-B")
        assert "tool-1" in deps
        assert "tool-2" in deps

    def test_find_circular_dependencies(self, query_engine, sample_skills, sample_tools):
        """Should detect circular dependencies."""
        for skill in sample_skills.values():
            query_engine.register_skill(skill)
        for tool in sample_tools.values():
            query_engine.register_tool(tool)

        cycles = query_engine.find_circular_dependencies()
        # Sample graph is acyclic
        assert len(cycles) == 0

    def test_get_critical_tools(self, query_engine, sample_skills, sample_tools):
        """Should identify critical tools."""
        for skill in sample_skills.values():
            query_engine.register_skill(skill)
        for tool in sample_tools.values():
            query_engine.register_tool(tool)

        # tool-1 has 150 uses, tool-2 has 100 uses
        critical = query_engine.get_critical_tools(usage_threshold=100)
        assert critical == ["tool-1", "tool-2"]

    def test_health_check(self, query_engine):
        """Health check should return True."""
        assert query_engine.health_check() is True


class TestCategoryQuery:
    """Tests for CategoryQuery."""

    @pytest.fixture
    def query_engine(self):
        """Create a CategoryQuery for testing."""
        return CategoryQuery(tenant_id="tenant-1")

    @pytest.fixture
    def sample_events(self):
        """Create sample events."""
        now = datetime.utcnow()
        return [
            EventSummary(
                event_id="ev-1", category="learning", timestamp=now - timedelta(seconds=5),
                event_type="skill_created", status="success", details={"skill_id": "test"},
                duration_ms=10.0,
            ),
            EventSummary(
                event_id="ev-2", category="learning", timestamp=now - timedelta(seconds=3),
                event_type="event_logged", status="success", details={"subcategory": "confidence"},
                duration_ms=5.0,
            ),
            EventSummary(
                event_id="ev-3", category="audit", timestamp=now - timedelta(seconds=1),
                event_type="verify", status="success", details={},
                duration_ms=15.0,
            ),
        ]

    @pytest.fixture
    def sample_metrics(self):
        """Create sample metrics."""
        return CategoryHealthMetrics(
            category="learning",
            event_count=100,
            error_count=5,
            error_rate=0.05,
            avg_latency_ms=12.5,
            p50_latency_ms=10.0,
            p95_latency_ms=30.0,
            p99_latency_ms=50.0,
            max_latency_ms=100.0,
            subcategories={"learning:confidence": 60, "learning:feedback": 40},
            recent_events=[],
            error_patterns=[],
            status=CategoryStatus.HEALTHY,
            tenant_id="tenant-1",
            timestamp=datetime.utcnow(),
        )

    def test_add_event(self, query_engine, sample_events):
        """Should add events."""
        event = sample_events[0]
        query_engine.add_event(event)
        # Events are stored in list
        assert len(query_engine._events) == 1

    def test_update_category_metrics(self, query_engine, sample_metrics):
        """Should update category metrics."""
        query_engine.update_category_metrics("learning", sample_metrics)
        retrieved = query_engine.get_category_health("learning")
        assert retrieved is not None
        assert retrieved.event_count == 100

    def test_update_category_metrics_tenant_mismatch(self, query_engine):
        """Should reject metrics with mismatched tenant_id."""
        bad_metrics = CategoryHealthMetrics(
            category="test",
            event_count=0, error_count=0, error_rate=0.0,
            avg_latency_ms=0, p50_latency_ms=0, p95_latency_ms=0,
            p99_latency_ms=0, max_latency_ms=0, subcategories={},
            recent_events=[], error_patterns=[],
            status=CategoryStatus.HEALTHY,
            tenant_id="tenant-2",  # Different tenant
            timestamp=datetime.utcnow(),
        )
        with pytest.raises(ValueError, match="Tenant mismatch"):
            query_engine.update_category_metrics("test", bad_metrics)

    def test_list_categories(self, query_engine, sample_metrics):
        """Should list all categories with data."""
        query_engine.update_category_metrics("learning", sample_metrics)
        categories = query_engine.list_categories()
        assert "learning" in categories

    def test_filter_events_by_category(self, query_engine, sample_events):
        """Should filter events by category."""
        for event in sample_events:
            query_engine.add_event(event)

        learning_events = query_engine.filter_events(category="learning")
        assert len(learning_events) == 2

    def test_filter_events_by_status(self, query_engine, sample_events):
        """Should filter events by status."""
        for event in sample_events:
            query_engine.add_event(event)

        success_events = query_engine.filter_events(status="success")
        assert len(success_events) == 3

    def test_filter_events_with_limit(self, query_engine, sample_events):
        """Should respect limit parameter."""
        for event in sample_events:
            query_engine.add_event(event)

        limited = query_engine.filter_events(limit=2)
        assert len(limited) == 2

    def test_filter_events_reverse_chronological(self, query_engine, sample_events):
        """Filtered events should be most recent first."""
        for event in sample_events:
            query_engine.add_event(event)

        filtered = query_engine.filter_events()
        # Most recent should be ev-3 (1 second ago)
        assert filtered[0].event_id == "ev-3"

    def test_get_drill_down(self, query_engine, sample_events, sample_metrics):
        """Should provide drill-down view."""
        for event in sample_events:
            query_engine.add_event(event)
        query_engine.update_category_metrics("learning", sample_metrics)

        drill_down = query_engine.get_drill_down("learning")
        assert drill_down is not None
        assert drill_down.category == "learning"
        assert drill_down.metrics.event_count == 100

    def test_health_check(self, query_engine):
        """Health check should return True."""
        assert query_engine.health_check() is True


class TestTenantIsolation:
    """Tests for cross-tenant isolation."""

    def test_task_graph_query_tenant_isolation(self):
        """TaskGraphQuery should not mix tenants."""
        query1 = TaskGraphQuery(tenant_id="tenant-1")
        query2 = TaskGraphQuery(tenant_id="tenant-2")

        graph1 = TaskGraph(
            tasks={"t1": TaskNode(
                task_id="t1", name="T1", status=TaskStatus.PENDING,
                phase="test", iteration=1, parent_id=None, children_ids=[],
                dependencies=[], created_at=datetime.utcnow(), started_at=None,
                completed_at=None, estimated_duration=None, actual_duration=None,
                error_message=None, owner="agent", tenant_id="tenant-1",
            )},
            tenant_id="tenant-1",
            session_id="session-1",
        )

        query1.register_task_graph("session-1", graph1)

        # Query2 should not see tenant-1's graphs
        assert query2.get_task_graph("session-1") is None

    def test_skill_tool_query_tenant_isolation(self):
        """SkillToolQuery should not mix tenants."""
        query1 = SkillToolQuery(tenant_id="tenant-1")
        query2 = SkillToolQuery(tenant_id="tenant-2")

        skill = ForgedSkillMetadata(
            skill_id="test", name="Test", version="1.0",
            created_at=datetime.utcnow(), last_used=None, usage_count=0,
            success_rate=0.0, avg_latency_ms=0, p95_latency_ms=0, p99_latency_ms=0,
            cost_estimate=0, depends_on_tools=[], depends_on_skills=[],
            tags=[], owner="agent", tenant_id="tenant-1",
        )

        query1.register_skill(skill)

        # Query2 should not see tenant-1's skills
        all_skills = query2.list_skills()
        assert len(all_skills) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
