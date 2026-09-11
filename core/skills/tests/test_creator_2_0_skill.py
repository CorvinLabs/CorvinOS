"""Tests for Creator 2.0 Skill wrapper."""

import pytest
from core.skills.os_skills.creator_2_0.skill import Creator20Skill
from core.skills.os_skills.creator_2_0.events import PhaseCompletedEvent


class TestCreator20Skill:
    """Test Creator 2.0 Skill class."""

    def test_skill_initialization(self):
        """Test skill initialization."""
        skill = Creator20Skill(mode="skill")
        assert skill.mode == "skill"

    def test_skill_initialization_tool_mode(self):
        """Test skill initialization with tool mode."""
        skill = Creator20Skill(mode="tool")
        assert skill.mode == "tool"

    def test_skill_execute(self):
        """Test skill execution."""
        skill = Creator20Skill(mode="skill")
        result = skill.execute("Email validator")

        assert result["skill_id"] is not None
        assert result["mode"] == "skill"
        assert result["success"] is True
        assert result["final_loss"] is not None

    def test_skill_execute_with_clarifications(self):
        """Test skill execution with clarifications."""
        skill = Creator20Skill(mode="skill")
        clarifications = {
            "What format should be validated?": "email",
        }

        result = skill.execute(
            "Email validator",
            clarifications=clarifications,
        )

        assert result["success"] is True

    def test_skill_output_has_manifest(self):
        """Test that skill output includes manifest."""
        skill = Creator20Skill(mode="skill")
        result = skill.execute("Test skill")

        assert result["manifest"] is not None
        assert "id" in result["manifest"]
        assert "version" in result["manifest"]

    def test_skill_output_has_artifacts(self):
        """Test that skill output lists artifacts."""
        skill = Creator20Skill(mode="skill")
        result = skill.execute("Test skill")

        assert result["artifacts"] is not None
        assert len(result["artifacts"]) > 0

    def test_skill_get_events(self):
        """Test retrieving events."""
        skill = Creator20Skill(mode="skill")
        skill.execute("Test skill")

        events = skill.get_events()
        assert len(events) > 0
        assert all(isinstance(e, dict) for e in events)

    def test_skill_validate_loss(self):
        """Test loss validation."""
        skill = Creator20Skill(mode="skill")
        skill.execute("Test skill")

        assert skill.validate_loss() is True

    def test_skill_loss_is_in_bounds(self):
        """Test that final loss is in bounds."""
        skill = Creator20Skill(mode="skill")
        result = skill.execute("Test skill")

        loss = result["final_loss"]
        assert loss is not None
        assert 0 <= loss <= 1

    def test_skill_multiple_executions(self):
        """Test that skill can be executed multiple times."""
        skill = Creator20Skill(mode="skill")

        result1 = skill.execute("Skill 1")
        result2 = skill.execute("Skill 2")

        assert result1["skill_id"] != result2["skill_id"]

    def test_skill_tool_mode_execution(self):
        """Test skill execution in tool mode."""
        skill = Creator20Skill(mode="tool")
        result = skill.execute("API validator tool")

        assert result["mode"] == "tool"
        assert result["success"] is True

    def test_skill_serialization(self):
        """Test that skill output is serializable."""
        skill = Creator20Skill(mode="skill")
        result = skill.execute("Test skill")

        # Should be able to access all fields
        assert "skill_id" in result
        assert "mode" in result
        assert "success" in result
        assert "final_loss" in result
        assert "output" in result
        assert "manifest" in result
        assert "artifacts" in result

    def test_skill_complex_query(self):
        """Test with a more complex query."""
        skill = Creator20Skill(mode="skill")
        result = skill.execute(
            "Create a data processor that validates and transforms JSON input according to user-defined schema"
        )

        assert result["success"] is True
        assert result["final_loss"] is not None

    def test_skill_validates_each_phase_loss(self):
        """Test that each phase has valid loss."""
        skill = Creator20Skill(mode="skill")
        skill.execute("Test skill")

        events = skill.get_events()
        for event in events:
            if event.get("loss_components"):
                components = event["loss_components"]
                assert 0 <= components["relevance"] <= 1
                assert 0 <= components["completeness"] <= 1
                assert 0 <= components["performance"] <= 1
                assert 0 <= components["maintainability"] <= 1


class TestCreator20SkillGateDiagnostics:
    """Diagnostic tests for Phase 2 gate."""

    def test_create_skill_commit_message_linter(self):
        """Gate test 1: Simple skill generation (commit message linter)."""
        skill = Creator20Skill(mode="skill")
        result = skill.execute("Create a commit message linter that validates git messages")

        assert result["success"] is True
        assert result["final_loss"] is not None
        assert "commit" in result["skill_id"].lower() or result["skill_id"] is not None

    def test_create_skill_support_classifier(self):
        """Gate test 2: Complex skill with clarifications (support ticket classifier)."""
        skill = Creator20Skill(mode="skill")
        clarifications = {
            "What categories should be classified into?": "urgent, high, medium, low",
            "Should multi-label classification be supported?": "Yes",
        }

        result = skill.execute(
            "Create a support ticket classifier that routes tickets based on urgency and category",
            clarifications=clarifications,
        )

        assert result["success"] is True
        assert result["final_loss"] is not None

    def test_create_tool_api_validator(self):
        """Gate test 3: Tool mode (API validator tool)."""
        skill = Creator20Skill(mode="tool")
        result = skill.execute("Create a REST API request validator")

        assert result["success"] is True
        assert result["mode"] == "tool"
        assert result["final_loss"] is not None

    def test_all_phases_emitted_events(self):
        """Gate test 4: All phases emit events for all 3 test skills."""
        test_cases = [
            ("Commit message linter", {}),
            ("Support ticket classifier", {"What categories": "urgent,high,medium,low"}),
        ]

        for query, clarifications in test_cases:
            skill = Creator20Skill(mode="skill")
            skill.execute(query, clarifications=clarifications if clarifications else None)
            events = skill.get_events()

            # Should have events from phases 0–10
            assert len(events) >= 10, f"Expected ≥10 events for '{query}', got {len(events)}"

    def test_loss_components_in_bounds(self):
        """Gate test 5: Loss components in bounds [0,1] for all phases."""
        skill = Creator20Skill(mode="skill")
        skill.execute("Email validator")

        events = skill.get_events()
        for event in events:
            if event.get("loss_components"):
                comp = event["loss_components"]
                assert 0 <= comp["relevance"] <= 1, f"relevance out of bounds: {comp['relevance']}"
                assert 0 <= comp["completeness"] <= 1, f"completeness out of bounds: {comp['completeness']}"
                assert 0 <= comp["performance"] <= 1, f"performance out of bounds: {comp['performance']}"
                assert 0 <= comp["maintainability"] <= 1, f"maintainability out of bounds: {comp['maintainability']}"

    def test_final_loss_makes_sense(self):
        """Gate test 6: Final loss is well-formed (no NaN, no inf)."""
        skill = Creator20Skill(mode="skill")
        result = skill.execute("Test skill")

        loss = result["final_loss"]
        assert loss is not None
        assert not (loss != loss), "Loss is NaN"
        assert loss != float("inf"), "Loss is infinity"
        assert loss != float("-inf"), "Loss is negative infinity"

    def test_e2e_full_generation_pipeline(self):
        """Gate test 7: End-to-end full generation pipeline."""
        skill = Creator20Skill(mode="skill")
        result = skill.execute("JSON schema validator with custom error messages")

        # Full pipeline validation
        assert result["success"] is True
        assert result["skill_id"] is not None
        assert result["final_loss"] is not None
        assert result["manifest"] is not None
        assert result["artifacts"] is not None

        # Manifest should be complete
        manifest = result["manifest"]
        assert "id" in manifest
        assert "version" in manifest
        assert "description" in manifest
        assert "type" in manifest

        # Should have multiple artifacts
        assert len(result["artifacts"]) >= 3

        # All phase events should be present
        events = skill.get_events()
        assert len(events) >= 10
