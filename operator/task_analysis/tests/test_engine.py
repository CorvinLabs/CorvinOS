"""Tests for TaskEngine orchestrator and phase contracts."""

import pytest
from ..engine import TaskEngine, EngineResult, EngineError, EnginePhase
from ..contracts import PhaseContracts, ContractViolation
from ..normalizer import TaskNormalizer, InsufficientTaskInfo
from ..validation import GraphValidator
from ..enrichment import TaskEnricher


@pytest.fixture
def engine():
    return TaskEngine()


@pytest.fixture
def normalizer():
    return TaskNormalizer()


@pytest.fixture
def validator():
    return GraphValidator()


@pytest.fixture
def enricher():
    return TaskEnricher()


@pytest.fixture
def router():
    from ..delegation import DelegationRouter
    return DelegationRouter()


class TestTaskEngine:
    """Test the complete TaskEngine pipeline."""

    def test_engine_routing_simple_bug_fix(self, engine):
        """Engine should route a simple bug-fix task."""
        result = engine.route_task("Fix crash in voice module for long audio files")

        assert isinstance(result, EngineResult)
        assert result.raw_task == "Fix crash in voice module for long audio files"
        assert result.decision_target.value in ["native", "acs", "tde"]
        assert 0.0 <= result.confidence <= 1.0
        assert 0.0 <= result.task_complexity <= 1.0
        assert result.model_recommendation in ["haiku", "opus"]

    def test_engine_routing_big_data_task(self, engine):
        """Engine should route big-data tasks to ACS."""
        result = engine.route_task(
            "Process big data from warehouse with millions of customer records"
        )

        assert result.decision_target.value == "acs"
        assert "big_data" in result.carve_out_reason

    def test_engine_routing_complex_refactor(self, engine):
        """Engine should route complex refactors to Opus (maybe TDE)."""
        result = engine.route_task(
            "Major system refactor: rewrite entire architecture and redesign all layers"
        )

        assert result.model_recommendation == "opus"
        assert result.task_complexity > 0.5

    def test_engine_routing_simple_doc_task(self, engine):
        """Engine should route simple docs to Haiku (native)."""
        result = engine.route_task("Fix typo in documentation file")

        assert result.model_recommendation == "haiku"
        assert result.decision_target.value == "native"

    def test_engine_insufficient_task_raises(self, engine):
        """Engine should raise InsufficientTaskInfo for vague tasks."""
        with pytest.raises(InsufficientTaskInfo):
            engine.route_task("fix")

    def test_engine_metadata_populated(self, engine):
        """Engine should populate enriched_metadata."""
        result = engine.route_task("Fix bug in voice module for testing")

        assert isinstance(result.enriched_metadata, dict)
        assert "normalized_type" in result.enriched_metadata
        assert "classified_confidence" in result.enriched_metadata
        assert "filtered_graphs" in result.enriched_metadata
        assert "validation_notes" in result.enriched_metadata

    def test_engine_deterministic(self, engine):
        """Same input should produce same output."""
        task = "Fix high severity crash in core voice rendering module"
        result1 = engine.route_task(task)
        result2 = engine.route_task(task)

        assert result1.decision_target == result2.decision_target
        assert result1.task_complexity == result2.task_complexity
        assert result1.model_recommendation == result2.model_recommendation


class TestPhaseContracts:
    """Test phase contract validation."""

    def test_phase0_contract_accepts_valid(self, normalizer):
        """Valid Phase 0 output should pass contract."""
        normalized = normalizer.normalize("test bug in voice")
        PhaseContracts.validate_phase0_output(normalized)  # Should not raise

    def test_phase0_contract_rejects_missing_field(self):
        """Phase 0 output missing required field should fail."""
        class FakeTask:
            summary = "test"
            # Missing 'type', 'severity', etc.

        with pytest.raises(ContractViolation, match="NormalizedTask"):
            PhaseContracts.validate_phase0_output(FakeTask())

    def test_phase1_contract_accepts_valid(self, normalizer):
        """Valid Phase 1 output should pass contract."""
        from ..classifier import ClassifiedTask

        normalized = normalizer.normalize("test bug in voice")
        classified = ClassifiedTask(
            normalized=normalized,
            classification={"call_graph": (0.8, {"files": ["test.py"]})},
            confidence=0.8,
            recommended_graphs=["call_graph"],
            skills_to_inject=[],
        )
        PhaseContracts.validate_phase1_output(classified)  # Should not raise

    def test_phase1_contract_rejects_bad_classification(self, normalizer):
        """Phase 1 with malformed classification should fail."""
        from ..classifier import ClassifiedTask

        normalized = normalizer.normalize("test bug in voice")
        classified = ClassifiedTask(
            normalized=normalized,
            classification={"call_graph": (0.8, "NOT_A_DICT")},  # Wrong type
            confidence=0.8,
            recommended_graphs=["call_graph"],
            skills_to_inject=[],
        )

        with pytest.raises(ContractViolation, match="metadata"):
            PhaseContracts.validate_phase1_output(classified)

    def test_phase1_contract_rejects_non_numeric_score(self, normalizer):
        """Phase 1 with non-numeric score should fail."""
        from ..classifier import ClassifiedTask

        normalized = normalizer.normalize("test bug in voice")
        classified = ClassifiedTask(
            normalized=normalized,
            classification={"call_graph": ("NOT_NUMERIC", {"files": []})},  # String instead of float
            confidence=0.8,
            recommended_graphs=["call_graph"],
            skills_to_inject=[],
        )

        with pytest.raises(ContractViolation, match="numeric"):
            PhaseContracts.validate_phase1_output(classified)

    def test_phase4_contract_rejects_invalid_model(self, normalizer, validator):
        """Phase 4 with invalid model recommendation should fail."""
        from ..enrichment import EnrichedTask
        from ..validation import ValidatedGraphs

        # Create minimal ValidatedGraphs
        normalized = normalizer.normalize("test bug in voice")
        from ..classifier import ClassifiedTask
        from ..filtering import FilteredGraphs

        classified = ClassifiedTask(
            normalized=normalized,
            classification={"call_graph": (0.8, {"files": ["test.py"]})},
            confidence=0.8,
            recommended_graphs=["call_graph"],
            skills_to_inject=[],
        )
        filtered = FilteredGraphs(
            normalized=normalized,
            classified=classified,
            filtered_graphs=["call_graph"],
            deduplicated_files={},
            ranked_graphs=[],
            redundancy_ratio=0.0,
        )
        validated = validator.validate(filtered)

        # Create invalid EnrichedTask
        enriched = EnrichedTask(
            validated=validated,
            task_complexity=0.5,
            model_recommendation="INVALID_MODEL",  # Should be 'haiku' or 'opus'
            estimated_tokens=2000,
            estimated_cost_usd=0.01,
        )

        with pytest.raises(ContractViolation, match="model_recommendation"):
            PhaseContracts.validate_phase4_output(enriched)

    def test_phase5_contract_accepts_valid_decision(self, normalizer, validator, enricher):
        """Phase 5 with valid decision should pass contract."""
        from ..delegation import DelegationDecision, DelegationTarget

        normalized = normalizer.normalize("test bug in voice")
        from ..classifier import ClassifiedTask
        from ..filtering import FilteredGraphs

        classified = ClassifiedTask(
            normalized=normalized,
            classification={"call_graph": (0.8, {"files": ["test.py"]})},
            confidence=0.8,
            recommended_graphs=["call_graph"],
            skills_to_inject=[],
        )
        filtered = FilteredGraphs(
            normalized=normalized,
            classified=classified,
            filtered_graphs=["call_graph"],
            deduplicated_files={},
            ranked_graphs=[],
            redundancy_ratio=0.0,
        )
        validated = validator.validate(filtered)
        enriched = enricher.enrich(validated)

        # Valid decision
        good_decision = DelegationDecision(
            enriched=enriched,
            should_delegate=False,
            delegation_target=DelegationTarget.NATIVE,
            carve_out_reason="none",
            confidence=0.95,
        )

        PhaseContracts.validate_phase5_output(good_decision)  # Should not raise
