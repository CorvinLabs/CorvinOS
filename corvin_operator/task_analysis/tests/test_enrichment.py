"""Tests for Phase 4: Task Enrichment."""

import pytest
from ..enrichment import (
    TaskComplexityCalculator,
    ModelSelector,
    CostEstimator,
    TaskEnricher,
    EnrichedTask,
)
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
def validated_graphs(normalizer, validator):
    """Create ValidatedGraphs for enrichment testing."""
    normalized = normalizer.normalize("fix high severity bug in voice module")
    classified = ClassifiedTask(
        normalized=normalized,
        classification={
            "call_graph": (0.8, {"files": ["voice.py"]}),
            "test_graph": (0.7, {"files": ["test_voice.py"]}),
        },
        confidence=0.75,
        recommended_graphs=["call_graph", "test_graph"],
        skills_to_inject=[],
    )

    filtered = FilteredGraphs(
        normalized=normalized,
        classified=classified,
        filtered_graphs=["call_graph", "test_graph"],
        deduplicated_files={"voice.py": ["call_graph"]},
        ranked_graphs=[("call_graph", 0.8), ("test_graph", 0.7)],
        redundancy_ratio=0.0,
    )

    return validator.validate(filtered)


class TestComplexityCalculator:
    """Test task complexity calculation."""

    def test_complexity_ranges_zero_to_one(self, validated_graphs):
        """Complexity should be in [0.0, 1.0]."""
        calc = TaskComplexityCalculator()
        complexity = calc.calculate(validated_graphs)
        assert 0.0 <= complexity <= 1.0

    def test_complexity_bug_fix_lower_than_incident(self, normalizer, validator):
        """BUG_FIX should have lower complexity than INCIDENT."""
        bug_fix_task = normalizer.normalize("fix bug in voice code")
        incident_task = normalizer.normalize("incident: system down completely")

        bug_fix_classified = ClassifiedTask(
            normalized=bug_fix_task,
            classification={"call_graph": (0.8, {"files": ["test.py"]})},
            confidence=0.8,
            recommended_graphs=["call_graph"],
            skills_to_inject=[],
        )

        incident_classified = ClassifiedTask(
            normalized=incident_task,
            classification={"call_graph": (0.8, {"files": ["test.py"]})},
            confidence=0.8,
            recommended_graphs=["call_graph"],
            skills_to_inject=[],
        )

        bug_fix_filtered = FilteredGraphs(
            normalized=bug_fix_task,
            classified=bug_fix_classified,
            filtered_graphs=["call_graph"],
            deduplicated_files={},
            ranked_graphs=[],
            redundancy_ratio=0.0,
        )

        incident_filtered = FilteredGraphs(
            normalized=incident_task,
            classified=incident_classified,
            filtered_graphs=["call_graph"],
            deduplicated_files={},
            ranked_graphs=[],
            redundancy_ratio=0.0,
        )

        bug_fix_validated = validator.validate(bug_fix_filtered)
        incident_validated = validator.validate(incident_filtered)

        calc = TaskComplexityCalculator()
        bug_fix_complexity = calc.calculate(bug_fix_validated)
        incident_complexity = calc.calculate(incident_validated)

        # INCIDENT should be more complex than BUG_FIX
        assert incident_complexity > bug_fix_complexity


class TestModelSelector:
    """Test model selection logic."""

    def test_high_complexity_selects_opus(self):
        """complexity >= 0.6 should select Opus."""
        selector = ModelSelector()
        model = selector.select(0.7, "medium")
        assert model == "opus"

    def test_high_severity_selects_opus(self):
        """High severity should select Opus."""
        selector = ModelSelector()
        model = selector.select(0.3, "high")
        assert model == "opus"

    def test_low_complexity_medium_severity_selects_haiku(self):
        """Low complexity + medium severity should select Haiku."""
        selector = ModelSelector()
        model = selector.select(0.3, "medium")
        assert model == "haiku"

    def test_boundary_complexity_0_6(self):
        """At exactly 0.6 complexity should select Opus."""
        selector = ModelSelector()
        model = selector.select(0.6, "low")
        assert model == "opus"


class TestCostEstimator:
    """Test cost estimation."""

    def test_cost_haiku_cheaper_than_opus(self, validated_graphs):
        """Haiku should always be cheaper than Opus."""
        estimator = CostEstimator()
        tokens_h, cost_h = estimator.estimate(validated_graphs, "haiku")
        tokens_o, cost_o = estimator.estimate(validated_graphs, "opus")

        # Same tokens, but Opus costs more
        assert tokens_h == tokens_o
        assert cost_h < cost_o

    def test_cost_estimate_positive(self, validated_graphs):
        """Cost estimates should be positive."""
        estimator = CostEstimator()
        tokens, cost = estimator.estimate(validated_graphs, "haiku")
        assert tokens > 0
        assert cost > 0.0

    def test_cost_estimate_scales_with_model(self, validated_graphs):
        """Cost should scale with model pricing."""
        estimator = CostEstimator()
        custom_pricing = {"haiku": 1.0, "opus": 10.0}
        tokens, cost_h = estimator.estimate(validated_graphs, "haiku", custom_pricing)
        tokens_o, cost_o = estimator.estimate(validated_graphs, "opus", custom_pricing)

        # Opus should be ~10x more expensive
        assert cost_o / cost_h == pytest.approx(10.0, rel=0.1)


class TestTaskEnricher:
    """Test the full enrichment pipeline."""

    def test_enricher_output_structure(self, validated_graphs):
        """Enriched task should have all required fields."""
        enricher = TaskEnricher()
        enriched = enricher.enrich(validated_graphs)

        assert isinstance(enriched, EnrichedTask)
        assert enriched.validated == validated_graphs
        assert 0.0 <= enriched.task_complexity <= 1.0
        assert enriched.model_recommendation in ["haiku", "opus"]
        assert enriched.estimated_tokens > 0
        assert enriched.estimated_cost_usd > 0.0

    def test_enricher_high_complexity_suggests_opus(self, normalizer, validator):
        """High-complexity task should suggest Opus."""
        task = normalizer.normalize("major refactor entire architecture rewrite")
        classified = ClassifiedTask(
            normalized=task,
            classification={
                "call_graph": (0.9, {"files": ["a.py", "b.py", "c.py", "d.py"]}),
            },
            confidence=0.9,
            recommended_graphs=["call_graph"],
            skills_to_inject=[],
        )

        filtered = FilteredGraphs(
            normalized=task,
            classified=classified,
            filtered_graphs=["call_graph"],
            deduplicated_files={},
            ranked_graphs=[],
            redundancy_ratio=0.0,
        )

        validated = validator.validate(filtered)
        enricher = TaskEnricher()
        enriched = enricher.enrich(validated)

        # Complex refactor should use Opus
        assert enriched.model_recommendation == "opus"

    def test_enricher_reproducible(self, validated_graphs):
        """Same input should produce same output."""
        enricher = TaskEnricher()
        enriched1 = enricher.enrich(validated_graphs)
        enriched2 = enricher.enrich(validated_graphs)

        assert enriched1.task_complexity == enriched2.task_complexity
        assert enriched1.model_recommendation == enriched2.model_recommendation
        assert enriched1.estimated_tokens == enriched2.estimated_tokens
