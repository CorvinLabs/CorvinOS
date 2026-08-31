#!/usr/bin/env python3
"""
Verification script for Phase 1.1: QueryEngine base classes and data models.

Performs basic sanity checks without requiring pytest:
1. Import all modules successfully
2. Create sample instances of all data models
3. Test basic functionality of query engines
4. Verify tenant isolation

Exit codes:
  0 = All checks passed
  1 = Import error
  2 = Data model instantiation error
  3 = Query engine error
  4 = Tenant isolation error
"""

import sys
import os
from datetime import datetime, timedelta

# Add the inspection module to path
sys.path.insert(0, os.path.dirname(__file__))

print("=" * 70)
print("PHASE 1.1 VERIFICATION: QueryEngine Base Classes & Data Models")
print("=" * 70)

# Test 1: Imports
print("\n[1/4] Testing imports...")
try:
    from data_models import (
        TaskStatus, TaskNode, TaskGraph,
        ForgedSkillMetadata, ForgedToolMetadata,
        SkillToolDependencyGraph,
        CategoryStatus, CategoryHealthMetrics,
        EventSummary, ErrorPattern,
        ToolStatus,
    )
    from query_engine import (
        QueryEngine, TaskGraphQuery, SkillToolQuery, CategoryQuery,
    )
    print("✓ All imports successful")
except Exception as e:
    print(f"✗ Import failed: {e}")
    sys.exit(1)

# Test 2: Data model instantiation
print("\n[2/4] Testing data model instantiation...")
try:
    # TaskNode
    task = TaskNode(
        task_id="test-1",
        name="Test Task",
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
        owner="test-agent",
        tenant_id="tenant-default",
    )
    assert task.task_id == "test-1"
    assert not task.is_blocked()
    print("  ✓ TaskNode instantiation works")

    # TaskGraph
    graph = TaskGraph(
        tasks={"test-1": task},
        tenant_id="tenant-default",
        session_id="session-1",
    )
    assert len(graph.tasks) == 1
    dag = graph.get_dag()
    assert isinstance(dag, dict)
    print("  ✓ TaskGraph instantiation works")

    # ForgedSkillMetadata
    skill = ForgedSkillMetadata(
        skill_id="test-skill",
        name="Test Skill",
        version="1.0.0",
        created_at=datetime.utcnow(),
        last_used=None,
        usage_count=0,
        success_rate=0.95,
        avg_latency_ms=10.0,
        p95_latency_ms=20.0,
        p99_latency_ms=50.0,
        cost_estimate=1.0,
        depends_on_tools=[],
        depends_on_skills=[],
        tags=["test"],
        owner="test-agent",
        tenant_id="tenant-default",
    )
    assert skill.skill_id == "test-skill"
    assert skill.is_reliable()
    print("  ✓ ForgedSkillMetadata instantiation works")

    # ForgedToolMetadata
    tool = ForgedToolMetadata(
        tool_id="test-tool",
        name="Test Tool",
        implementation="mcp",
        version="1.0.0",
        created_at=datetime.utcnow(),
        last_used=None,
        usage_count=10,
        success_rate=1.0,
        avg_latency_ms=5.0,
        p95_latency_ms=10.0,
        avg_cost_per_call=0.5,
        used_by_skills=[],
        used_by_tools=[],
        status=ToolStatus.AVAILABLE,
        tags=[],
        tenant_id="tenant-default",
    )
    assert tool.tool_id == "test-tool"
    assert tool.is_available()
    print("  ✓ ForgedToolMetadata instantiation works")

    # CategoryHealthMetrics
    metrics = CategoryHealthMetrics(
        category="learning",
        event_count=100,
        error_count=5,
        error_rate=0.05,
        avg_latency_ms=10.0,
        p50_latency_ms=8.0,
        p95_latency_ms=25.0,
        p99_latency_ms=50.0,
        max_latency_ms=100.0,
        subcategories={"learning:confidence": 50},
        recent_events=[],
        error_patterns=[],
        status=CategoryStatus.HEALTHY,
        tenant_id="tenant-default",
        timestamp=datetime.utcnow(),
    )
    assert metrics.is_healthy()
    print("  ✓ CategoryHealthMetrics instantiation works")

    print("✓ All data models instantiate successfully")
except Exception as e:
    print(f"✗ Data model instantiation failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(2)

# Test 3: Query engine functionality
print("\n[3/4] Testing query engines...")
try:
    # TaskGraphQuery
    tg_query = TaskGraphQuery(tenant_id="tenant-default")
    tg_query.register_task_graph("session-1", graph)
    retrieved = tg_query.get_task_graph("session-1")
    assert retrieved is not None
    print("  ✓ TaskGraphQuery works")

    # SkillToolQuery
    st_query = SkillToolQuery(tenant_id="tenant-default")
    st_query.register_skill(skill)
    st_query.register_tool(tool)
    retrieved_skill = st_query.get_skill("test-skill")
    assert retrieved_skill is not None
    dep_graph = st_query.get_dependency_graph()
    assert len(dep_graph.skills) == 1
    print("  ✓ SkillToolQuery works")

    # CategoryQuery
    cat_query = CategoryQuery(tenant_id="tenant-default")
    cat_query.update_category_metrics("learning", metrics)
    retrieved_metrics = cat_query.get_category_health("learning")
    assert retrieved_metrics is not None
    print("  ✓ CategoryQuery works")

    print("✓ All query engines function correctly")
except Exception as e:
    print(f"✗ Query engine test failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(3)

# Test 4: Tenant isolation
print("\n[4/4] Testing tenant isolation...")
try:
    # TaskGraphQuery isolation
    tg_query_t1 = TaskGraphQuery(tenant_id="tenant-1")
    tg_query_t2 = TaskGraphQuery(tenant_id="tenant-2")

    graph_t1 = TaskGraph(tasks={}, tenant_id="tenant-1", session_id="session-1")
    tg_query_t1.register_task_graph("session-1", graph_t1)

    # tenant-2 should not see tenant-1's graphs
    assert tg_query_t2.get_task_graph("session-1") is None
    print("  ✓ TaskGraphQuery tenant isolation works")

    # SkillToolQuery isolation
    st_query_t1 = SkillToolQuery(tenant_id="tenant-1")
    st_query_t2 = SkillToolQuery(tenant_id="tenant-2")

    skill_t1 = ForgedSkillMetadata(
        skill_id="test", name="Test", version="1.0",
        created_at=datetime.utcnow(), last_used=None, usage_count=0,
        success_rate=0.0, avg_latency_ms=0, p95_latency_ms=0, p99_latency_ms=0,
        cost_estimate=0, depends_on_tools=[], depends_on_skills=[],
        tags=[], owner="agent", tenant_id="tenant-1",
    )
    st_query_t1.register_skill(skill_t1)

    # tenant-2 should not see tenant-1's skills
    all_skills = st_query_t2.list_skills()
    assert len(all_skills) == 0
    print("  ✓ SkillToolQuery tenant isolation works")

    # CategoryQuery isolation
    cat_query_t1 = CategoryQuery(tenant_id="tenant-1")
    cat_query_t2 = CategoryQuery(tenant_id="tenant-2")

    metrics_t1 = CategoryHealthMetrics(
        category="test", event_count=0, error_count=0, error_rate=0.0,
        avg_latency_ms=0, p50_latency_ms=0, p95_latency_ms=0, p99_latency_ms=0,
        max_latency_ms=0, subcategories={}, recent_events=[],
        error_patterns=[], status=CategoryStatus.HEALTHY,
        tenant_id="tenant-1", timestamp=datetime.utcnow(),
    )
    cat_query_t1.update_category_metrics("test", metrics_t1)

    # tenant-2 should not see tenant-1's metrics
    assert cat_query_t2.get_category_health("test") is None
    print("  ✓ CategoryQuery tenant isolation works")

    print("✓ Tenant isolation verified")
except Exception as e:
    print(f"✗ Tenant isolation test failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(4)

print("\n" + "=" * 70)
print("VERIFICATION PASSED ✓")
print("=" * 70)
print("\nPhase 1.1 Summary:")
print("  • Data Models: TaskNode, TaskGraph, ForgedSkillMetadata,")
print("                 ForgedToolMetadata, SkillToolDependencyGraph,")
print("                 CategoryHealthMetrics, EventSummary, ErrorPattern")
print("  • Query Engines: TaskGraphQuery, SkillToolQuery, CategoryQuery")
print("  • Tenant Isolation: Verified (cross-tenant queries return empty)")
print("  • Code Quality: All syntax valid, all imports work")
print("\nReady for Phase 1.2: Inspection API routes (Flask)")
print("=" * 70)
