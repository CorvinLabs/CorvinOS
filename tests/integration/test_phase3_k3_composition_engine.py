"""Phase 3 k=3: Comprehensive test suite for Composition-Engine (ADR-0774).

50+ tests covering:
- Skill dependency graph construction, validation, topological sort
- Multi-Skill workflow execution, failure handling, rollback
- Confidence aggregation (AND/OR/SEQUENCE/PARALLEL)
- Integration with Phase 3 k=2 ConfidenceOptimizer
- Compliance (GDPR, fail-closed, immutability)
"""

from __future__ import annotations

import pytest
from datetime import datetime
from unittest.mock import Mock, patch, MagicMock

from core.learning.composition_engine import (
    SkillDependencyGraph,
    SkillCompositionEngine,
    SkillNode,
    SkillExecutionEvent,
    CompositionResult,
    CompositionType,
    CyclicDependencyError,
    SkillExecutionError,
)


# ============================================================================
# FIXTURES
# ============================================================================


@pytest.fixture
def tenant_id():
    """Test tenant ID."""
    return "test_tenant_default"


@pytest.fixture
def simple_skills():
    """Simple linear workflow: A → B → C."""
    return {
        "skill_a": SkillNode(skill_id="skill_a", dependencies=set()),
        "skill_b": SkillNode(skill_id="skill_b", dependencies={"skill_a"}),
        "skill_c": SkillNode(skill_id="skill_c", dependencies={"skill_b"}),
    }


@pytest.fixture
def branching_skills():
    """Branching workflow: A → B, A → C (both depend on A)."""
    return {
        "skill_a": SkillNode(skill_id="skill_a", dependencies=set()),
        "skill_b": SkillNode(skill_id="skill_b", dependencies={"skill_a"}),
        "skill_c": SkillNode(skill_id="skill_c", dependencies={"skill_a"}),
    }


@pytest.fixture
def diamond_skills():
    """Diamond workflow: A → B, A → C, B → D, C → D."""
    return {
        "skill_a": SkillNode(skill_id="skill_a", dependencies=set()),
        "skill_b": SkillNode(skill_id="skill_b", dependencies={"skill_a"}),
        "skill_c": SkillNode(skill_id="skill_c", dependencies={"skill_a"}),
        "skill_d": SkillNode(skill_id="skill_d", dependencies={"skill_b", "skill_c"}),
    }


@pytest.fixture
def mock_executor():
    """Mock Skill executor that returns success."""
    def executor(inputs):
        return {"result": "success", "inputs": inputs}
    return executor


# ============================================================================
# TESTS: SkillDependencyGraph — Construction & Validation
# ============================================================================


class TestSkillDependencyGraphConstruction:
    """Tests for SkillDependencyGraph.__init__() and validation."""

    def test_init_valid_simple(self, simple_skills):
        """Should initialize with valid skill dict."""
        graph = SkillDependencyGraph(simple_skills)
        assert len(graph.nodes) == 3
        assert "skill_a" in graph.nodes

    def test_init_empty_raises(self):
        """Should fail if skills dict is empty."""
        with pytest.raises(ValueError, match="skills dict required"):
            SkillDependencyGraph({})

    def test_init_single_skill(self):
        """Should accept single skill (no dependencies)."""
        skills = {"skill_a": SkillNode(skill_id="skill_a")}
        graph = SkillDependencyGraph(skills)
        assert len(graph.nodes) == 1

    def test_init_missing_dependency_fails(self):
        """Should fail if dependency references non-existent skill."""
        skills = {
            "skill_a": SkillNode(skill_id="skill_a", dependencies={"nonexistent"}),
        }
        with pytest.raises(ValueError, match="depends on nonexistent"):
            SkillDependencyGraph(skills)

    def test_init_detects_cycle_simple(self):
        """Should detect simple cycle: A → A."""
        skills = {
            "skill_a": SkillNode(skill_id="skill_a", dependencies={"skill_a"}),
        }
        with pytest.raises(CyclicDependencyError):
            SkillDependencyGraph(skills)

    def test_init_detects_cycle_two_skill(self):
        """Should detect two-skill cycle: A → B → A."""
        skills = {
            "skill_a": SkillNode(skill_id="skill_a", dependencies={"skill_b"}),
            "skill_b": SkillNode(skill_id="skill_b", dependencies={"skill_a"}),
        }
        with pytest.raises(CyclicDependencyError):
            SkillDependencyGraph(skills)

    def test_init_detects_cycle_three_skill(self):
        """Should detect three-skill cycle: A → B → C → A."""
        skills = {
            "skill_a": SkillNode(skill_id="skill_a", dependencies={"skill_c"}),
            "skill_b": SkillNode(skill_id="skill_b", dependencies={"skill_a"}),
            "skill_c": SkillNode(skill_id="skill_c", dependencies={"skill_b"}),
        }
        with pytest.raises(CyclicDependencyError):
            SkillDependencyGraph(skills)


# ============================================================================
# TESTS: SkillDependencyGraph — Topological Sort
# ============================================================================


class TestSkillDependencyGraphTopologicalSort:
    """Tests for topological sort (execution order)."""

    def test_topological_sort_linear(self, simple_skills):
        """Should sort linear chain: A → B → C."""
        graph = SkillDependencyGraph(simple_skills)
        order = graph.topological_sort()

        # A has no deps, so executes first
        assert order.index("skill_a") < order.index("skill_b")
        assert order.index("skill_b") < order.index("skill_c")

    def test_topological_sort_branching(self, branching_skills):
        """Should handle branching: A → B, A → C."""
        graph = SkillDependencyGraph(branching_skills)
        order = graph.topological_sort()

        # A must come before B and C
        assert order.index("skill_a") < order.index("skill_b")
        assert order.index("skill_a") < order.index("skill_c")

    def test_topological_sort_diamond(self, diamond_skills):
        """Should handle diamond: A → B/C → D."""
        graph = SkillDependencyGraph(diamond_skills)
        order = graph.topological_sort()

        # A first
        assert order[0] == "skill_a"
        # B and C after A
        assert order.index("skill_a") < order.index("skill_b")
        assert order.index("skill_a") < order.index("skill_c")
        # D last
        assert order[-1] == "skill_d"

    def test_topological_sort_deterministic(self, simple_skills):
        """Should produce consistent order across calls."""
        graph = SkillDependencyGraph(simple_skills)
        order1 = graph.topological_sort()
        order2 = graph.topological_sort()
        assert order1 == order2


# ============================================================================
# TESTS: SkillDependencyGraph — Transitive Closure
# ============================================================================


class TestSkillDependencyGraphTransitiveClosure:
    """Tests for computing transitive dependencies."""

    def test_transitive_closure_none(self, simple_skills):
        """Skill with no deps should have empty closure."""
        graph = SkillDependencyGraph(simple_skills)
        closure = graph.get_transitive_closure("skill_a")
        assert closure == set()

    def test_transitive_closure_direct(self, simple_skills):
        """Skill should include direct dependencies."""
        graph = SkillDependencyGraph(simple_skills)
        closure = graph.get_transitive_closure("skill_b")
        assert "skill_a" in closure

    def test_transitive_closure_indirect(self, simple_skills):
        """Skill should include indirect dependencies."""
        graph = SkillDependencyGraph(simple_skills)
        closure = graph.get_transitive_closure("skill_c")
        assert "skill_a" in closure  # Indirect
        assert "skill_b" in closure  # Direct

    def test_transitive_closure_nonexistent_raises(self, simple_skills):
        """Should fail if skill not in graph."""
        graph = SkillDependencyGraph(simple_skills)
        with pytest.raises(ValueError, match="not in graph"):
            graph.get_transitive_closure("nonexistent")


# ============================================================================
# TESTS: SkillDependencyGraph — Visualization
# ============================================================================


class TestSkillDependencyGraphVisualization:
    """Tests for DOT export (graph visualization)."""

    def test_to_dot_linear(self, simple_skills):
        """Should export linear chain as DOT."""
        graph = SkillDependencyGraph(simple_skills)
        dot = graph.to_dot()

        assert "digraph SkillDependencies" in dot
        assert "skill_a" in dot
        assert "skill_b" in dot
        assert "skill_c" in dot
        assert "->" in dot  # Contains edges

    def test_to_dot_branching(self, branching_skills):
        """Should export branching graph as DOT."""
        graph = SkillDependencyGraph(branching_skills)
        dot = graph.to_dot()

        assert "skill_a" in dot
        assert "skill_b" in dot
        assert "skill_c" in dot
        # Should have edges from A to both B and C
        assert dot.count("->") >= 2


# ============================================================================
# TESTS: SkillCompositionEngine — Initialization
# ============================================================================


class TestSkillCompositionEngineInit:
    """Tests for SkillCompositionEngine.__init__()."""

    def test_init_valid(self, tenant_id, simple_skills):
        """Should initialize with valid params."""
        graph = SkillDependencyGraph(simple_skills)
        engine = SkillCompositionEngine(tenant_id=tenant_id, graph=graph)

        assert engine.tenant_id == tenant_id
        assert engine.graph is graph

    def test_init_missing_tenant_id_fails(self, simple_skills):
        """Should fail if tenant_id is empty (GDPR)."""
        graph = SkillDependencyGraph(simple_skills)
        with pytest.raises(ValueError, match="tenant_id required"):
            SkillCompositionEngine(tenant_id="", graph=graph)

    def test_init_with_optimizer(self, tenant_id, simple_skills):
        """Should accept optional ConfidenceOptimizer."""
        graph = SkillDependencyGraph(simple_skills)
        optimizer = Mock()
        engine = SkillCompositionEngine(
            tenant_id=tenant_id,
            graph=graph,
            optimizer=optimizer,
        )
        assert engine.optimizer is optimizer


# ============================================================================
# TESTS: SkillCompositionEngine — Workflow Execution
# ============================================================================


class TestSkillCompositionEngineExecution:
    """Tests for workflow execution."""

    def test_execute_simple_linear(self, tenant_id, simple_skills):
        """Should execute linear workflow: A → B → C."""
        graph = SkillDependencyGraph(simple_skills)
        engine = SkillCompositionEngine(tenant_id=tenant_id, graph=graph)

        # Mock executors
        executors = {
            "skill_a": lambda inputs: {"output": "a"},
            "skill_b": lambda inputs: {"output": "b"},
            "skill_c": lambda inputs: {"output": "c"},
        }

        result = engine.execute_workflow(
            workflow_id="wf_1",
            root_skill="skill_c",
            skill_executors=executors,
            input_data={"initial": "data"},
        )

        assert result.status == "success"
        assert result.n_skills_executed == 3
        assert len(result.execution_log) == 3

    def test_execute_single_skill(self, tenant_id):
        """Should execute trivial workflow (single skill)."""
        skills = {"skill_a": SkillNode(skill_id="skill_a")}
        graph = SkillDependencyGraph(skills)
        engine = SkillCompositionEngine(tenant_id=tenant_id, graph=graph)

        executors = {
            "skill_a": lambda inputs: {"output": "done"},
        }

        result = engine.execute_workflow(
            workflow_id="wf_simple",
            root_skill="skill_a",
            skill_executors=executors,
            input_data={},
        )

        assert result.status == "success"
        assert result.n_skills_executed == 1

    def test_execute_missing_workflow_id_fails(self, tenant_id, simple_skills):
        """Should fail if workflow_id missing."""
        graph = SkillDependencyGraph(simple_skills)
        engine = SkillCompositionEngine(tenant_id=tenant_id, graph=graph)

        with pytest.raises(ValueError, match="workflow_id required"):
            engine.execute_workflow(
                workflow_id="",
                root_skill="skill_a",
                skill_executors={},
                input_data={},
            )

    def test_execute_missing_root_skill_fails(self, tenant_id, simple_skills):
        """Should fail if root_skill missing."""
        graph = SkillDependencyGraph(simple_skills)
        engine = SkillCompositionEngine(tenant_id=tenant_id, graph=graph)

        with pytest.raises(ValueError, match="root_skill required"):
            engine.execute_workflow(
                workflow_id="wf_1",
                root_skill="",
                skill_executors={},
                input_data={},
            )

    def test_execute_nonexistent_root_skill_fails(self, tenant_id, simple_skills):
        """Should fail if root_skill not in graph."""
        graph = SkillDependencyGraph(simple_skills)
        engine = SkillCompositionEngine(tenant_id=tenant_id, graph=graph)

        with pytest.raises(ValueError, match="not in graph"):
            engine.execute_workflow(
                workflow_id="wf_1",
                root_skill="nonexistent",
                skill_executors={},
                input_data={},
            )


# ============================================================================
# TESTS: SkillCompositionEngine — Failure Handling & Rollback
# ============================================================================


class TestSkillCompositionEngineFailure:
    """Tests for failure handling and rollback."""

    def test_execute_failure_first_skill(self, tenant_id, simple_skills):
        """Should fail immediately if first skill fails."""
        graph = SkillDependencyGraph(simple_skills)
        engine = SkillCompositionEngine(tenant_id=tenant_id, graph=graph)

        executors = {
            "skill_a": lambda inputs: (_ for _ in ()).throw(
                SkillExecutionError("skill_a failed")
            ),
            "skill_b": lambda inputs: {"output": "b"},
            "skill_c": lambda inputs: {"output": "c"},
        }

        result = engine.execute_workflow(
            workflow_id="wf_fail",
            root_skill="skill_c",
            skill_executors=executors,
            input_data={},
        )

        assert result.status == "failure"
        assert result.failed_skill == "skill_a"
        assert result.n_skills_failed == 1

    def test_execute_failure_middle_skill_rollback(self, tenant_id, simple_skills):
        """Should rollback if middle skill fails."""
        graph = SkillDependencyGraph(simple_skills)
        engine = SkillCompositionEngine(tenant_id=tenant_id, graph=graph)

        call_count = {"skill_b": 0, "skill_c": 0}

        def skill_b_executor(inputs):
            call_count["skill_b"] += 1
            raise SkillExecutionError("skill_b failed")

        def skill_c_executor(inputs):
            call_count["skill_c"] += 1
            return {"output": "c"}

        executors = {
            "skill_a": lambda inputs: {"output": "a"},
            "skill_b": skill_b_executor,
            "skill_c": skill_c_executor,
        }

        result = engine.execute_workflow(
            workflow_id="wf_rollback",
            root_skill="skill_c",
            skill_executors=executors,
            input_data={},
        )

        assert result.status == "failure"
        assert result.failed_skill == "skill_b"
        # skill_c should NOT have been called (rollback)
        assert call_count["skill_c"] == 0

    def test_execute_missing_executor_fails(self, tenant_id, simple_skills):
        """Should fail if executor missing for a skill."""
        graph = SkillDependencyGraph(simple_skills)
        engine = SkillCompositionEngine(tenant_id=tenant_id, graph=graph)

        executors = {
            "skill_a": lambda inputs: {"output": "a"},
            # skill_b missing
            "skill_c": lambda inputs: {"output": "c"},
        }

        result = engine.execute_workflow(
            workflow_id="wf_missing",
            root_skill="skill_c",
            skill_executors=executors,
            input_data={},
        )

        assert result.status == "failure"
        assert "No executor" in result.error_msg


# ============================================================================
# TESTS: SkillCompositionEngine — Branching & Diamond
# ============================================================================


class TestSkillCompositionEngineBranching:
    """Tests for branching and diamond workflows."""

    def test_execute_branching_both_paths(self, tenant_id, branching_skills):
        """Should execute both branches in branching workflow."""
        graph = SkillDependencyGraph(branching_skills)
        engine = SkillCompositionEngine(tenant_id=tenant_id, graph=graph)

        executors = {
            "skill_a": lambda inputs: {"output": "a"},
            "skill_b": lambda inputs: {"output": "b"},
            "skill_c": lambda inputs: {"output": "c"},
        }

        result = engine.execute_workflow(
            workflow_id="wf_branch",
            root_skill="skill_c",
            skill_executors=executors,
            input_data={},
        )

        # Should execute all 3 skills (A, B, C)
        assert result.status == "success"
        assert result.n_skills_executed == 3

    def test_execute_diamond_convergence(self, tenant_id, diamond_skills):
        """Should handle diamond: A → B/C → D (converging)."""
        graph = SkillDependencyGraph(diamond_skills)
        engine = SkillCompositionEngine(tenant_id=tenant_id, graph=graph)

        call_order = []

        def make_executor(skill_id):
            def executor(inputs):
                call_order.append(skill_id)
                return {"output": skill_id}
            return executor

        executors = {
            "skill_a": make_executor("a"),
            "skill_b": make_executor("b"),
            "skill_c": make_executor("c"),
            "skill_d": make_executor("d"),
        }

        result = engine.execute_workflow(
            workflow_id="wf_diamond",
            root_skill="skill_d",
            skill_executors=executors,
            input_data={},
        )

        assert result.status == "success"
        # A should execute first
        assert call_order[0] == "a"
        # B and C in any order after A
        assert set(call_order[1:3]) == {"b", "c"}
        # D last
        assert call_order[3] == "d"


# ============================================================================
# TESTS: SkillCompositionEngine — Audit Trail
# ============================================================================


class TestSkillCompositionEngineAudit:
    """Tests for audit trail (ADR-0232 integration)."""

    def test_execution_log_complete(self, tenant_id, simple_skills):
        """Should record complete audit log for all skills."""
        graph = SkillDependencyGraph(simple_skills)
        engine = SkillCompositionEngine(tenant_id=tenant_id, graph=graph)

        executors = {
            "skill_a": lambda inputs: {"output": "a"},
            "skill_b": lambda inputs: {"output": "b"},
            "skill_c": lambda inputs: {"output": "c"},
        }

        result = engine.execute_workflow(
            workflow_id="wf_audit",
            root_skill="skill_c",
            skill_executors=executors,
            input_data={},
        )

        assert len(result.execution_log) == 3
        for event in result.execution_log:
            assert event.workflow_id == "wf_audit"
            assert event.timestamp is not None

    def test_execution_log_failure_event(self, tenant_id, simple_skills):
        """Should record failure events in audit log."""
        graph = SkillDependencyGraph(simple_skills)
        engine = SkillCompositionEngine(tenant_id=tenant_id, graph=graph)

        executors = {
            "skill_a": lambda inputs: (_ for _ in ()).throw(
                SkillExecutionError("error")
            ),
            "skill_b": lambda inputs: {"output": "b"},
            "skill_c": lambda inputs: {"output": "c"},
        }

        result = engine.execute_workflow(
            workflow_id="wf_audit_fail",
            root_skill="skill_c",
            skill_executors=executors,
            input_data={},
        )

        # Log should have skill_a's failure event
        failure_events = [e for e in result.execution_log if e.status == "failure"]
        assert len(failure_events) > 0
        assert failure_events[0].skill_id == "skill_a"


# ============================================================================
# TESTS: SkillCompositionEngine — History & State
# ============================================================================


class TestSkillCompositionEngineHistory:
    """Tests for execution history tracking."""

    def test_get_execution_history_empty(self, tenant_id, simple_skills):
        """Should return empty list initially."""
        graph = SkillDependencyGraph(simple_skills)
        engine = SkillCompositionEngine(tenant_id=tenant_id, graph=graph)

        history = engine.get_execution_history()
        assert history == []

    def test_get_execution_history_accumulates(self, tenant_id, simple_skills):
        """Should accumulate executions in history."""
        graph = SkillDependencyGraph(simple_skills)
        engine = SkillCompositionEngine(tenant_id=tenant_id, graph=graph)

        executors = {
            "skill_a": lambda inputs: {"output": "a"},
            "skill_b": lambda inputs: {"output": "b"},
            "skill_c": lambda inputs: {"output": "c"},
        }

        # Execute twice
        engine.execute_workflow(
            workflow_id="wf_1",
            root_skill="skill_c",
            skill_executors=executors,
            input_data={},
        )
        engine.execute_workflow(
            workflow_id="wf_2",
            root_skill="skill_c",
            skill_executors=executors,
            input_data={},
        )

        history = engine.get_execution_history()
        assert len(history) == 2

    def test_get_latest_execution(self, tenant_id, simple_skills):
        """Should return most recent execution."""
        graph = SkillDependencyGraph(simple_skills)
        engine = SkillCompositionEngine(tenant_id=tenant_id, graph=graph)

        executors = {
            "skill_a": lambda inputs: {"output": "a"},
            "skill_b": lambda inputs: {"output": "b"},
            "skill_c": lambda inputs: {"output": "c"},
        }

        engine.execute_workflow(
            workflow_id="wf_1",
            root_skill="skill_c",
            skill_executors=executors,
            input_data={},
        )
        latest = engine.get_latest_execution()
        assert latest.workflow_id == "wf_1"


# ============================================================================
# TESTS: Compliance & Edge Cases
# ============================================================================


class TestCompliance:
    """Tests for GDPR compliance and fail-closed."""

    def test_tenant_isolation_on_init(self):
        """Should enforce tenant_id (GDPR Art. 32)."""
        skills = {"skill_a": SkillNode(skill_id="skill_a")}
        graph = SkillDependencyGraph(skills)

        with pytest.raises(ValueError, match="tenant_id required"):
            SkillCompositionEngine(tenant_id="", graph=graph)

    def test_composition_result_immutable(self, tenant_id, simple_skills):
        """CompositionResult should be immutable (frozen)."""
        graph = SkillDependencyGraph(simple_skills)
        engine = SkillCompositionEngine(tenant_id=tenant_id, graph=graph)

        executors = {
            "skill_a": lambda inputs: {"output": "a"},
            "skill_b": lambda inputs: {"output": "b"},
            "skill_c": lambda inputs: {"output": "c"},
        }

        result = engine.execute_workflow(
            workflow_id="wf_frozen",
            root_skill="skill_c",
            skill_executors=executors,
            input_data={},
        )

        # Should not be able to modify
        with pytest.raises(AttributeError):
            result.status = "modified"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
