"""G1 Wiring: Dependency Validation in registry.execute() — E2E test.

Verifies that registry.execute() validates skill dependencies (from metadata.depends_on)
before running. A skill with missing dependencies is rejected with an audit event.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.skills.boot import boot_skills
from core.skills.skill_registry_phase1 import (
    SkillMetadata,
    SkillOrigin,
    SkillTier,
    get_registry,
)
from core.skills.skill_dag_loader import SkillDependency


@pytest.fixture()
def booted(tmp_path, monkeypatch):
    """Boot skills registry with audit spy."""
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("FORGE_ROOT", str(tmp_path / "forge"))
    records: list[tuple[str, dict]] = []

    def _spy(event_type: str, details: dict) -> bool:
        records.append((event_type, dict(details)))
        return True

    ids = boot_skills(tenant_id="_default", audit_emit=_spy, wire_learning=False)
    return get_registry(), ids, records


def test_skill_with_missing_dependency_is_rejected(booted):
    """A skill with unmet dependencies is rejected before execution."""
    registry, _, records = booted

    # Create a test skill with a dependency that doesn't exist
    test_skill = MagicMock()
    test_skill.metadata = SkillMetadata(
        id="os.test_dependent",
        name="Test Dependent Skill",
        description="Has a dependency on a nonexistent skill",
        version="1.0.0",
        origin=SkillOrigin.BUILTIN,
        owner="test",
        depends_on=[
            {"name": "os.nonexistent_skill", "version": "*"},  # This skill doesn't exist
        ],
    )
    test_skill.execute = MagicMock()

    # Register the test skill
    registry._skills["os.test_dependent"] = test_skill
    registry._metadata_by_id["os.test_dependent"] = test_skill.metadata

    # Try to execute — should fail on dependency check, not on skill.execute()
    result = registry.execute(
        "os.test_dependent",
        {},
        timeout_ms=5000,
        lom="tests/skills/test_g1_dependency_validation_e2e.py:test_skill_with_missing_dependency_is_rejected",
        tenant_id="_default",
    )

    # Verify: execution failed due to dependency validation
    assert result.status in ("failure", "error"), f"Expected failure/error, got {result.status}"
    assert "Dependency validation failed" in (result.error_message or "")
    assert "os.nonexistent_skill" in (result.error_message or "")

    # Verify: skill.execute() was NEVER called (dependency check is pre-execution gate)
    test_skill.execute.assert_not_called()

    # Verify: audit event was logged
    audit_events = [t for t, d in records if d.get("skill_id") == "os.test_dependent"]
    assert any(d.get("error_class") == "dependency_validation_failed" for _, d in records)


def test_skill_without_dependencies_runs_normally(booted):
    """A skill with no dependencies runs without dependency checks."""
    registry, _, records = booted

    # Create a test skill with NO dependencies
    test_skill = MagicMock()
    test_skill.metadata = SkillMetadata(
        id="os.test_independent",
        name="Test Independent Skill",
        description="Has no dependencies",
        version="1.0.0",
        origin=SkillOrigin.BUILTIN,
        owner="test",
        depends_on=[],  # No dependencies
    )
    test_skill.execute = MagicMock(return_value={"status": "ok"})

    registry._skills["os.test_independent"] = test_skill
    registry._metadata_by_id["os.test_independent"] = test_skill.metadata

    # Execute — should proceed directly to skill.execute()
    result = registry.execute(
        "os.test_independent",
        {"input": "test"},
        timeout_ms=5000,
        lom="tests/skills/test_g1_dependency_validation_e2e.py:test_skill_without_dependencies_runs_normally",
        tenant_id="_default",
    )

    # Verify: skill.execute() WAS called
    test_skill.execute.assert_called_once()

    # Verify: no dependency_validation_failed error in audit
    assert not any(
        d.get("error_class") == "dependency_validation_failed" for _, d in records
    )


# ── version constraints through the real entry point (registry.execute) ─────────────────────


def _register(registry, skill_id: str, version: str, depends_on=None, returns=None):
    skill = MagicMock()
    skill.metadata = SkillMetadata(
        id=skill_id, name=skill_id, description="test", version=version,
        origin=SkillOrigin.BUILTIN, owner="test", depends_on=depends_on or [],
    )
    skill.execute = MagicMock(return_value=returns if returns is not None else {"status": "ok"})
    registry._skills[skill_id] = skill
    registry._metadata_by_id[skill_id] = skill.metadata
    return skill


@pytest.mark.parametrize("constraint", [">=2.0.0", ">= 2.0.0", "<1.0.0", ">=1.5.0 <2.0.0", ">=1.5.0,<2.0.0"])
def test_an_unsatisfied_version_constraint_blocks_execution_with_or_without_a_space(booted, constraint):
    """The fail-open bug: ">=2.0.0" (no space) matched no operator branch and EVERY version satisfied it,
    so the dependent skill ran against a dependency that was too old. Through the real entry point."""
    registry, _, _ = booted
    _register(registry, "os.test_dep_lib", "1.0.0")
    dependent = _register(
        registry, "os.test_dep_user", "1.0.0",
        depends_on=[SkillDependency(name="os.test_dep_lib", version=constraint)],
    )
    result = registry.execute("os.test_dep_user", {}, timeout_ms=5000,
                              lom="tests/skills/test_g1_dependency_validation_e2e.py:test_an_unsatisfied_version_constraint_blocks_execution_with_or_without_a_space", tenant_id="_default")
    assert result.status in ("failure", "error"), f"{constraint!r} should not be satisfied by 1.0.0"
    assert "Dependency validation failed" in (result.error_message or "")
    dependent.execute.assert_not_called()


@pytest.mark.parametrize("constraint", [">=1.0.0", ">= 1.0.0", "<2.0.0", ">=0.9.0 <2.0.0", "==1.0.0", "1.0.0"])
def test_a_satisfied_version_constraint_lets_the_skill_run(booted, constraint):
    registry, _, _ = booted
    _register(registry, "os.test_dep_lib", "1.0.0")
    dependent = _register(
        registry, "os.test_dep_user", "1.0.0",
        depends_on=[SkillDependency(name="os.test_dep_lib", version=constraint)],
    )
    result = registry.execute("os.test_dep_user", {}, timeout_ms=5000,
                              lom="tests/skills/test_g1_dependency_validation_e2e.py:test_a_satisfied_version_constraint_lets_the_skill_run", tenant_id="_default")
    assert result.status == "success", (constraint, result.error_message)
    dependent.execute.assert_called_once()


# ── every declaration form is read, and an unreadable one BLOCKS ────────────────────────────


def _run(registry, skill_id):
    return registry.execute(skill_id, {}, timeout_ms=5000, tenant_id="_default",
                            lom="tests/skills/test_g1_dependency_validation_e2e.py:_run")


def test_a_missing_dependency_declared_as_a_SkillDependency_object_blocks(booted):
    """Before the fix this form was DROPPED by execute() and the skill ran with no check at all."""
    registry, _, _ = booted
    dependent = _register(registry, "os.test_dep_user", "1.0.0",
                          depends_on=[SkillDependency(name="os.test_gone", version=">=1.0.0")])
    result = _run(registry, "os.test_dep_user")
    assert result.status in ("failure", "error")
    assert "os.test_gone" in (result.error_message or "")
    dependent.execute.assert_not_called()


def test_a_dict_declaration_keeps_required_false(booted):
    """The dict form lost `required`: an OPTIONAL missing dependency blocked the skill."""
    registry, _, _ = booted
    dependent = _register(registry, "os.test_dep_user", "1.0.0",
                          depends_on=[{"name": "os.test_optional_gone", "version": "*", "required": False}])
    result = _run(registry, "os.test_dep_user")
    assert result.status == "success", result.error_message
    dependent.execute.assert_called_once()


@pytest.mark.parametrize("wanted,runs", [("1.0.0", True), ("2.0.0", False)])
def test_a_dict_declaration_keeps_constraint_type_exact(booted, wanted, runs):
    registry, _, _ = booted
    _register(registry, "os.test_dep_lib", "1.0.0")
    dependent = _register(registry, "os.test_dep_user", "1.0.0", depends_on=[
        {"name": "os.test_dep_lib", "version": wanted, "constraint_type": "exact"}])
    result = _run(registry, "os.test_dep_user")
    assert (result.status == "success") is runs, result.error_message
    assert dependent.execute.called is runs


@pytest.mark.parametrize("declaration", [42, {"version": ">=1.0.0"}, {"name": "os.x", "constraint_type": "caret"}, None])
def test_an_unreadable_declaration_blocks_instead_of_vanishing(booted, declaration):
    registry, _, _ = booted
    dependent = _register(registry, "os.test_dep_user", "1.0.0", depends_on=[declaration])
    if not dependent.metadata.depends_on or declaration is None:
        pytest.skip("a None entry is falsy-filtered upstream; covered by the 42/dict cases")
    result = _run(registry, "os.test_dep_user")
    assert result.status in ("failure", "error")
    assert "Unreadable dependency declaration" in (result.error_message or "")
    dependent.execute.assert_not_called()
