"""Tests for Phase 2: Graph Filtering & Deduplication."""

import pytest
from ..filtering import FilterConfig, GraphFilteringPipeline, FilteredGraphs
from ..classifier import ClassifiedTask, TaskClassifier
from ..normalizer import TaskNormalizer, TaskType


@pytest.fixture
def normalizer():
    """Create a TaskNormalizer."""
    return TaskNormalizer()


@pytest.fixture
def classifier():
    """Create a TaskClassifier."""
    return TaskClassifier()


@pytest.fixture
def pipeline():
    """Create a GraphFilteringPipeline."""
    return GraphFilteringPipeline()


class TestFilterConfig:
    """Test FilterConfig validation."""

    def test_valid_default_config(self):
        """Default config should be valid."""
        config = FilterConfig()
        config.validate()  # Should not raise

    def test_custom_valid_config(self):
        """Custom valid config should pass."""
        config = FilterConfig(
            relevance_threshold=0.5, min_graphs=1, max_graphs=3
        )
        config.validate()

    def test_invalid_relevance_threshold_low(self):
        """Relevance threshold < 0.0 should fail."""
        config = FilterConfig(relevance_threshold=-0.1)
        with pytest.raises(ValueError, match="relevance_threshold"):
            config.validate()

    def test_invalid_relevance_threshold_high(self):
        """Relevance threshold > 1.0 should fail."""
        config = FilterConfig(relevance_threshold=1.1)
        with pytest.raises(ValueError, match="relevance_threshold"):
            config.validate()

    def test_invalid_min_graphs(self):
        """min_graphs < 1 should fail."""
        config = FilterConfig(min_graphs=0)
        with pytest.raises(ValueError, match="min_graphs"):
            config.validate()

    def test_invalid_max_graphs(self):
        """max_graphs < min_graphs should fail."""
        config = FilterConfig(min_graphs=3, max_graphs=2)
        with pytest.raises(ValueError, match="max_graphs"):
            config.validate()


class TestGraphFilteringPipeline:
    """Test the filtering pipeline."""

    def test_pipeline_with_none_classified(self, pipeline):
        """Pipeline should handle None classified gracefully."""
        result = pipeline.process(None)
        assert result.filtered_graphs == []
        assert result.deduplicated_files == {}

    def test_pipeline_with_empty_classification(self, pipeline, normalizer):
        """Pipeline should handle empty classification."""
        # Create minimal classified task
        task = "test task that needs normalization"
        normalized = normalizer.normalize(task)
        classified = ClassifiedTask(
            normalized=normalized,
            classification={},
            confidence=0.0,
            recommended_graphs=[],
            skills_to_inject=[],
        )

        result = pipeline.process(classified)
        assert result.filtered_graphs == []

    def test_pipeline_preserves_input(self, pipeline, normalizer):
        """Pipeline should preserve input task context."""
        task = "test task for graph preservation"
        normalized = normalizer.normalize(task)
        classified = ClassifiedTask(
            normalized=normalized,
            classification={
                "call_graph": (0.8, {"files": ["test.py"]})
            },
            confidence=0.8,
            recommended_graphs=["call_graph"],
            skills_to_inject=[],
        )

        result = pipeline.process(classified, normalized=normalized)
        assert result.normalized == normalized
        assert result.classified == classified

    def test_deduplication_identifies_overlaps(self, pipeline, normalizer):
        """Deduplication should find files in multiple graphs."""
        normalized = normalizer.normalize("fix bug in voice module")
        classified = ClassifiedTask(
            normalized=normalized,
            classification={
                "call_graph": (0.8, {"files": ["module.py", "utils.py"]}),
                "test_graph": (0.7, {"files": ["test_module.py", "utils.py"]}),
            },
            confidence=0.75,
            recommended_graphs=["call_graph", "test_graph"],
            skills_to_inject=[],
        )

        result = pipeline.process(classified)
        # utils.py should appear in both graphs
        assert "utils.py" in result.deduplicated_files
        assert len(result.deduplicated_files["utils.py"]) == 2

    def test_ranking_by_confidence(self, pipeline, normalizer):
        """Ranking should prefer higher-confidence graphs."""
        normalized = normalizer.normalize("fix bug in voice module")
        classified = ClassifiedTask(
            normalized=normalized,
            classification={
                "call_graph": (0.9, {"files": ["a.py"]}),
                "test_graph": (0.5, {"files": ["b.py"]}),
            },
            confidence=0.7,
            recommended_graphs=["call_graph", "test_graph"],
            skills_to_inject=[],
        )

        result = pipeline.process(classified)
        # call_graph should rank higher
        assert result.ranked_graphs[0][0] == "call_graph"
        assert result.ranked_graphs[0][1] > result.ranked_graphs[1][1]

    def test_filtering_respects_threshold(self, pipeline, normalizer):
        """Filtering should remove low-confidence graphs."""
        normalized = normalizer.normalize("fix bug in voice module")
        classified = ClassifiedTask(
            normalized=normalized,
            classification={
                "call_graph": (0.8, {"files": ["a.py"]}),
                "test_graph": (0.2, {"files": ["b.py"]}),
            },
            confidence=0.5,
            recommended_graphs=["call_graph", "test_graph"],
            skills_to_inject=[],
        )

        config = FilterConfig(relevance_threshold=0.5)
        result = pipeline.process(classified, config=config)

        # test_graph (0.2) should be filtered out
        assert "call_graph" in result.filtered_graphs
        assert "test_graph" not in result.filtered_graphs

    def test_filtering_min_graphs_fallback(self, pipeline, normalizer):
        """Filtering should keep min_graphs even if below threshold."""
        normalized = normalizer.normalize("fix bug in voice module")
        classified = ClassifiedTask(
            normalized=normalized,
            classification={
                "call_graph": (0.2, {"files": ["a.py"]}),
                "test_graph": (0.1, {"files": ["b.py"]}),
            },
            confidence=0.15,
            recommended_graphs=["call_graph", "test_graph"],
            skills_to_inject=[],
        )

        config = FilterConfig(relevance_threshold=0.9, min_graphs=1)
        result = pipeline.process(classified, config=config)

        # Should keep best graph despite threshold
        assert len(result.filtered_graphs) >= 1
        assert result.filtered_graphs[0] == "call_graph"

    def test_filtering_max_graphs_limit(self, pipeline, normalizer):
        """Filtering should respect max_graphs."""
        normalized = normalizer.normalize("fix bug in voice module")
        classified = ClassifiedTask(
            normalized=normalized,
            classification={
                "call_graph": (0.9, {"files": ["a.py"]}),
                "test_graph": (0.8, {"files": ["b.py"]}),
                "adr_graph": (0.7, {"files": ["c.py"]}),
                "layer_graph": (0.6, {"files": ["d.py"]}),
            },
            confidence=0.75,
            recommended_graphs=["call_graph", "test_graph", "adr_graph", "layer_graph"],
            skills_to_inject=[],
        )

        config = FilterConfig(max_graphs=2)
        result = pipeline.process(classified, config=config)

        # Should not exceed max_graphs
        assert len(result.filtered_graphs) <= 2

    def test_redundancy_calculation(self, pipeline, normalizer):
        """Redundancy should be calculated correctly."""
        normalized = normalizer.normalize("fix bug in voice module")
        classified = ClassifiedTask(
            normalized=normalized,
            classification={
                "call_graph": (0.8, {"files": ["shared.py", "a.py"]}),
                "test_graph": (0.7, {"files": ["shared.py", "b.py"]}),
            },
            confidence=0.75,
            recommended_graphs=["call_graph", "test_graph"],
            skills_to_inject=[],
        )

        result = pipeline.process(classified)
        # 4 total entries (shared.py×2, a.py, b.py), 3 unique → redundancy = 1/4 = 0.25
        assert result.redundancy_ratio == pytest.approx(0.25, abs=0.01)

    def test_deterministic_filtering(self, pipeline, normalizer):
        """Same input should produce same output."""
        normalized = normalizer.normalize("test bug in voice")
        classified = ClassifiedTask(
            normalized=normalized,
            classification={
                "call_graph": (0.8, {"files": ["voice.py"]}),
                "test_graph": (0.7, {"files": ["test_voice.py"]}),
                "adr_graph": (0.6, {"files": ["ADR-0180"]}),
            },
            confidence=0.7,
            recommended_graphs=["call_graph", "test_graph", "adr_graph"],
            skills_to_inject=[],
        )

        result1 = pipeline.process(classified)
        result2 = pipeline.process(classified)

        assert result1.filtered_graphs == result2.filtered_graphs
        assert result1.ranked_graphs == result2.ranked_graphs

    def test_output_structure(self, pipeline, normalizer):
        """FilteredGraphs should have all required fields."""
        normalized = normalizer.normalize("fix bug in voice module")
        classified = ClassifiedTask(
            normalized=normalized,
            classification={"call_graph": (0.8, {"files": ["a.py"]})},
            confidence=0.8,
            recommended_graphs=["call_graph"],
            skills_to_inject=[],
        )

        result = pipeline.process(classified, normalized=normalized)

        assert isinstance(result, FilteredGraphs)
        assert result.normalized == normalized
        assert result.classified == classified
        assert isinstance(result.filtered_graphs, list)
        assert isinstance(result.deduplicated_files, dict)
        assert isinstance(result.ranked_graphs, list)
        assert isinstance(result.redundancy_ratio, float)
