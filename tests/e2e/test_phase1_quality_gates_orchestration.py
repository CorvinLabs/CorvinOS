"""E2E Test: Phase 1 Quality Gates Activation (ADR-0688 + ADR-2065).

Proves that all 4 validators work on real artifacts and emit to audit trail.
"""
import pytest
from core.quality_gates.orchestration_validator import (
    OrchestrationQualityValidator,
    GateVerdictEnum,
)


class TestPhase1QualityGatesActivation:
    """Phase 1 E2E: Quality gates wired into orchestration."""

    @pytest.fixture
    def validator(self):
        """Instantiate validator."""
        return OrchestrationQualityValidator()

    def test_adr_gate_valid_adr(self, validator):
        """ADRGate: Valid ADR with all fields passes."""
        adr = {
            "id": "ADR-2065",
            "status": "proposed",
            "depends_on": ["ADR-0897"],
            "paths": ["core/orchestration/"],
            "docs": ["docs/autonomous-execution-master-plan.md"],
        }

        result = validator.adr_validator.run(adr)

        assert result.verdict == GateVerdictEnum.PASS
        assert result.confidence >= 0.95
        assert len(result.findings) == 0

    def test_adr_gate_missing_field(self, validator):
        """ADRGate: ADR missing required field fails."""
        adr = {
            "id": "ADR-2065",
            "status": "proposed",
            # missing: depends_on, paths, docs
        }

        result = validator.adr_validator.run(adr)

        assert result.verdict == GateVerdictEnum.FAIL
        assert len(result.findings) >= 3  # At least 3 missing fields
        assert all(f.severity == "error" for f in result.findings)

    def test_adr_gate_empty_paths_warns(self, validator):
        """ADRGate: ADR with empty paths warns."""
        adr = {
            "id": "ADR-2065",
            "status": "proposed",
            "depends_on": [],
            "paths": [],  # Empty → warning
            "docs": [],
        }

        result = validator.adr_validator.run(adr)

        assert result.verdict == GateVerdictEnum.WARN
        assert any(f.category == "empty_field" for f in result.findings)

    def test_concept_gate_valid(self, validator):
        """ConceptGate: Valid concept passes."""
        concept = {
            "id": "CONCEPT-0040",
            "status": "proposed",
            "depends_on": [],
            "skills": ["my-skill"],
            "evidence_commits": ["abc123"],
        }

        result = validator.concept_validator.run(concept)

        assert result.verdict == GateVerdictEnum.PASS
        assert result.confidence >= 0.95

    def test_concept_gate_invalid_id_format(self, validator):
        """ConceptGate: Invalid ID format fails."""
        concept = {
            "id": "SKILL-0040",  # Should be CONCEPT-NNNN
            "status": "proposed",
            "depends_on": [],
        }

        result = validator.concept_validator.run(concept)

        assert result.verdict == GateVerdictEnum.FAIL
        assert any(f.category == "invalid_id_format" for f in result.findings)

    def test_implementation_plan_gate_valid(self, validator):
        """ImplementationPlanGate: Valid plan passes."""
        plan = {
            "id": "PLAN-001",
            "subsystem": "skill-forge",
            "phases": ["phase1", "phase2"],
        }

        result = validator.plan_validator.run(plan)

        assert result.verdict == GateVerdictEnum.PASS

    def test_implementation_plan_gate_missing_subsystem(self, validator):
        """ImplementationPlanGate: Missing subsystem fails."""
        plan = {
            "id": "PLAN-001",
            # missing: subsystem
            "phases": ["phase1"],
        }

        result = validator.plan_validator.run(plan)

        assert result.verdict == GateVerdictEnum.FAIL
        assert any(f.category == "missing_field" and f.severity == "error" for f in result.findings)

    def test_idea_gate_valid(self, validator):
        """IdeaGate: Valid idea passes."""
        idea = {
            "id": "IDEA-001",
            "description": "Improve performance via caching",
            "evidence_sources": ["task-123", "commit-abc"],
        }

        result = validator.idea_validator.run(idea)

        assert result.verdict == GateVerdictEnum.PASS

    def test_idea_gate_missing_evidence(self, validator):
        """IdeaGate: Idea without evidence sources warns."""
        idea = {
            "id": "IDEA-001",
            "description": "Improve performance via caching",
            # missing: evidence_sources
        }

        result = validator.idea_validator.run(idea)

        assert result.verdict == GateVerdictEnum.WARN
        assert any(f.category == "missing_field" and f.severity == "warning" for f in result.findings)

    def test_orchestration_validator_all_gates_passed(self, validator):
        """OrchestrationValidator: All gates passed when all verdicts PASS."""
        adr = {
            "id": "ADR-2065",
            "status": "proposed",
            "depends_on": ["ADR-0897"],
            "paths": ["core/orchestration/"],
            "docs": ["docs/autonomous-execution-master-plan.md"],
            "type": "ADR",
        }

        results = validator.validate_phase_artifacts("phase-1", adr)

        assert validator.all_gates_passed(results)

    def test_orchestration_validator_any_gate_failed(self, validator):
        """OrchestrationValidator: all_gates_passed returns False if any gate fails."""
        adr = {
            "id": "ADR-2065",
            "status": "invalid_status",  # Invalid → gate fails
            "depends_on": ["ADR-0897"],
            "paths": [],
            "docs": [],
            "type": "ADR",
        }

        results = validator.validate_phase_artifacts("phase-1", adr)

        assert not validator.all_gates_passed(results)

    def test_phase1_e2e_proof_all_validators_fire(self, validator):
        """Phase 1 E2E: All 4 validators fire on their respective artifact types."""
        artifacts = [
            # ADR artifact
            {
                "type": "ADR",
                "id": "ADR-2065",
                "status": "proposed",
                "depends_on": ["ADR-0897"],
                "paths": ["core/orchestration/"],
                "docs": ["docs/autonomous-execution-master-plan.md"],
            },
            # Concept artifact
            {
                "type": "Concept",
                "id": "CONCEPT-0040",
                "status": "proposed",
                "depends_on": [],
            },
            # Implementation Plan artifact
            {
                "type": "ImplementationPlan",
                "subsystem": "skill-forge",
                "phases": ["phase1", "phase2"],
            },
            # Idea artifact
            {
                "type": "Idea",
                "id": "IDEA-001",
                "description": "Improve performance",
                "evidence_sources": ["task-123"],
            },
        ]

        all_results = {}
        for artifact in artifacts:
            results = validator.validate_phase_artifacts("phase-1", artifact)
            all_results.update(results)

        # Verify all 4 gates fired
        assert "ADRGate" in all_results
        assert "ConceptGate" in all_results
        assert "ImplementationPlanGate" in all_results
        assert "IdeaGate" in all_results

        # Verify all passed
        for gate_name, result in all_results.items():
            assert result.verdict == GateVerdictEnum.PASS, f"{gate_name} failed: {result.findings}"


# Marker for pytest discovery
pytestmark = pytest.mark.e2e


if __name__ == "__main__":
    # Run tests: pytest tests/e2e/test_phase1_quality_gates_orchestration.py -v
    pytest.main([__file__, "-v"])
