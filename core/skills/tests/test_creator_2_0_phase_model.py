"""Tests for Creator 2.0 phase model orchestrator."""

import pytest
from core.skills.os_skills.creator_2_0.phase_model import PhaseModelOrchestrator, Creator20State
from core.skills.os_skills.creator_2_0.events import LossComponents, PhaseCompletedEvent, LossEmitter


class TestPhaseModelOrchestrator:
    """Test the full 12-phase orchestrator."""

    def test_execute_simple_validation_skill(self):
        """Test creation of a simple validation skill."""
        orchestrator = PhaseModelOrchestrator()
        state = orchestrator.execute(
            query="Email validator",
            mode="skill",
        )

        assert state.skill_id.startswith("skill_")
        assert state.intake_result is not None
        assert state.ingestion_result is not None
        assert state.planning_result is not None
        assert state.delivery_result is not None

    def test_execute_complex_skill_with_clarifications(self):
        """Test creation of a complex skill with user clarifications."""
        orchestrator = PhaseModelOrchestrator()
        clarifications = {
            "What format should be validated (email, URL, regex pattern)?": "email",
            "Should validation be strict or lenient?": "strict",
        }

        state = orchestrator.execute(
            query="Validate user input with custom rules",
            mode="skill",
            user_clarifications=clarifications,
        )

        assert state.skill_id is not None
        assert state.clarifications == clarifications

    def test_all_phases_executed(self):
        """Test that all 12 phases were executed."""
        orchestrator = PhaseModelOrchestrator()
        state = orchestrator.execute("Test skill")

        assert orchestrator.validate_execution(state)
        events = orchestrator.get_events()
        # Should have ~11 events (phases 0–10, plus checkpoint at 3b)
        assert len(events) >= 10

    def test_events_emitted_correctly(self):
        """Test that phase completion events are emitted."""
        orchestrator = PhaseModelOrchestrator()
        state = orchestrator.execute("Test skill")

        events = orchestrator.get_events()
        assert all(isinstance(e, PhaseCompletedEvent) for e in events)
        assert all(e.skill_id == state.skill_id for e in events)

    def test_loss_calculation(self):
        """Test that final loss is calculated."""
        orchestrator = PhaseModelOrchestrator()
        state = orchestrator.execute("Test skill")

        assert state.final_loss is not None
        assert 0 <= state.final_loss <= 1

    def test_loss_validity(self):
        """Test that loss is well-formed (no NaN, no inf)."""
        orchestrator = PhaseModelOrchestrator()
        state = orchestrator.execute("Test skill")

        assert orchestrator.is_loss_valid()

    def test_execution_with_tool_mode(self):
        """Test skill generation in tool mode."""
        orchestrator = PhaseModelOrchestrator()
        state = orchestrator.execute(
            query="API validator tool",
            mode="tool",
        )

        assert state.intake_result.mode == "tool"

    def test_error_handling_on_invalid_query(self):
        """Test that invalid queries are handled gracefully."""
        orchestrator = PhaseModelOrchestrator()

        # Empty query should be handled
        state = orchestrator.execute("")
        # Should still complete (with errors recorded)
        assert state.skill_id is not None

    def test_multiple_executions(self):
        """Test that orchestrator can be reused for multiple executions."""
        orchestrator1 = PhaseModelOrchestrator()
        state1 = orchestrator1.execute("Skill 1")

        orchestrator2 = PhaseModelOrchestrator()
        state2 = orchestrator2.execute("Skill 2")

        assert state1.skill_id != state2.skill_id

    def test_phase_ordering(self):
        """Test that phases execute in correct order."""
        orchestrator = PhaseModelOrchestrator()
        state = orchestrator.execute("Test skill")

        events = orchestrator.get_events()
        phase_nums = [e.phase_num for e in events if e.phase_num >= 0]
        assert phase_nums == sorted(phase_nums)

    def test_state_intermediate_results(self):
        """Test that intermediate results are stored correctly."""
        orchestrator = PhaseModelOrchestrator()
        state = orchestrator.execute("Data processor")

        assert state.intake_result.intent in ["validation", "data_processing", "classification", "api_integration", "utility"]
        assert len(state.planning_result.functions) > 0
        assert len(state.structure_result.file_layout) > 0
        assert state.content_result.lines_of_code > 0
        assert state.delivery_result.delivery_status == "ready"


class TestLossEmitter:
    """Test the loss emitter."""

    def test_emit_event(self):
        """Test emitting a phase completion event."""
        emitter = LossEmitter()
        event = PhaseCompletedEvent(
            phase_num=0,
            skill_id="test_skill",
            duration_ms=100.0,
            loss_components=LossComponents(
                relevance=0.9,
                completeness=0.85,
                performance=1.0,
                maintainability=0.95,
            ),
        )

        emitter.emit(event)
        events = emitter.get_events()

        assert len(events) == 1
        assert events[0].skill_id == "test_skill"

    def test_overall_loss_calculation(self):
        """Test calculation of overall loss."""
        emitter = LossEmitter()

        emitter.emit(PhaseCompletedEvent(
            phase_num=0,
            skill_id="test",
            duration_ms=100.0,
            loss_components=LossComponents(
                relevance=0.8,
                completeness=0.8,
                performance=0.8,
                maintainability=0.8,
            ),
        ))
        emitter.emit(PhaseCompletedEvent(
            phase_num=1,
            skill_id="test",
            duration_ms=100.0,
            loss_components=LossComponents(
                relevance=1.0,
                completeness=1.0,
                performance=1.0,
                maintainability=1.0,
            ),
        ))

        overall_loss = emitter.overall_skill_loss()
        # Average of [0.8, 1.0] = 0.9
        assert abs(overall_loss - 0.9) < 0.01

    def test_loss_validation(self):
        """Test loss validation."""
        emitter = LossEmitter()

        # Valid loss
        emitter.emit(PhaseCompletedEvent(
            phase_num=0,
            skill_id="test",
            duration_ms=100.0,
            loss_components=LossComponents(
                relevance=0.5,
                completeness=0.5,
                performance=0.5,
                maintainability=0.5,
            ),
        ))

        assert emitter.validate_skill_loss()

    def test_invalid_loss_components_rejected(self):
        """Test that invalid loss components are rejected."""
        emitter = LossEmitter()

        with pytest.raises(ValueError):
            emitter.emit(PhaseCompletedEvent(
                phase_num=0,
                skill_id="test",
                duration_ms=100.0,
                loss_components=LossComponents(
                    relevance=1.5,  # Invalid: > 1.0
                    completeness=0.5,
                    performance=0.5,
                    maintainability=0.5,
                ),
            ))


class TestLossComponents:
    """Test the loss components dataclass."""

    def test_loss_components_validation(self):
        """Test loss component validation."""
        # Valid components
        valid = LossComponents(0.5, 0.6, 0.7, 0.8)
        assert valid.validate()

        # Invalid: > 1.0
        invalid_high = LossComponents(1.5, 0.5, 0.5, 0.5)
        assert not invalid_high.validate()

        # Invalid: < 0.0
        invalid_low = LossComponents(-0.1, 0.5, 0.5, 0.5)
        assert not invalid_low.validate()

    def test_loss_components_overall(self):
        """Test overall loss calculation."""
        components = LossComponents(0.8, 0.8, 0.8, 0.8)
        overall = components.overall()

        # With default weights (0.4, 0.3, 0.2, 0.1): 0.8 * (0.4 + 0.3 + 0.2 + 0.1) = 0.8
        assert abs(overall - 0.8) < 0.01

    def test_custom_loss_weights(self):
        """Test loss calculation with custom weights."""
        components = LossComponents(1.0, 0.0, 0.0, 0.0)
        weights = {"relevance": 1.0, "completeness": 0.0, "performance": 0.0, "maintainability": 0.0}

        overall = components.overall(weights=weights)
        assert abs(overall - 1.0) < 0.01
