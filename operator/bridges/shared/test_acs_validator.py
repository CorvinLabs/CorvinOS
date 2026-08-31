"""Tests for acs_validator.py — ADR-0104 M1."""
from __future__ import annotations

import pytest

try:
    from . import acs_validator as _v
except ImportError:
    import acs_validator as _v  # type: ignore[no-redef]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _minimal() -> dict:
    return {
        "awp": "1.0.0",
        "workflow": {
            "name": "test-workflow",
            "description": "A minimal test workflow",
            "version": "1.0.0",
        },
        "orchestration": {
            "engine": "dag",
            "graph": [
                {"id": "step_one", "agent": "agents/step_one", "depends_on": []},
            ],
        },
    }


def _with_delegation(max_depth: int = 3) -> dict:
    d = _minimal()
    d["orchestration"]["delegation_loop"] = {
        "budget": {
            "max_loops": 10,
            "max_depth": max_depth,
            "max_total_workers": 50,
        }
    }
    return d


# ---------------------------------------------------------------------------
# R1: SemVer
# ---------------------------------------------------------------------------

def test_r1_valid_semver():
    d = _minimal()
    r = _v.validate_workflow_dict(d)
    assert r.ok
    assert not r.errors


def test_r1_invalid_semver_no_patch():
    d = _minimal()
    d["awp"] = "1.0"
    r = _v.validate_workflow_dict(d)
    assert not r.ok
    assert any(i.rule_id == "R1" for i in r.errors)


def test_r1_invalid_semver_v_prefix():
    d = _minimal()
    d["awp"] = "v1.0.0"
    r = _v.validate_workflow_dict(d)
    assert not r.ok


def test_r1_missing():
    d = _minimal()
    del d["awp"]
    r = _v.validate_workflow_dict(d)
    assert not r.ok
    assert any(i.rule_id == "R1" for i in r.errors)


# ---------------------------------------------------------------------------
# R2: Workflow name
# ---------------------------------------------------------------------------

def test_r2_valid_kebab():
    d = _minimal()
    d["workflow"]["name"] = "my-cool-workflow"
    r = _v.validate_workflow_dict(d)
    assert r.ok or not any(i.rule_id == "R2" for i in r.errors)


def test_r2_invalid_uppercase():
    d = _minimal()
    d["workflow"]["name"] = "MyWorkflow"
    r = _v.validate_workflow_dict(d)
    assert any(i.rule_id == "R2" for i in r.errors)


def test_r2_invalid_starts_with_digit():
    d = _minimal()
    d["workflow"]["name"] = "1workflow"
    r = _v.validate_workflow_dict(d)
    assert any(i.rule_id == "R2" for i in r.errors)


# ---------------------------------------------------------------------------
# R5: Unique agent IDs
# ---------------------------------------------------------------------------

def test_r5_duplicate_id():
    d = _minimal()
    d["orchestration"]["graph"].append({"id": "step_one", "agent": "agents/other"})
    r = _v.validate_workflow_dict(d)
    assert any(i.rule_id == "R5" for i in r.errors)


# ---------------------------------------------------------------------------
# R6: DAG acyclicity
# ---------------------------------------------------------------------------

def test_r6_cycle_detected():
    d = _minimal()
    d["orchestration"]["graph"] = [
        {"id": "a", "agent": "agents/a", "depends_on": ["b"]},
        {"id": "b", "agent": "agents/b", "depends_on": ["a"]},
    ]
    r = _v.validate_workflow_dict(d)
    assert any(i.rule_id == "R6" for i in r.errors)


def test_r6_no_cycle():
    d = _minimal()
    d["orchestration"]["graph"] = [
        {"id": "a", "agent": "agents/a", "depends_on": []},
        {"id": "b", "agent": "agents/b", "depends_on": ["a"]},
        {"id": "c", "agent": "agents/c", "depends_on": ["b"]},
    ]
    r = _v.validate_workflow_dict(d)
    assert not any(i.rule_id == "R6" for i in r.errors)


# ---------------------------------------------------------------------------
# R7: Dependency resolution
# ---------------------------------------------------------------------------

def test_r7_unknown_dependency():
    d = _minimal()
    d["orchestration"]["graph"][0]["depends_on"] = ["nonexistent_node"]
    r = _v.validate_workflow_dict(d)
    assert any(i.rule_id == "R7" for i in r.errors)


# ---------------------------------------------------------------------------
# R12: Agent ID format
# ---------------------------------------------------------------------------

def test_r12_valid():
    agents = [{"awp_agent": "1.0.0", "identity": {"id": "research_analyst", "role": "r"},
               "model": {"name": "claude-sonnet-4-6"}, "output": {"format": "json"}}]
    r = _v.validate_workflow_dict(_minimal(), agents_data=agents)
    assert not any(i.rule_id == "R12" for i in r.errors)


def test_r12_invalid_uppercase():
    agents = [{"identity": {"id": "ResearchAnalyst"}}]
    r = _v.validate_workflow_dict(_minimal(), agents_data=agents)
    assert any(i.rule_id == "R12" for i in r.errors)


def test_r12_missing():
    agents = [{"identity": {}}]
    r = _v.validate_workflow_dict(_minimal(), agents_data=agents)
    assert any(i.rule_id == "R12" for i in r.errors)


# ---------------------------------------------------------------------------
# R19-R20: Code mode rules
# ---------------------------------------------------------------------------

def test_r19_codemode_requires_tools():
    agents = [{
        "identity": {"id": "agent_a"},
        "capabilities": {
            "codemode": {"enabled": True, "language": "python"},
            "tools": {"enabled": False},
            "sandbox": {"type": "subprocess"},
        },
    }]
    r = _v.validate_workflow_dict(_minimal(), agents_data=agents)
    assert any(i.rule_id == "R19" for i in r.errors)


def test_r20_codemode_no_sandbox_none():
    agents = [{
        "identity": {"id": "agent_b"},
        "capabilities": {
            "codemode": {"enabled": True},
            "tools": {"enabled": True},
            "sandbox": {"type": "none"},
        },
    }]
    r = _v.validate_workflow_dict(_minimal(), agents_data=agents)
    assert any(i.rule_id == "R20" for i in r.errors)


# ---------------------------------------------------------------------------
# R27-R28: Evaluation rules
# ---------------------------------------------------------------------------

def test_r27_invalid_metric_kind():
    d = _minimal()
    d["observability"] = {
        "evaluation": {
            "enabled": True,
            "metrics": [{"name": "quality", "kind": "invalid_kind", "weight": 1.0}],
            "thresholds": {"accept": 0.85, "retry": 0.65, "fail": 0.40},
        }
    }
    r = _v.validate_workflow_dict(d)
    assert any(i.rule_id == "R27" for i in r.errors)


def test_r28_thresholds_consistent():
    d = _minimal()
    d["observability"] = {
        "evaluation": {
            "enabled": True,
            "metrics": [{"name": "q", "kind": "schema", "weight": 1.0}],
            "thresholds": {"accept": 0.50, "retry": 0.80, "fail": 0.40},  # accept < retry → fail
        }
    }
    r = _v.validate_workflow_dict(d)
    assert any(i.rule_id == "R28" for i in r.errors)


def test_r28_thresholds_valid():
    d = _minimal()
    d["observability"] = {
        "evaluation": {
            "enabled": True,
            "metrics": [{"name": "q", "kind": "schema", "weight": 1.0}],
            "thresholds": {"accept": 0.85, "retry": 0.65, "fail": 0.40},
        }
    }
    r = _v.validate_workflow_dict(d)
    assert not any(i.rule_id in ("R27", "R28", "R29") for i in r.errors)


# ---------------------------------------------------------------------------
# R31-R32: A4 max_depth
# ---------------------------------------------------------------------------

def test_r31_max_depth_required():
    d = _minimal()
    d["orchestration"]["delegation_loop"] = {
        "budget": {"max_loops": 10}  # no max_depth
    }
    r = _v.validate_workflow_dict(d)
    assert any(i.rule_id == "R31" for i in r.errors)


def test_r31_max_depth_negative():
    d = _with_delegation(max_depth=-1)
    r = _v.validate_workflow_dict(d)
    assert any(i.rule_id == "R31" for i in r.errors)


def test_r32_max_depth_exceeds_ceiling():
    d = _with_delegation(max_depth=11)
    r = _v.validate_workflow_dict(d)
    assert any(i.rule_id == "R32" for i in r.errors)


def test_r32_max_depth_warning_at_6():
    d = _with_delegation(max_depth=6)
    r = _v.validate_workflow_dict(d)
    assert any(i.rule_id == "R32" and i.severity == "WARNING" for i in r.issues)


def test_r32_valid_depth():
    d = _with_delegation(max_depth=3)
    r = _v.validate_workflow_dict(d)
    assert not any(i.rule_id in ("R31", "R32") for i in r.errors)


# ---------------------------------------------------------------------------
# R35-R36: ACS-1 worker/wall-time budget ceilings (silent-inflation backstop)
# ---------------------------------------------------------------------------

def test_r35_max_total_workers_exceeds_ceiling():
    """The a47c6d3 inflation to 400 workers must now fail LOUDLY."""
    d = _with_delegation(max_depth=3)
    d["orchestration"]["delegation_loop"]["budget"]["max_total_workers"] = 400
    r = _v.validate_workflow_dict(d)
    assert any(i.rule_id == "R35" for i in r.errors)


def test_r35_max_total_workers_negative():
    d = _with_delegation(max_depth=3)
    d["orchestration"]["delegation_loop"]["budget"]["max_total_workers"] = -1
    r = _v.validate_workflow_dict(d)
    assert any(i.rule_id == "R35" for i in r.errors)


def test_r35_max_total_workers_warning_above_16():
    d = _with_delegation(max_depth=3)
    d["orchestration"]["delegation_loop"]["budget"]["max_total_workers"] = 32
    r = _v.validate_workflow_dict(d)
    assert any(i.rule_id == "R35" and i.severity == "WARNING" for i in r.issues)
    assert not any(i.rule_id == "R35" for i in r.errors)


def test_r35_sane_worker_count_ok():
    d = _with_delegation(max_depth=3)
    d["orchestration"]["delegation_loop"]["budget"]["max_total_workers"] = 8
    r = _v.validate_workflow_dict(d)
    assert not any(i.rule_id == "R35" for i in r.issues)


def test_r36_max_wall_time_exceeds_ceiling():
    """The a47c6d3 inflation to 360000s (100 h) must now fail LOUDLY."""
    d = _with_delegation(max_depth=3)
    d["orchestration"]["delegation_loop"]["budget"]["max_wall_time"] = 360000
    r = _v.validate_workflow_dict(d)
    assert any(i.rule_id == "R36" for i in r.errors)


def test_r36_sane_wall_time_ok():
    d = _with_delegation(max_depth=3)
    d["orchestration"]["delegation_loop"]["budget"]["max_wall_time"] = 3600
    r = _v.validate_workflow_dict(d)
    assert not any(i.rule_id == "R36" for i in r.issues)


# ---------------------------------------------------------------------------
# R13: Reserved state keys
# ---------------------------------------------------------------------------

def test_r13_reserved_key():
    d = _minimal()
    d["state"] = {"initial": {"_meta": "forbidden", "valid_key": 1}}
    r = _v.validate_workflow_dict(d)
    assert any(i.rule_id == "R13" for i in r.errors)


def test_r13_valid_state():
    d = _minimal()
    d["state"] = {"initial": {"topic": "AI safety", "max_results": 10}}
    r = _v.validate_workflow_dict(d)
    assert not any(i.rule_id == "R13" for i in r.errors)


# ---------------------------------------------------------------------------
# Validation result helper methods
# ---------------------------------------------------------------------------

def test_validation_result_ok_property():
    r = _v.ValidationResult("test-wf")
    assert r.ok
    r.add_error("R1", "test error")
    assert not r.ok


def test_validation_result_str():
    issue = _v.ValidationIssue("R1", "ERROR", "bad version", "awp")
    assert "R1" in str(issue)
    assert "ERROR" in str(issue)
    assert "bad version" in str(issue)
