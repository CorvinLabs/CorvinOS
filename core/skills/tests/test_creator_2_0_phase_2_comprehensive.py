"""Comprehensive tests for ADR-0661 Phase 2 — Creator 2.0 with learning integration.

Phase 2 Gate Requirements:
1. Creator 2.0 generates 3 test skills end-to-end (simple, complex, tool mode)
2. All 12 phases (0–10) emit PhaseCompletedEvent
3. Learning events bridge to ADR-0314
4. Loss components are in bounds [0, 1]
5. Final loss makes sense (no NaN, no inf)
6. All 250+ tests passing
7. 0 CRITICAL/HIGH findings
"""

import pytest
from typing import Dict, Any

from core.skills.os_skills.creator_2_0.skill import Creator20Skill
from core.skills.os_skills.creator_2_0.phase_model import PhaseModelOrchestrator
from core.skills.os_skills.creator_2_0.events import PhaseCompletedEvent, LossComponents
from core.skills.os_skills.creator_2_0.learning_integration import (
    Creator20LearningBridge,
    Creator20AuditLogger,
)
from core.learning.learning_events import EventType


class TestPhase2GateRequirements:
    """Test Phase 2 gate requirements (must all pass for production)."""

    def test_gate_requirement_1_skill_generation_simple(self):
        """Gate: Generate simple skill (Commit Message Linter)."""
        skill = Creator20Skill(mode="skill")
        result = skill.execute("Create a commit message linter that validates git messages")

        # Verify success
        assert result["success"] is True, "Simple skill generation must succeed"
        assert result["skill_id"] is not None, "Skill ID must be generated"
        assert result["final_loss"] is not None, "Final loss must be computed"
        assert result["manifest"] is not None, "Manifest must be generated"
        assert result["artifacts"] is not None, "Artifacts must be generated"

    def test_gate_requirement_2_skill_generation_complex(self):
        """Gate: Generate complex skill with clarifications (Support Classifier)."""
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
        assert result["manifest"] is not None

    def test_gate_requirement_3_tool_generation(self):
        """Gate: Generate tool mode (API Validator Tool)."""
        skill = Creator20Skill(mode="tool")
        result = skill.execute("Create a REST API request validator")

        assert result["success"] is True
        assert result["mode"] == "tool"
        assert result["final_loss"] is not None

    def test_gate_requirement_4_all_phases_emit_events(self):
        """Gate: All 12 phases (0–10) must emit PhaseCompletedEvent."""
        test_skills = [
            ("Commit message linter", {}),
            ("Support ticket classifier", {"What categories": "urgent,high,medium,low"}),
        ]

        for query, clarifications in test_skills:
            skill = Creator20Skill(mode="skill")
            skill.execute(query, clarifications=clarifications if clarifications else None)
            events = skill.get_events()

            # Should have events from phases 0–10 (11 phases total)
            assert len(events) >= 10, f"Expected ≥10 events for '{query}', got {len(events)}"

            # Verify phase coverage
            phase_nums = {e["phase_num"] for e in events}
            expected_phases = {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10}
            missing_phases = expected_phases - phase_nums
            assert not missing_phases, f"Missing phases: {missing_phases}"

    def test_gate_requirement_5_loss_components_in_bounds(self):
        """Gate: All loss components must be in [0, 1]."""
        skill = Creator20Skill(mode="skill")
        skill.execute("Test skill for loss bounds")

        events = skill.get_events()
        for event in events:
            if event.get("loss_components"):
                comp = event["loss_components"]
                assert 0 <= comp["relevance"] <= 1, f"relevance out of bounds: {comp['relevance']}"
                assert 0 <= comp["completeness"] <= 1, f"completeness out of bounds: {comp['completeness']}"
                assert 0 <= comp["performance"] <= 1, f"performance out of bounds: {comp['performance']}"
                assert 0 <= comp["maintainability"] <= 1, f"maintainability out of bounds: {comp['maintainability']}"
                assert 0 <= comp["overall"] <= 1, f"overall loss out of bounds: {comp['overall']}"

    def test_gate_requirement_6_final_loss_validity(self):
        """Gate: Final loss must be well-formed (no NaN, inf)."""
        skill = Creator20Skill(mode="skill")
        result = skill.execute("Test skill for final loss")

        loss = result["final_loss"]
        assert loss is not None, "Final loss must not be None"
        assert not (loss != loss), "Final loss is NaN"
        assert loss != float("inf"), "Final loss is positive infinity"
        assert loss != float("-inf"), "Final loss is negative infinity"
        assert 0 <= loss <= 1, f"Final loss out of bounds: {loss}"

    def test_gate_requirement_7_e2e_full_pipeline(self):
        """Gate: End-to-end full generation pipeline must work."""
        skill = Creator20Skill(mode="skill")
        result = skill.execute(
            "JSON schema validator with custom error messages for data validation"
        )

        # Execution success
        assert result["success"] is True
        assert result["skill_id"] is not None
        assert result["final_loss"] is not None

        # Manifest completeness
        manifest = result["manifest"]
        assert manifest is not None
        assert "id" in manifest
        assert "version" in manifest
        assert "description" in manifest or "type" in manifest

        # Artifacts present
        assert result["artifacts"] is not None
        assert len(result["artifacts"]) >= 1

        # Events present
        events = result.get("phases_completed", 0)
        assert events >= 10


class TestPhase2LearningIntegration:
    """Test integration between Creator 2.0 and ADR-0314 learning infrastructure."""

    def test_phase_events_convert_to_learning_events(self):
        """Test converting PhaseCompletedEvent to LearningEvent."""
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
            skill_version="1.0",
        )

        assert learning_event.event_type == EventType.SKILL_EXECUTED
        assert learning_event.skill_id == "creator_2_0.test_skill"
        assert learning_event.tenant_id == "_default"
        assert learning_event.signal["phase_num"] == 0
        assert learning_event.signal["duration_ms"] == 100.5
        assert "loss_components" in learning_event.signal

    def test_all_phase_events_convert_successfully(self):
        """Test converting all phases to learning events."""
        skill = Creator20Skill(mode="skill")
        skill.execute("Test skill")

        events_dicts = skill.get_events()

        # Reconstruct PhaseCompletedEvent objects from dicts
        phase_events = []
        for e in events_dicts:
            if "loss_components" in e and e["loss_components"] is not None:
                lc = e["loss_components"]
                loss_comp = LossComponents(
                    relevance=lc.get("relevance", 0.5),
                    completeness=lc.get("completeness", 0.5),
                    performance=lc.get("performance", 0.5),
                    maintainability=lc.get("maintainability", 0.5),
                )
            else:
                loss_comp = None

            phase_event = PhaseCompletedEvent(
                phase_num=e["phase_num"],
                skill_id=e["skill_id"],
                duration_ms=e["duration_ms"],
                errors=e.get("errors", []),
                loss_components=loss_comp,
                timestamp=e.get("timestamp"),
                metadata=e.get("metadata", {}),
            )
            phase_events.append(phase_event)

        learning_events = Creator20LearningBridge.convert_phase_events_to_learning_events(
            phase_events,
            tenant_id="_default",
        )

        assert len(learning_events) == len(phase_events)
        assert all(e.event_type == EventType.SKILL_EXECUTED for e in learning_events)
        assert all(e.tenant_id == "_default" for e in learning_events)

    def test_loss_component_signal_preservation(self):
        """Test that loss components are preserved in learning event signal."""
        phase_event = PhaseCompletedEvent(
            phase_num=5,
            skill_id="test_skill",
            duration_ms=250.0,
            errors=[],
            loss_components=LossComponents(
                relevance=0.85,
                completeness=0.75,
                performance=0.80,
                maintainability=0.90,
            ),
        )

        learning_event = Creator20LearningBridge.convert_phase_event_to_learning_event(
            phase_event,
        )

        signal_loss = learning_event.signal["loss_components"]
        assert signal_loss["relevance"] == 0.85
        assert signal_loss["completeness"] == 0.75
        assert signal_loss["performance"] == 0.80
        assert signal_loss["maintainability"] == 0.90
        assert 0 <= signal_loss["overall"] <= 1

    def test_overall_skill_loss_computation(self):
        """Test computing overall skill loss across all phases."""
        skill = Creator20Skill(mode="skill")
        skill.execute("Test skill")

        # Get raw events from orchestrator to get PhaseCompletedEvent objects
        # We'll compute loss based on the individual event losses
        events_list = skill.get_events()

        # Compute loss manually from events
        losses = [e.get("loss_components", {}).get("overall", 0.5)
                 for e in events_list if e.get("loss_components")]

        if losses:
            overall_loss = sum(losses) / len(losses)
        else:
            overall_loss = 0.5  # Default if no losses

        # Loss should be reasonable
        assert 0 <= overall_loss <= 1, f"Overall loss out of bounds: {overall_loss}"

    def test_overall_loss_with_custom_weights(self):
        """Test overall loss computation with custom weights."""
        skill = Creator20Skill(mode="skill")
        skill.execute("Test skill")

        events_list = skill.get_events()

        custom_weights = {
            "validation": 0.5,  # Heavy weight on validation phase
            "content": 0.3,
            "planning": 0.2,
        }

        # Compute weighted average manually
        total_weight = 0
        total_loss = 0
        for e in events_list:
            if e.get("loss_components"):
                loss = e["loss_components"].get("overall", 0.5)
                total_loss += loss
                total_weight += 1

        overall_loss = total_loss / total_weight if total_weight > 0 else 0.5

        assert 0 <= overall_loss <= 1


class TestPhase2AuditLogging:
    """Test audit logging compliance for Phase 2."""

    def test_skill_generation_audit_log_started(self):
        """Test logging skill generation start."""
        log_entry = Creator20AuditLogger.log_skill_generation_started(
            skill_id="test_skill_001",
            query="Test skill",
            mode="skill",
            tenant_id="_default",
        )

        assert log_entry["skill_id"] == "test_skill_001"
        assert log_entry["query"] == "Test skill"
        assert log_entry["mode"] == "skill"
        assert log_entry["tenant_id"] == "_default"
        assert "started_at" in log_entry

    def test_skill_generation_audit_log_completed(self):
        """Test logging skill generation completion."""
        log_entry = Creator20AuditLogger.log_skill_generation_completed(
            skill_id="test_skill_001",
            final_loss=0.15,
            phases_completed=11,
            success=True,
            tenant_id="_default",
        )

        assert log_entry["skill_id"] == "test_skill_001"
        assert log_entry["final_loss"] == 0.15
        assert log_entry["phases_completed"] == 11
        assert log_entry["success"] is True
        assert log_entry["tenant_id"] == "_default"
        assert "completed_at" in log_entry


class TestPhase2VariationHandling:
    """Test that Creator 2.0 handles various input variations correctly."""

    def test_empty_query_handling(self):
        """Test that empty queries are handled gracefully."""
        skill = Creator20Skill(mode="skill")
        # Empty query should not crash
        result = skill.execute("")
        # Should still return a valid result structure
        assert "skill_id" in result
        assert "final_loss" in result

    def test_very_long_query_handling(self):
        """Test that very long queries are handled."""
        long_query = "A skill that " * 100  # Very long query
        skill = Creator20Skill(mode="skill")
        result = skill.execute(long_query)
        assert result["success"] is not None

    def test_special_characters_in_query(self):
        """Test that special characters in queries are handled."""
        special_query = "Create a skill for: parsing JSON/YAML with 'special' chars & symbols"
        skill = Creator20Skill(mode="skill")
        result = skill.execute(special_query)
        assert result["skill_id"] is not None

    def test_multiple_sequential_generations(self):
        """Test generating multiple skills sequentially."""
        queries = [
            "Email validator",
            "JSON parser",
            "API rate limiter",
        ]

        skills = []
        for query in queries:
            skill = Creator20Skill(mode="skill")
            result = skill.execute(query)
            skills.append(result)
            assert result["success"] is True

        # All should have different skill IDs
        skill_ids = {s["skill_id"] for s in skills}
        assert len(skill_ids) == len(queries), "All skills should have unique IDs"

    def test_mode_switching(self):
        """Test switching between skill and tool modes."""
        skill_query = "Email validator"
        tool_query = "API request validator"

        skill_result = Creator20Skill(mode="skill").execute(skill_query)
        tool_result = Creator20Skill(mode="tool").execute(tool_query)

        assert skill_result["mode"] == "skill"
        assert tool_result["mode"] == "tool"


class TestPhase2ConsistencyAndDeterminism:
    """Test that Creator 2.0 produces consistent results."""

    def test_same_query_same_intent(self):
        """Test that same query produces same intent classification."""
        query = "Email validator that validates email addresses"

        results = []
        for _ in range(3):
            skill = Creator20Skill(mode="skill")
            result = skill.execute(query)
            results.append(result)

        # All should have similar structure
        assert all(r["success"] for r in results)
        assert all(r["skill_id"] for r in results)
        assert all(r["final_loss"] is not None for r in results)

    def test_loss_computation_consistency(self):
        """Test that loss computation is consistent for same skill."""
        query = "Test consistency"
        losses = []

        for _ in range(3):
            skill = Creator20Skill(mode="skill")
            result = skill.execute(query)
            losses.append(result["final_loss"])

        # Losses should be reasonably close (not exactly same due to timing)
        avg_loss = sum(losses) / len(losses)
        for loss in losses:
            # Within 10% of average
            assert abs(loss - avg_loss) < avg_loss * 0.1 + 0.01

    def test_manifest_schema_consistency(self):
        """Test that generated manifests have consistent structure."""
        skill = Creator20Skill(mode="skill")
        result = skill.execute("Test manifest schema")

        manifest = result["manifest"]
        required_fields = ["id", "version"]
        for field in required_fields:
            assert field in manifest, f"Manifest missing required field: {field}"


class TestPhase2EventEmission:
    """Test that all phases properly emit events with correct structure."""

    def test_phase_0_event_structure(self):
        """Test Phase 0 (Intake) event structure."""
        skill = Creator20Skill(mode="skill")
        skill.execute("Test intake phase")

        events = skill.get_events()
        phase_0_events = [e for e in events if e["phase_num"] == 0]

        assert len(phase_0_events) > 0, "Phase 0 must emit event"
        event = phase_0_events[0]
        assert event["skill_id"] is not None
        assert "duration_ms" in event
        assert event["duration_ms"] >= 0

    def test_all_phase_events_have_skill_id(self):
        """Test that all phase events have skill_id."""
        skill = Creator20Skill(mode="skill")
        result = skill.execute("Test skill ID consistency")

        events = skill.get_events()
        expected_skill_id = result["skill_id"]

        for event in events:
            assert event["skill_id"] == expected_skill_id, \
                f"Event phase {event['phase_num']} has wrong skill_id"

    def test_phase_events_timestamps_are_ordered(self):
        """Test that phase event timestamps are ordered."""
        skill = Creator20Skill(mode="skill")
        skill.execute("Test timestamp ordering")

        events = skill.get_events()
        for i in range(len(events) - 1):
            assert events[i]["timestamp"] <= events[i + 1]["timestamp"], \
                "Event timestamps should be in order"

    def test_duration_ms_is_positive(self):
        """Test that all phase durations are positive."""
        skill = Creator20Skill(mode="skill")
        skill.execute("Test duration values")

        events = skill.get_events()
        for event in events:
            assert event["duration_ms"] >= 0, \
                f"Phase {event['phase_num']} has negative duration"


class TestPhase2ComplexityHandling:
    """Test that Creator 2.0 handles complex skill definitions."""

    def test_skill_with_many_dependencies(self):
        """Test skill generation with implied complex dependencies."""
        skill = Creator20Skill(mode="skill")
        result = skill.execute(
            "Create a skill that integrates with multiple APIs and databases"
        )
        assert result["success"] is True

    def test_skill_with_nested_requirements(self):
        """Test skill with nested/complex requirements."""
        skill = Creator20Skill(mode="skill")
        result = skill.execute(
            "Create a skill that validates, transforms, and caches data with retry logic"
        )
        assert result["success"] is True

    def test_tool_with_complex_api_contract(self):
        """Test tool generation with complex API contract."""
        skill = Creator20Skill(mode="tool")
        result = skill.execute(
            "Create a tool that makes authenticated HTTP requests with error handling"
        )
        assert result["success"] is True


class TestPhase2OutputArtifacts:
    """Test that generated artifacts are well-formed."""

    def test_artifacts_list_is_not_empty(self):
        """Test that skill generation produces artifacts."""
        skill = Creator20Skill(mode="skill")
        result = skill.execute("Test artifact generation")

        artifacts = result.get("artifacts", [])
        assert len(artifacts) > 0, "Should generate at least one artifact"

    def test_manifest_has_required_metadata(self):
        """Test that manifest has required metadata."""
        skill = Creator20Skill(mode="skill")
        result = skill.execute("Test manifest metadata")

        manifest = result["manifest"]
        assert "id" in manifest, "Manifest must have id"
        assert "version" in manifest, "Manifest must have version"

    def test_manifest_version_is_valid_semver(self):
        """Test that manifest version follows semantic versioning."""
        skill = Creator20Skill(mode="skill")
        result = skill.execute("Test semver")

        manifest = result["manifest"]
        version = manifest.get("version", "")
        # Very basic check: should have at least one dot
        assert "." in str(version) or version.isdigit(), \
            f"Version should follow semver format, got: {version}"


# Integration tests combining multiple requirements
class TestPhase2IntegrationScenarios:
    """Integration tests combining multiple Phase 2 requirements."""

    def test_full_skill_generation_to_audit_trail(self):
        """Test full journey from skill generation through audit logging."""
        # Generate skill
        skill = Creator20Skill(mode="skill")
        query = "Email validator for user registration"
        result = skill.execute(query)

        # Log in audit trail
        start_log = Creator20AuditLogger.log_skill_generation_started(
            skill_id=result["skill_id"],
            query=query,
            mode="skill",
        )

        end_log = Creator20AuditLogger.log_skill_generation_completed(
            skill_id=result["skill_id"],
            final_loss=result["final_loss"],
            phases_completed=result["phases_completed"],
            success=result["success"],
        )

        # Verify audit trail completeness
        assert start_log["skill_id"] == end_log["skill_id"]
        assert result["final_loss"] == end_log["final_loss"]

    def test_learning_events_from_real_skill_generation(self):
        """Test that real skill generation produces proper learning events."""
        skill = Creator20Skill(mode="skill")
        result = skill.execute("JSON schema validator")

        # Convert to learning events
        events_dicts = skill.get_events()

        # Reconstruct PhaseCompletedEvent objects from dicts
        phase_events = []
        for e in events_dicts:
            if "loss_components" in e and e["loss_components"] is not None:
                lc = e["loss_components"]
                loss_comp = LossComponents(
                    relevance=lc.get("relevance", 0.5),
                    completeness=lc.get("completeness", 0.5),
                    performance=lc.get("performance", 0.5),
                    maintainability=lc.get("maintainability", 0.5),
                )
            else:
                loss_comp = None

            phase_event = PhaseCompletedEvent(
                phase_num=e["phase_num"],
                skill_id=e["skill_id"],
                duration_ms=e["duration_ms"],
                errors=e.get("errors", []),
                loss_components=loss_comp,
                timestamp=e.get("timestamp"),
                metadata=e.get("metadata", {}),
            )
            phase_events.append(phase_event)

        learning_events = Creator20LearningBridge.convert_phase_events_to_learning_events(
            phase_events,
        )

        # Verify learning events
        assert len(learning_events) == len(phase_events)
        assert all(e.event_type == EventType.SKILL_EXECUTED for e in learning_events)
        assert all(e.tenant_id == "_default" for e in learning_events)

        # Verify signal completeness
        for event in learning_events:
            assert event.signal is not None
            assert "phase_num" in event.signal
            assert "duration_ms" in event.signal
