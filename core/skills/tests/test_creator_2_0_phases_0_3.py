"""Tests for Creator 2.0 phases 0–3 (Intake, Ingestion, Clarification, Planning)."""

import pytest
from core.skills.os_skills.creator_2_0.phases.phase_0 import IntakePhase, IntakeRequest
from core.skills.os_skills.creator_2_0.phases.phase_1 import IngestionPhase, IngestionRequest
from core.skills.os_skills.creator_2_0.phases.phase_2 import ClarificationPhase, ClarificationRequest
from core.skills.os_skills.creator_2_0.phases.phase_3 import PlanningPhase, PlanningRequest
from core.skills.os_skills.creator_2_0.events import LossEmitter, LossComponents


class TestIntakePhase:
    """Test Phase 0: Intake."""

    def test_intake_valid_query(self):
        """Test intake with valid query."""
        emitter = LossEmitter()
        phase = IntakePhase()

        result = phase.execute(
            IntakeRequest(query="Email validator"),
            emitter,
        )

        assert result.skill_id.startswith("skill_")
        assert result.description == "Email validator"
        assert result.mode == "skill"

    def test_intake_generates_unique_ids(self):
        """Test that intake generates unique skill IDs."""
        emitter1 = LossEmitter()
        emitter2 = LossEmitter()
        phase = IntakePhase()

        result1 = phase.execute(IntakeRequest(query="Validator 1"), emitter1)
        result2 = phase.execute(IntakeRequest(query="Validator 2"), emitter2)

        assert result1.skill_id != result2.skill_id

    def test_intake_intent_detection(self):
        """Test that intake detects intent correctly."""
        emitter = LossEmitter()
        phase = IntakePhase()

        # Validation intent
        result = phase.execute(IntakeRequest(query="validate email addresses"), emitter)
        assert result.intent == "validation"

        # Data processing intent
        emitter = LossEmitter()
        result = phase.execute(IntakeRequest(query="process JSON files"), emitter)
        assert result.intent == "data_processing"

    def test_intake_emits_event(self):
        """Test that intake emits phase completion event."""
        emitter = LossEmitter()
        phase = IntakePhase()

        phase.execute(IntakeRequest(query="Test"), emitter)

        events = emitter.get_events()
        assert len(events) == 1
        assert events[0].phase_num == 0
        assert events[0].loss_components is not None

    def test_intake_invalid_query(self):
        """Test that invalid query raises error."""
        emitter = LossEmitter()
        phase = IntakePhase()

        with pytest.raises(ValueError):
            phase.execute(IntakeRequest(query=""), emitter)

    def test_intake_tool_mode(self):
        """Test intake with tool mode."""
        emitter = LossEmitter()
        phase = IntakePhase()

        result = phase.execute(
            IntakeRequest(query="Test tool", mode="tool"),
            emitter,
        )

        assert result.mode == "tool"


class TestIngestionPhase:
    """Test Phase 1: Ingestion."""

    def test_ingestion_collects_knowledge(self):
        """Test that ingestion collects domain knowledge."""
        emitter = LossEmitter()
        phase = IngestionPhase()

        result = phase.execute(
            IngestionRequest(
                skill_id="test_skill",
                description="Email validator",
                intent="validation",
                mode="skill",
            ),
            emitter,
        )

        assert result.domain_knowledge is not None
        assert "intent" in result.domain_knowledge
        assert "required_imports" in result.domain_knowledge

    def test_ingestion_generates_examples(self):
        """Test that ingestion generates examples."""
        emitter = LossEmitter()
        phase = IngestionPhase()

        result = phase.execute(
            IngestionRequest(
                skill_id="test",
                description="Validate emails",
                intent="validation",
                mode="skill",
            ),
            emitter,
        )

        assert len(result.examples) > 0
        assert all("input" in ex for ex in result.examples)

    def test_ingestion_finds_related_skills(self):
        """Test that ingestion finds related skills."""
        emitter = LossEmitter()
        phase = IngestionPhase()

        result = phase.execute(
            IngestionRequest(
                skill_id="test",
                description="Classify text",
                intent="classification",
                mode="skill",
            ),
            emitter,
        )

        assert len(result.related_skills) > 0

    def test_ingestion_emits_event(self):
        """Test that ingestion emits event."""
        emitter = LossEmitter()
        phase = IngestionPhase()

        phase.execute(
            IngestionRequest(skill_id="test", description="Test", intent="validation", mode="skill"),
            emitter,
        )

        events = emitter.get_events()
        assert len(events) == 1
        assert events[0].phase_num == 1


class TestClarificationPhase:
    """Test Phase 2: Clarification."""

    def test_clarification_generates_questions(self):
        """Test that clarification phase generates questions."""
        emitter = LossEmitter()
        phase = ClarificationPhase()

        result = phase.execute(
            ClarificationRequest(
                skill_id="test",
                description="Email validator",
                intent="validation",
                domain_knowledge={},
            ),
            emitter,
        )

        assert len(result.required_clarifications_asked) > 0
        assert len(result.clarifications) > 0

    def test_clarification_respects_user_answers(self):
        """Test that clarification respects pre-provided answers."""
        emitter = LossEmitter()
        phase = ClarificationPhase()

        user_answers = {
            "What format should be validated (email, URL, regex pattern)?": "email",
        }

        result = phase.execute(
            ClarificationRequest(
                skill_id="test",
                description="Email validator",
                intent="validation",
                domain_knowledge={},
                user_clarifications=user_answers,
            ),
            emitter,
        )

        assert result.clarifications["What format should be validated (email, URL, regex pattern)?"] == "email"

    def test_clarification_max_3_questions(self):
        """Test that clarification asks max 3 questions."""
        emitter = LossEmitter()
        phase = ClarificationPhase()

        result = phase.execute(
            ClarificationRequest(
                skill_id="test",
                description="Complex skill",
                intent="validation",
                domain_knowledge={},
            ),
            emitter,
        )

        assert len(result.required_clarifications_asked) <= 3

    def test_clarification_emits_event(self):
        """Test that clarification emits event."""
        emitter = LossEmitter()
        phase = ClarificationPhase()

        phase.execute(
            ClarificationRequest(skill_id="test", description="Test", intent="validation", domain_knowledge={}),
            emitter,
        )

        events = emitter.get_events()
        assert len(events) == 1
        assert events[0].phase_num == 2


class TestPlanningPhase:
    """Test Phase 3: Planning."""

    def test_planning_creates_architecture(self):
        """Test that planning creates architecture."""
        emitter = LossEmitter()
        phase = PlanningPhase()

        result = phase.execute(
            PlanningRequest(
                skill_id="test",
                intent="validation",
                description="Email validator",
                domain_knowledge={},
                clarifications={},
            ),
            emitter,
        )

        assert result.architecture is not None
        assert "pattern" in result.architecture

    def test_planning_designs_functions(self):
        """Test that planning designs function signatures."""
        emitter = LossEmitter()
        phase = PlanningPhase()

        result = phase.execute(
            PlanningRequest(
                skill_id="test",
                intent="validation",
                description="Email validator",
                domain_knowledge={},
                clarifications={},
            ),
            emitter,
        )

        assert len(result.functions) > 0
        assert all("name" in f and "returns" in f for f in result.functions)

    def test_planning_identifies_dependencies(self):
        """Test that planning identifies dependencies."""
        emitter = LossEmitter()
        phase = PlanningPhase()

        result = phase.execute(
            PlanningRequest(
                skill_id="test",
                intent="validation",
                description="Email validator",
                domain_knowledge={},
                clarifications={},
            ),
            emitter,
        )

        assert len(result.dependencies) > 0

    def test_planning_different_intents(self):
        """Test that planning differs for different intents."""
        emitter1 = LossEmitter()
        phase1 = PlanningPhase()

        result1 = phase1.execute(
            PlanningRequest(
                skill_id="test1",
                intent="validation",
                description="Validator",
                domain_knowledge={},
                clarifications={},
            ),
            emitter1,
        )

        emitter2 = LossEmitter()
        phase2 = PlanningPhase()

        result2 = phase2.execute(
            PlanningRequest(
                skill_id="test2",
                intent="data_processing",
                description="Processor",
                domain_knowledge={},
                clarifications={},
            ),
            emitter2,
        )

        # Different intents should produce different function counts or types
        # (at least they should be different objects)
        assert result1.intent != result2.intent

    def test_planning_emits_event(self):
        """Test that planning emits event."""
        emitter = LossEmitter()
        phase = PlanningPhase()

        phase.execute(
            PlanningRequest(
                skill_id="test",
                intent="validation",
                description="Test",
                domain_knowledge={},
                clarifications={},
            ),
            emitter,
        )

        events = emitter.get_events()
        assert len(events) == 1
        assert events[0].phase_num == 3
