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
