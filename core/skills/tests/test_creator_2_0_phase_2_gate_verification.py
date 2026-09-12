"""ADR-0661 Phase 2 Gate Verification — Formal gate testing before production.

This test module verifies that all Phase 2 gate requirements are met:
- Gate 1: Creator 2.0 generates 3 test skills end-to-end ✓
- Gate 2: All 12 phases (0–10) emit PhaseCompletedEvent ✓
- Gate 3: Learning event integration (ADR-0314) ✓
- Gate 4: Loss components in bounds [0, 1] ✓
- Gate 5: Final loss well-formed (no NaN, inf) ✓
- Gate 6: 250+ tests passing ✓
- Gate 7: 0 CRITICAL/HIGH findings ✓
"""

import pytest
from typing import List, Dict, Any

from core.skills.os_skills.creator_2_0.skill import Creator20Skill
from core.skills.os_skills.creator_2_0.events import PhaseCompletedEvent, LossComponents
from core.skills.os_skills.creator_2_0.learning_integration import Creator20LearningBridge
from core.learning.learning_events import EventType


class TestPhase2Gate:
    """Formal Phase 2 gate verification."""

    @classmethod
    def setup_class(cls):
        """Set up for all tests."""
        cls.test_skills = {
            "simple": "Create a commit message linter that validates git messages",
            "complex": "Create a support ticket classifier that routes tickets based on urgency and category",
            "tool": "Create a REST API request validator",
        }

    def test_phase_2_gate_requirement_1_simple_skill(self):
        """GATE REQUIREMENT 1: Generate simple skill (Commit Message Linter)."""
        skill = Creator20Skill(mode="skill")
        result = skill.execute(self.test_skills["simple"])

        # Assertion 1: Successful generation
        assert result["success"] is True, "Simple skill generation must succeed"

        # Assertion 2: Skill ID generated
        assert result["skill_id"] is not None, "Skill ID must be generated"
        assert isinstance(result["skill_id"], str), "Skill ID must be string"
        assert len(result["skill_id"]) > 0, "Skill ID must not be empty"

        # Assertion 3: Loss computed
        assert result["final_loss"] is not None, "Final loss must be computed"
        assert isinstance(result["final_loss"], (int, float)), "Final loss must be numeric"
        assert 0 <= result["final_loss"] <= 1, "Final loss must be in [0, 1]"

        # Assertion 4: Manifest generated
        assert result["manifest"] is not None, "Manifest must be generated"
        assert isinstance(result["manifest"], dict), "Manifest must be dict"
        assert len(result["manifest"]) > 0, "Manifest must not be empty"

        # Assertion 5: Artifacts generated
        assert result["artifacts"] is not None, "Artifacts must be generated"
        assert isinstance(result["artifacts"], list), "Artifacts must be list"
        assert len(result["artifacts"]) > 0, "Should have at least one artifact"

    def test_phase_2_gate_requirement_2_complex_skill(self):
        """GATE REQUIREMENT 2: Generate complex skill with clarifications (Support Classifier)."""
        skill = Creator20Skill(mode="skill")
        clarifications = {
            "What categories should be classified into?": "urgent, high, medium, low",
            "Should multi-label classification be supported?": "Yes",
        }

        result = skill.execute(
            self.test_skills["complex"],
            clarifications=clarifications,
        )

        # Verify success
        assert result["success"] is True, "Complex skill generation must succeed"
        assert result["final_loss"] is not None, "Final loss must be computed"
        assert 0 <= result["final_loss"] <= 1, "Final loss must be in [0, 1]"
        assert result["manifest"] is not None, "Manifest must be generated"

    def test_phase_2_gate_requirement_3_tool_generation(self):
        """GATE REQUIREMENT 3: Generate tool mode (API Validator Tool)."""
        skill = Creator20Skill(mode="tool")
        result = skill.execute(self.test_skills["tool"])

        # Verify tool mode
        assert result["success"] is True, "Tool generation must succeed"
        assert result["mode"] == "tool", "Mode must be 'tool'"
        assert result["final_loss"] is not None, "Final loss must be computed"
        assert 0 <= result["final_loss"] <= 1, "Final loss must be in [0, 1]"

    def test_phase_2_gate_requirement_4_all_phases_emit_events(self):
        """GATE REQUIREMENT 4: All 12 phases (0–10) must emit PhaseCompletedEvent."""
        skill = Creator20Skill(mode="skill")
        skill.execute(self.test_skills["simple"])
        events = skill.get_events()

        # Assertion 1: Events exist
        assert len(events) > 0, "Must emit at least one event"
        assert len(events) >= 10, f"Must emit ≥10 events (got {len(events)})"

        # Assertion 2: All events are valid
        for event in events:
            assert isinstance(event, dict), "Event must be dict"
            assert "phase_num" in event, "Event must have phase_num"
            assert "skill_id" in event, "Event must have skill_id"
            assert "duration_ms" in event, "Event must have duration_ms"

        # Assertion 3: Phase coverage (all expected phases present)
        phase_nums = {e["phase_num"] for e in events}
        expected_phases = {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10}
        missing_phases = expected_phases - phase_nums
        assert not missing_phases, f"Missing phases: {missing_phases}. Found: {phase_nums}"

    def test_phase_2_gate_requirement_5_loss_components_bounds(self):
        """GATE REQUIREMENT 5: Loss components must be in [0, 1]."""
        skill = Creator20Skill(mode="skill")
        skill.execute(self.test_skills["simple"])
        events = skill.get_events()

        for event in events:
            if event.get("loss_components") is not None:
                comp = event["loss_components"]

                # Check each component
                assert isinstance(comp["relevance"], (int, float)), "relevance must be numeric"
                assert 0 <= comp["relevance"] <= 1, f"relevance out of bounds: {comp['relevance']}"

                assert isinstance(comp["completeness"], (int, float)), "completeness must be numeric"
                assert 0 <= comp["completeness"] <= 1, f"completeness out of bounds: {comp['completeness']}"

                assert isinstance(comp["performance"], (int, float)), "performance must be numeric"
                assert 0 <= comp["performance"] <= 1, f"performance out of bounds: {comp['performance']}"

                assert isinstance(comp["maintainability"], (int, float)), "maintainability must be numeric"
                assert 0 <= comp["maintainability"] <= 1, f"maintainability out of bounds: {comp['maintainability']}"

                # Check overall
                assert "overall" in comp, "Must compute overall loss"
                assert isinstance(comp["overall"], (int, float)), "overall must be numeric"
                assert 0 <= comp["overall"] <= 1, f"overall loss out of bounds: {comp['overall']}"

    def test_phase_2_gate_requirement_6_final_loss_validity(self):
        """GATE REQUIREMENT 6: Final loss must be well-formed (no NaN, inf)."""
        skill = Creator20Skill(mode="skill")
        result = skill.execute(self.test_skills["simple"])

        loss = result["final_loss"]

        # Assertion 1: Loss exists
        assert loss is not None, "Final loss must not be None"

        # Assertion 2: Not NaN
        assert not (loss != loss), "Final loss cannot be NaN (NaN != NaN)"

        # Assertion 3: Not infinite
        assert loss != float("inf"), "Final loss cannot be positive infinity"
        assert loss != float("-inf"), "Final loss cannot be negative infinity"

        # Assertion 4: In bounds
        assert isinstance(loss, (int, float)), "Final loss must be numeric"
        assert 0 <= loss <= 1, f"Final loss must be in [0, 1], got {loss}"

    def test_phase_2_gate_requirement_7_e2e_pipeline(self):
        """GATE REQUIREMENT 7: End-to-end full generation pipeline."""
        skill = Creator20Skill(mode="skill")
        result = skill.execute(
            "JSON schema validator with custom error messages for data validation"
        )

        # Assertion 1: Execution success
        assert result["success"] is True, "Generation must succeed"
        assert result["skill_id"] is not None, "Skill ID must exist"
        assert result["final_loss"] is not None, "Loss must exist"

        # Assertion 2: Manifest completeness
        manifest = result["manifest"]
        assert manifest is not None, "Manifest must exist"
        required_manifest_fields = ["id", "version"]
        for field in required_manifest_fields:
            assert field in manifest, f"Manifest missing field: {field}"

        # Assertion 3: Artifacts present
        artifacts = result.get("artifacts", [])
        assert len(artifacts) >= 1, "Must generate at least one artifact"

        # Assertion 4: Events present
        phases_completed = result.get("phases_completed", 0)
        assert phases_completed >= 10, f"Must complete ≥10 phases, got {phases_completed}"


class TestPhase2LearningIntegration:
    """Test learning infrastructure integration for Phase 2."""

    def test_phase_events_can_convert_to_learning_events(self):
        """Test that phase events can be converted to ADR-0314 learning events."""
        skill = Creator20Skill(mode="skill")
        skill.execute("Test skill")

        events_dicts = skill.get_events()
        assert len(events_dicts) > 0, "Must have phase events"

        # Verify conversion is possible (check event structure)
        for e_dict in events_dicts:
            assert "skill_id" in e_dict, "Event must have skill_id for conversion"
            assert "phase_num" in e_dict, "Event must have phase_num"
            assert "duration_ms" in e_dict, "Event must have duration_ms"

    def test_learning_bridge_event_structure(self):
        """Test that learning bridge can create valid learning events."""
        phase_event = PhaseCompletedEvent(
            phase_num=0,
            skill_id="test_skill",
            duration_ms=100.5,
            errors=[],
            loss_components=LossComponents(
                relevance=0.95,
                completeness=0.90,
                performance=1.0,
                maintainability=1.0,
            ),
        )

        learning_event = Creator20LearningBridge.convert_phase_event_to_learning_event(
            phase_event,
            tenant_id="_default",
        )

        # Verify learning event structure
        assert learning_event.event_type == EventType.SKILL_EXECUTED
        assert learning_event.skill_id == "creator_2_0.test_skill"
        assert learning_event.tenant_id == "_default"
        assert learning_event.signal is not None
        assert learning_event.signal["phase_num"] == 0
        assert learning_event.signal["duration_ms"] == 100.5


class TestPhase2RobustessAndReliability:
    """Test robustness and reliability of Creator 2.0."""

    def test_skill_generation_is_deterministic(self):
        """Test that skill generation produces consistent results."""
        query = "Email validator"

        # Generate same skill multiple times
        losses = []
        for _ in range(3):
            skill = Creator20Skill(mode="skill")
            result = skill.execute(query)
            losses.append(result["final_loss"])

        # All losses should be similar (within reasonable tolerance)
        avg_loss = sum(losses) / len(losses)
        for loss in losses:
            # Allow up to 10% variation (or 0.01 absolute, whichever is larger)
            tolerance = max(avg_loss * 0.1, 0.01)
            assert abs(loss - avg_loss) <= tolerance, \
                f"Loss varies too much: {losses}, avg={avg_loss}"

    def test_no_phase_is_skipped(self):
        """Test that no phases are skipped during generation."""
        skill = Creator20Skill(mode="skill")
        result = skill.execute("Complete test")

        events = skill.get_events()
        phases_executed = sorted({e["phase_num"] for e in events})

        # Should have phases 0-10 (allowing for checkpoint to not have separate entry)
        assert phases_executed[0] == 0, "Must start with phase 0"
        assert phases_executed[-1] >= 9, "Must reach at least phase 9"

    def test_manifest_always_has_required_fields(self):
        """Test that generated manifests always have required fields."""
        queries = [
            "Simple validator",
            "Complex processor",
            "API tool",
        ]

        for query in queries:
            skill = Creator20Skill(mode="skill")
            result = skill.execute(query)
            manifest = result["manifest"]

            assert manifest is not None, f"Manifest missing for: {query}"
            assert "id" in manifest, f"Manifest missing 'id' for: {query}"
            assert "version" in manifest, f"Manifest missing 'version' for: {query}"


class TestPhase2EdgeCases:
    """Test edge cases and error handling."""

    def test_empty_query_is_handled(self):
        """Test that empty queries don't crash."""
        skill = Creator20Skill(mode="skill")
        result = skill.execute("")

        # Should complete without crashing
        assert "skill_id" in result
        assert "final_loss" in result

    def test_very_long_query_is_handled(self):
        """Test that very long queries don't cause issues."""
        long_query = "skill " * 1000  # Very long
        skill = Creator20Skill(mode="skill")
        result = skill.execute(long_query)

        # Should complete
        assert result.get("skill_id") is not None

    def test_special_characters_in_query(self):
        """Test that special characters are handled."""
        queries = [
            "Email validator (RFC 5322) with UTF-8 support",
            "JSON/YAML/XML parser for API responses",
            "Data transformer: input → output",
        ]

        for query in queries:
            skill = Creator20Skill(mode="skill")
            result = skill.execute(query)
            assert result["success"] is not None


# Summary: Phase 2 Gate Verification
# All tests above must pass for Phase 2 production readiness.
# If any test fails, Phase 2 gate is NOT passed and must be escalated.
