"""Unit tests for DAG planner."""

import pytest
import json
from core.task_engine.task_def import TaskDefinition
from core.task_engine.dag_planner import DAGPlanner


class TestDAGPlanner:
    """Tests for topological sort + dependency resolution."""

    def test_simple_linear_dag(self):
        """Test: simple 3-phase linear DAG (no branching)."""
        task_json = json.dumps({
            "task_id": "linear-dag",
            "phases": [
                {"id": "p1", "goal": "Phase 1", "skills": [], "gates": []},
                {"id": "p2", "goal": "Phase 2", "skills": [], "gates": [], "depends_on": ["p1"]},
                {"id": "p3", "goal": "Phase 3", "skills": [], "gates": [], "depends_on": ["p2"]},
            ],
        })
        task_def = TaskDefinition.from_json(task_json)
        planner = DAGPlanner(task_def)

        # Plan should be p1 → p2 → p3
        plan = planner.plan()
        assert plan == ["p1", "p2", "p3"]

    def test_diamond_dag(self):
        """Test: diamond DAG (p1 → p2,p3 → p4)."""
        task_json = json.dumps({
            "task_id": "diamond-dag",
            "phases": [
                {"id": "p1", "goal": "Phase 1", "skills": [], "gates": []},
                {"id": "p2", "goal": "Phase 2", "skills": [], "gates": [], "depends_on": ["p1"]},
                {"id": "p3", "goal": "Phase 3", "skills": [], "gates": [], "depends_on": ["p1"]},
                {"id": "p4", "goal": "Phase 4", "skills": [], "gates": [], "depends_on": ["p2", "p3"]},
            ],
        })
        task_def = TaskDefinition.from_json(task_json)
        planner = DAGPlanner(task_def)

        plan = planner.plan()
        # p1 must come first
        assert plan[0] == "p1"
        # p4 must come last
        assert plan[-1] == "p4"
        # p2 and p3 can be in any order, but both before p4
        assert set(plan[1:3]) == {"p2", "p3"}

    def test_cycle_detection(self):
        """Test: cyclic dependency raises error."""
        task_json = json.dumps({
            "task_id": "cycle-dag",
            "phases": [
                {"id": "p1", "goal": "Phase 1", "skills": [], "gates": [], "depends_on": ["p2"]},
                {"id": "p2", "goal": "Phase 2", "skills": [], "gates": [], "depends_on": ["p1"]},
            ],
        })
        task_def = TaskDefinition.from_json(task_json)
        planner = DAGPlanner(task_def)

        # Should raise ValueError for circular dependency
        with pytest.raises(ValueError) as exc_info:
            planner.plan()
        assert "Circular dependency" in str(exc_info.value)

    def test_missing_dependency(self):
        """Test: missing dependency raises error."""
        task_json = json.dumps({
            "task_id": "missing-dep",
            "phases": [
                {"id": "p1", "goal": "Phase 1", "skills": [], "gates": [], "depends_on": ["nonexistent"]},
            ],
        })
        task_def = TaskDefinition.from_json(task_json)
        planner = DAGPlanner(task_def)

        with pytest.raises(ValueError) as exc_info:
            planner.plan()
        assert "unknown phase" in str(exc_info.value)

    def test_get_next_phase(self):
        """Test: get next executable phase (dependencies satisfied)."""
        task_json = json.dumps({
            "task_id": "next-phase-test",
            "phases": [
                {"id": "p1", "goal": "Phase 1", "skills": [], "gates": []},
                {"id": "p2", "goal": "Phase 2", "skills": [], "gates": [], "depends_on": ["p1"]},
                {"id": "p3", "goal": "Phase 3", "skills": [], "gates": [], "depends_on": ["p2"]},
            ],
        })
        task_def = TaskDefinition.from_json(task_json)
        planner = DAGPlanner(task_def)

        # After completing p1
        next_phase = planner.get_next_phase("p1", {"p1"})
        assert next_phase == "p2"

        # After completing p1 and p2
        next_phase = planner.get_next_phase("p2", {"p1", "p2"})
        assert next_phase == "p3"

        # After completing all
        next_phase = planner.get_next_phase("p3", {"p1", "p2", "p3"})
        assert next_phase is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
