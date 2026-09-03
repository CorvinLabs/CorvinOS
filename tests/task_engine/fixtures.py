"""Test fixtures for task engine (3-phase mock DAG)."""

import json
from core.task_engine.task_def import TaskDefinition


def mock_3phase_dag() -> TaskDefinition:
    """3-phase task DAG for E2E testing."""
    task_json = json.dumps({
        "task_id": "test-3phase-dag",
        "tenant_id": "_default",
        "description": "3-phase E2E test DAG",
        "phases": [
            {
                "id": "phase-1-audit",
                "goal": "Phase 1: Audit (entry point)",
                "skills": ["mock-skill-1"],
                "gates": [
                    {"type": "finding_count", "max_critical": 0}
                ],
                "depends_on": [],
                "timeout_hours": 1,
            },
            {
                "id": "phase-2-refactor",
                "goal": "Phase 2: Refactor (depends on phase 1)",
                "skills": ["mock-skill-2"],
                "gates": [
                    {"type": "test_pass_rate", "min": 1.0}
                ],
                "depends_on": ["phase-1-audit"],
                "timeout_hours": 4,
            },
            {
                "id": "phase-3-test",
                "goal": "Phase 3: Test (depends on phase 2)",
                "skills": ["mock-skill-3"],
                "gates": [
                    {"type": "audit_trail_verified", "must_verify": True}
                ],
                "depends_on": ["phase-2-refactor"],
                "timeout_hours": 2,
            },
        ],
        "autonomy_level": 3,
        "success_criteria": {
            "all_phases_complete": True,
            "audit_trail_verified": True,
        },
        "timeout_weeks": 1,
    })
    return TaskDefinition.from_json(task_json)


def mock_skill_1(input_data):
    """Mock skill for phase 1."""
    return {
        "phase": "1",
        "status": "complete",
        "audit_findings": [],
        "state": {"code_audited": True},
    }


def mock_skill_2(input_data):
    """Mock skill for phase 2."""
    return {
        "phase": "2",
        "status": "complete",
        "refactored_files": 5,
        "state": {"code_refactored": True},
    }


def mock_skill_3(input_data):
    """Mock skill for phase 3."""
    return {
        "phase": "3",
        "status": "complete",
        "tests_passed": 42,
        "state": {"tests_run": True},
    }
