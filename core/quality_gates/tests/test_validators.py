"""Tests for validators (ADR-0688)."""

import pytest
import tempfile
import os

from core.quality_gates.graph import KnowledgeGraph
from core.quality_gates.validators import (
    IdeaGateValidator,
    ConceptGateValidator,
    ADRGateValidator,
    ImplementationPlanGateValidator,
)
from core.quality_gates.models import VerdictType


class TestIdeaGateValidator:
    """Test IdeaGateValidator."""

    @pytest.fixture
    def validator(self):
        """Create a test validator."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            graph = KnowledgeGraph(db_path, tenant_id="test-tenant")
            validator = IdeaGateValidator(graph, "test-tenant")
            yield validator
            graph.close()

    def test_idea_pass_with_evidence(self, validator):
        """Test idea passes with sufficient evidence."""
        artifact = {
            "id": "IDEA-001",
            "description": "Test idea",
            "evidence_tasks": ["task1", "task2"],
            "evidence_commits": [],
            "recurrence_count": 0,
        }

        result = validator.validate(artifact)
        assert result.verdict == VerdictType.PASS
        assert result.confidence > 0.5

    def test_idea_pass_with_recurrence(self, validator):
        """Test idea passes with recurrence."""
        artifact = {
            "id": "IDEA-001",
            "description": "Test idea",
            "evidence_tasks": [],
            "evidence_commits": [],
            "recurrence_count": 2,
        }

        result = validator.validate(artifact)
        assert result.verdict == VerdictType.PASS

    def test_idea_fail_insufficient_evidence(self, validator):
        """Test idea fails with insufficient evidence."""
        artifact = {
            "id": "IDEA-001",
            "description": "Test idea",
            "evidence_tasks": ["task1"],
            "evidence_commits": [],
            "recurrence_count": 0,
        }

        result = validator.validate(artifact)
        assert result.verdict == VerdictType.FAIL
        assert result.confidence < 0.5

    def test_idea_confidence_scales_with_evidence(self, validator):
        """Test idea confidence increases with evidence."""
        artifact_1 = {
            "id": "IDEA-001",
            "description": "Test idea",
            "evidence_tasks": ["task1", "task2"],
            "evidence_commits": [],
            "recurrence_count": 0,
        }

        artifact_5 = {
            "id": "IDEA-002",
            "description": "Test idea",
            "evidence_tasks": ["task1", "task2", "task3", "task4", "task5"],
            "evidence_commits": ["commit1"],
            "recurrence_count": 1,
        }

        result_1 = validator.validate(artifact_1)
        result_5 = validator.validate(artifact_5)

        assert result_5.confidence > result_1.confidence


class TestConceptGateValidator:
    """Test ConceptGateValidator."""

    @pytest.fixture
    def validator(self):
        """Create a test validator."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            graph = KnowledgeGraph(db_path, tenant_id="test-tenant")
            validator = ConceptGateValidator(graph, "test-tenant")
            yield validator
            graph.close()

    def test_concept_pass_complete(self, validator):
        """Test concept passes with complete info."""
        artifact = {
            "id": "CONCEPT-0040",
            "narrative": " ".join(["word"] * 150),  # 150 words
            "boundaries": "This is a boundary definition",
            "evidence_commits": ["commit1", "commit2"],
            "skills": ["skill1"],
        }

        result = validator.validate(artifact)
        assert result.verdict == VerdictType.PASS
        assert result.confidence > 0.5

    def test_concept_fail_short_narrative(self, validator):
        """Test concept fails with short narrative."""
        artifact = {
            "id": "CONCEPT-0040",
            "narrative": "Short narrative",
            "boundaries": "Boundaries",
            "evidence_commits": ["commit1", "commit2"],
        }

        result = validator.validate(artifact)
        assert result.verdict == VerdictType.FAIL

    def test_concept_fail_missing_boundaries(self, validator):
        """Test concept fails with missing boundaries."""
        artifact = {
            "id": "CONCEPT-0040",
            "narrative": " ".join(["word"] * 150),
            "boundaries": "",
            "evidence_commits": ["commit1", "commit2"],
        }

        result = validator.validate(artifact)
        assert result.verdict == VerdictType.FAIL

    def test_concept_fail_insufficient_evidence(self, validator):
        """Test concept fails with insufficient evidence."""
        artifact = {
            "id": "CONCEPT-0040",
            "narrative": " ".join(["word"] * 150),
            "boundaries": "Boundaries",
            "evidence_commits": ["commit1"],
        }

        result = validator.validate(artifact)
        assert result.verdict == VerdictType.FAIL

    def test_concept_confidence_scales(self, validator):
        """Test concept confidence increases with detail."""
        artifact_minimal = {
            "id": "CONCEPT-001",
            "narrative": " ".join(["word"] * 100),
            "boundaries": "Boundaries",
            "evidence_commits": ["commit1", "commit2"],
        }

        artifact_detailed = {
            "id": "CONCEPT-002",
            "narrative": " ".join(["word"] * 300),
            "boundaries": " ".join(["boundary"] * 50),
            "evidence_commits": ["commit1", "commit2", "commit3", "commit4"],
        }

        result_minimal = validator.validate(artifact_minimal)
        result_detailed = validator.validate(artifact_detailed)

        assert result_detailed.confidence > result_minimal.confidence


class TestADRGateValidator:
    """Test ADRGateValidator."""

    @pytest.fixture
    def validator(self):
        """Create a test validator."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            graph = KnowledgeGraph(db_path, tenant_id="test-tenant")
            validator = ADRGateValidator(graph, "test-tenant")
            yield validator
            graph.close()

    def test_adr_pass_complete_frontmatter(self, validator):
        """Test ADR passes with complete frontmatter."""
        artifact = {
            "id": "ADR-0688",
            "status": "proposed",
            "depends_on": ["ADR-0687"],
            "paths": ["core/quality_gates/"],
            "docs": ["docs/quality-gates/"],
            "commits": ["abc123"],
        }

        result = validator.validate(artifact)
        assert result.verdict == VerdictType.PASS
        assert result.confidence == 0.95

    def test_adr_pass_empty_depends_on(self, validator):
        """Test ADR passes with empty depends_on."""
        artifact = {
            "id": "ADR-0688",
            "status": "proposed",
            "depends_on": [],
            "paths": ["core/quality_gates/"],
            "docs": ["docs/quality-gates/"],
            "commits": ["abc123"],
        }

        result = validator.validate(artifact)
        assert result.verdict == VerdictType.PASS

    def test_adr_fail_missing_id(self, validator):
        """Test ADR fails with missing id."""
        artifact = {
            "status": "proposed",
            "depends_on": [],
            "paths": ["core/quality_gates/"],
            "docs": ["docs/quality-gates/"],
            "commits": ["abc123"],
        }

        result = validator.validate(artifact)
        assert result.verdict == VerdictType.FAIL

    def test_adr_fail_missing_status(self, validator):
        """Test ADR fails with missing status."""
        artifact = {
            "id": "ADR-0688",
            "depends_on": [],
            "paths": ["core/quality_gates/"],
            "docs": ["docs/quality-gates/"],
            "commits": ["abc123"],
        }

        result = validator.validate(artifact)
        assert result.verdict == VerdictType.FAIL

    def test_adr_fail_empty_paths(self, validator):
        """Test ADR fails with empty paths."""
        artifact = {
            "id": "ADR-0688",
            "status": "proposed",
            "depends_on": [],
            "paths": [],
            "docs": ["docs/quality-gates/"],
            "commits": ["abc123"],
        }

        result = validator.validate(artifact)
        assert result.verdict == VerdictType.FAIL


class TestImplementationPlanGateValidator:
    """Test ImplementationPlanGateValidator."""

    @pytest.fixture
    def validator(self):
        """Create a test validator."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            graph = KnowledgeGraph(db_path, tenant_id="test-tenant")
            validator = ImplementationPlanGateValidator(graph, "test-tenant")
            yield validator
            graph.close()

    def test_plan_pass_complete(self, validator):
        """Test implementation plan passes with complete info."""
        artifact = {
            "id": "PLAN-001",
            "subsystem": "quality_gates",
            "phases": [
                {"name": "Phase 1", "duration": "1 week"},
                {"name": "Phase 2", "duration": "1 week"},
                {"name": "Phase 3", "duration": "1 week"},
            ],
            "success_criteria": "All tests pass",
            "resource_estimate": "40 hours",
            "timeline_weeks": 3,
        }

        result = validator.validate(artifact)
        assert result.verdict == VerdictType.PASS
        assert result.confidence > 0.5

    def test_plan_fail_insufficient_phases(self, validator):
        """Test plan fails with insufficient phases."""
        artifact = {
            "id": "PLAN-001",
            "subsystem": "quality_gates",
            "phases": [
                {"name": "Phase 1", "duration": "1 week"},
                {"name": "Phase 2", "duration": "1 week"},
            ],
            "success_criteria": "All tests pass",
            "resource_estimate": "40 hours",
        }

        result = validator.validate(artifact)
        assert result.verdict == VerdictType.FAIL

    def test_plan_fail_missing_success_criteria(self, validator):
        """Test plan fails with missing success criteria."""
        artifact = {
            "id": "PLAN-001",
            "subsystem": "quality_gates",
            "phases": [
                {"name": "Phase 1"},
                {"name": "Phase 2"},
                {"name": "Phase 3"},
            ],
            "success_criteria": "",
            "resource_estimate": "40 hours",
        }

        result = validator.validate(artifact)
        assert result.verdict == VerdictType.FAIL

    def test_plan_fail_missing_resource_estimate(self, validator):
        """Test plan fails with missing resource estimate."""
        artifact = {
            "id": "PLAN-001",
            "subsystem": "quality_gates",
            "phases": [
                {"name": "Phase 1"},
                {"name": "Phase 2"},
                {"name": "Phase 3"},
            ],
            "success_criteria": "All tests pass",
            "resource_estimate": "",
        }

        result = validator.validate(artifact)
        assert result.verdict == VerdictType.FAIL

    def test_plan_confidence_scales(self, validator):
        """Test plan confidence increases with detail."""
        artifact_minimal = {
            "id": "PLAN-001",
            "subsystem": "quality_gates",
            "phases": [{"name": "P1"}, {"name": "P2"}, {"name": "P3"}],
            "success_criteria": "Pass",
            "resource_estimate": "30 hours",
            "timeline_weeks": 1,
        }

        artifact_detailed = {
            "id": "PLAN-002",
            "subsystem": "quality_gates",
            "phases": [
                {"name": "P1", "deliverables": ["code", "tests"]},
                {"name": "P2", "deliverables": ["docs"]},
                {"name": "P3", "deliverables": ["review"]},
                {"name": "P4", "deliverables": ["deploy"]},
                {"name": "P5", "deliverables": ["monitor"]},
            ],
            "success_criteria": "All tests pass, 100% coverage, docs complete",
            "resource_estimate": "120 hours with 3 reviewers",
            "timeline_weeks": 8,
        }

        result_minimal = validator.validate(artifact_minimal)
        result_detailed = validator.validate(artifact_detailed)

        assert result_detailed.confidence > result_minimal.confidence
