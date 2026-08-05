"""Tests for Phase 3: Graph Validation & Re-Ranking."""

import pytest
from ..validation import GraphValidator, ValidatedGraphs
from ..filtering import FilteredGraphs
from ..classifier import ClassifiedTask
from ..normalizer import TaskNormalizer


@pytest.fixture
def normalizer():
    return TaskNormalizer()


@pytest.fixture
def validator():
    return GraphValidator()


@pytest.fixture
def simple_filtered_graphs(normalizer):
    """Create a simple FilteredGraphs for testing."""
    normalized = normalizer.normalize("fix bug in voice module for long audio")
    classified = ClassifiedTask(
        normalized=normalized,
        classification={
            "call_graph": (0.8, {"files": ["voice.py", "renderer.py"]}),
            "test_graph": (0.7, {"files": ["test_voice.py"]}),
            "adr_graph": (0.6, {"files": ["ADR-0185"]}),
        },
        confidence=0.7,
        recommended_graphs=["call_graph", "test_graph", "adr_graph"],
        skills_to_inject=[],
    )

    return FilteredGraphs(
        normalized=normalized,
        classified=classified,
        filtered_graphs=["call_graph", "test_graph", "adr_graph"],
        deduplicated_files={"voice.py": ["call_graph"]},
        ranked_graphs=[
            ("call_graph", 0.8),
            ("test_graph", 0.7),
            ("adr_graph", 0.6),
        ],
        redundancy_ratio=0.0,
    )


class TestGraphValidator:
    """Test graph validation."""

    def test_validator_completeness_check(self, validator, simple_filtered_graphs):
        """Validator should check which routers had data."""
        result = validator.validate(simple_filtered_graphs)
        assert isinstance(result.graphs_complete, dict)
        assert "call_graph" in result.graphs_complete
        assert result.graphs_complete["call_graph"] is True

    def test_validator_rerank(self, validator, simple_filtered_graphs):
        """Validator should re-rank based on completeness."""
        result = validator.validate(simple_filtered_graphs)
        assert isinstance(result.confidence_adjusted, dict)
        # Graphs with data should have scores
        assert result.confidence_adjusted["call_graph"] > 0.0

    def test_validator_output_structure(self, validator, simple_filtered_graphs):
        """ValidatedGraphs should have required fields."""
        result = validator.validate(simple_filtered_graphs)
        assert isinstance(result, ValidatedGraphs)
        assert result.filtered == simple_filtered_graphs
        assert isinstance(result.graphs_complete, dict)
        assert isinstance(result.confidence_adjusted, dict)
        assert isinstance(result.validation_notes, list)
        assert isinstance(result.final_confidence, float)

    def test_validator_notes(self, validator, simple_filtered_graphs):
        """Validator should generate human-readable notes."""
        result = validator.validate(simple_filtered_graphs)
        assert len(result.validation_notes) > 0
        assert any("Router" in note or "%" in note for note in result.validation_notes)

    def test_validator_final_confidence(self, validator, simple_filtered_graphs):
        """Final confidence should be mean of adjusted scores."""
        result = validator.validate(simple_filtered_graphs)
        expected = sum(result.confidence_adjusted.values()) / len(
            result.confidence_adjusted
        )
        assert result.final_confidence == pytest.approx(expected, abs=0.01)

    def test_validator_handles_empty(self, validator, normalizer):
        """Validator should handle empty classification."""
        normalized = normalizer.normalize("fix bug for testing validation")
        classified = ClassifiedTask(
            normalized=normalized,
            classification={},
            confidence=0.0,
            recommended_graphs=[],
            skills_to_inject=[],
        )

        filtered = FilteredGraphs(
            normalized=normalized,
            classified=classified,
            filtered_graphs=[],
            deduplicated_files={},
            ranked_graphs=[],
            redundancy_ratio=0.0,
        )

        result = validator.validate(filtered)
        assert result.graphs_complete == {}
        assert result.confidence_adjusted == {}
