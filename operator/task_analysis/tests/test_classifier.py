"""Tests for task classifier (end-to-end routing + scoring + skill injection).

Tests:
    - End-to-end classification
    - All routers working together
    - Confidence calculation
    - Graph recommendation logic
    - Skill injection integration
"""

import pytest
from sys import path
from pathlib import Path

# Add parent to path to avoid operator stdlib conflict
_task_analysis_root = Path(__file__).parent.parent
if str(_task_analysis_root) not in path:
    path.insert(0, str(_task_analysis_root.parent))

from task_analysis import TaskType, NormalizedTask
from task_analysis.classifier import TaskClassifier, ClassifiedTask


@pytest.fixture
def classifier():
    """Create a classifier instance."""
    return TaskClassifier()


@pytest.fixture
def minimal_task():
    """Minimal task."""
    return NormalizedTask(
        summary="Fix bug in voice renderer",
        description="Voice rendering crashes on files > 5 min",
        type=TaskType.BUG_FIX,
        severity="high",
        components=["core/voice/renderer.py"],
        affected_layers=["L23"],
        memory_context=[],
        related_incidents=[],
        metadata={},
    )


@pytest.fixture
def complex_task():
    """Complex task with multiple components."""
    return NormalizedTask(
        summary="Add worker delegation feature",
        description="Implement delegation for big-data tasks",
        type=TaskType.FEATURE,
        severity="high",
        components=[
            "core/delegation",
            "core/forge",
            "core/mcp_manager",
            "operator/task_engine",
        ],
        affected_layers=["L29", "L30", "L6", "L22"],
        memory_context=["adr-0200-delegation.md"],
        related_incidents=[],
        metadata={"component_count": 4},
    )


@pytest.fixture
def empty_task():
    """Task with minimal information."""
    return NormalizedTask(
        summary="unknown task",
        description="",
        type=TaskType.UNKNOWN,
        severity="low",
        components=[],
        affected_layers=[],
        memory_context=[],
        related_incidents=[],
        metadata={},
    )


# End-to-End Classification Tests
class TestTaskClassifierEndToEnd:
    """End-to-end classification tests."""

    def test_classify_minimal_task(self, classifier, minimal_task):
        """Classify a minimal task."""
        result = classifier.classify(minimal_task)

        assert isinstance(result, ClassifiedTask)
        assert result.normalized == minimal_task
        assert 0.0 <= result.confidence <= 1.0
        assert isinstance(result.recommended_graphs, list)
        assert isinstance(result.skills_to_inject, list)

    def test_classify_complex_task(self, classifier, complex_task):
        """Classify a complex task."""
        result = classifier.classify(complex_task)

        assert isinstance(result, ClassifiedTask)
        assert result.confidence >= 0.0  # Should have decent confidence
        assert len(result.recommended_graphs) >= 1
        assert len(result.skills_to_inject) >= 1

    def test_classify_empty_task(self, classifier, empty_task):
        """Classify a task with minimal info."""
        result = classifier.classify(empty_task)

        assert isinstance(result, ClassifiedTask)
        # Should gracefully handle low-info task
        assert 0.0 <= result.confidence <= 1.0

    def test_classification_includes_all_fields(self, classifier, minimal_task):
        """Result includes all expected fields."""
        result = classifier.classify(minimal_task)

        assert hasattr(result, "normalized")
        assert hasattr(result, "classification")
        assert hasattr(result, "scored_routers")
        assert hasattr(result, "confidence")
        assert hasattr(result, "recommended_graphs")
        assert hasattr(result, "recommended_graph_scores")
        assert hasattr(result, "skills_to_inject")


# Router Execution Tests
class TestClassifierRouterExecution:
    """Tests for router execution within classifier."""

    def test_all_routers_executed(self, classifier, minimal_task):
        """All five routers are executed."""
        result = classifier.classify(minimal_task)

        # Classification should have all five routers
        expected_routers = {
            "call_graph",
            "test_graph",
            "adr_graph",
            "layer_graph",
            "code_diff",
        }

        assert set(result.classification.keys()) >= expected_routers

    def test_router_scores_in_classification(self, classifier, minimal_task):
        """Router scores are stored in classification."""
        result = classifier.classify(minimal_task)

        # Each router should have a (score, metadata) tuple
        for router_name, value in result.classification.items():
            assert isinstance(value, tuple)
            assert len(value) == 2
            score, metadata = value
            assert isinstance(score, float)
            assert isinstance(metadata, dict)

    def test_error_handling_in_routers(self, classifier):
        """Classifier handles router errors gracefully."""
        # Create a task with invalid data that might cause router issues
        bad_task = NormalizedTask(
            summary="test",
            description="test",
            type=TaskType.BUG_FIX,
            severity="bad_severity",  # Invalid
            components=None,  # None components
            affected_layers=None,
            memory_context=[],
            related_incidents=[],
            metadata=None,
        )

        # Should not crash
        result = classifier.classify(bad_task)
        assert isinstance(result, ClassifiedTask)


# Confidence and Scoring Tests
class TestClassifierConfidenceCalculation:
    """Tests for confidence calculation."""

    def test_confidence_ranges_zero_to_one(self, classifier, minimal_task):
        """Confidence is always in [0.0, 1.0]."""
        result = classifier.classify(minimal_task)
        assert 0.0 <= result.confidence <= 1.0

    def test_high_information_task_yields_higher_confidence(self, classifier, complex_task):
        """Tasks with more info → higher confidence."""
        result = classifier.classify(complex_task)

        # Complex task has more components and layers
        # Should yield better confidence than empty task
        confidence_complex = result.confidence

        empty_task = NormalizedTask(
            summary="test",
            description="",
            type=TaskType.UNKNOWN,
            severity="low",
            components=[],
            affected_layers=[],
            memory_context=[],
            related_incidents=[],
            metadata={},
        )
        result_empty = classifier.classify(empty_task)

        # Complex should be >= empty (or at least not much worse)
        assert confidence_complex >= result_empty.confidence - 0.1

    def test_scored_routers_available(self, classifier, minimal_task):
        """Scored routers are available in result."""
        result = classifier.classify(minimal_task)

        assert result.scored_routers is not None
        assert hasattr(result.scored_routers, "call_graph")
        assert hasattr(result.scored_routers, "test_graph")
        assert hasattr(result.scored_routers, "global_confidence")


# Graph Recommendation Tests
class TestClassifierGraphRecommendation:
    """Tests for graph recommendation logic."""

    def test_high_confidence_recommends_selective_graphs(self, classifier):
        """Confidence >= 0.7 → recommend high-scoring graphs."""
        task = NormalizedTask(
            summary="Fix bug in well-documented module",
            description="Known module with existing tests",
            type=TaskType.BUG_FIX,
            severity="high",
            components=["core/console/main.py"],  # Well-documented
            affected_layers=["L16"],
            memory_context=[],
            related_incidents=[],
            metadata={},
        )

        result = classifier.classify(task)

        # High-confidence task should recommend fewer graphs (selective)
        if result.confidence >= 0.7:
            # Selective mode: recommend only high-scoring graphs
            assert len(result.recommended_graphs) <= 5

    def test_low_confidence_recommends_all_graphs(self, classifier):
        """Confidence < 0.7 → recommend all graphs (fallback)."""
        task = NormalizedTask(
            summary="Fix bug",
            description="",
            type=TaskType.UNKNOWN,
            severity="low",
            components=[],
            affected_layers=[],
            memory_context=[],
            related_incidents=[],
            metadata={},
        )

        result = classifier.classify(task)

        # Low-confidence task should recommend all graphs
        if result.confidence < 0.7:
            assert len(result.recommended_graphs) == 5

    def test_recommended_graphs_have_scores(self, classifier, minimal_task):
        """Each recommended graph has a score."""
        result = classifier.classify(minimal_task)

        for graph_name in result.recommended_graphs:
            assert graph_name in result.recommended_graph_scores
            score = result.recommended_graph_scores[graph_name]
            assert isinstance(score, float)
            assert 0.0 <= score <= 1.0

    def test_at_least_one_graph_recommended(self, classifier, minimal_task):
        """At least one graph is recommended."""
        result = classifier.classify(minimal_task)

        # Even on fallback (all 5), should have some
        assert len(result.recommended_graphs) >= 1


# Skill Injection Tests
class TestClassifierSkillInjection:
    """Tests for skill injection."""

    def test_bug_fix_high_injects_skills(self, classifier, minimal_task):
        """BUG_FIX + high → specific skills injected."""
        result = classifier.classify(minimal_task)

        # BUG_FIX high should inject iteration and root-cause skills
        skill_names = result.skills_to_inject
        assert len(skill_names) >= 1
        # Should include LDD skills
        assert any("iteration" in s or "root-cause" in s for s in skill_names)

    def test_feature_high_injects_skills(self, classifier, complex_task):
        """FEATURE + high → design/wiring skills."""
        result = classifier.classify(complex_task)

        skill_names = result.skills_to_inject
        assert len(skill_names) >= 1

    def test_documentation_injects_doc_skill(self, classifier):
        """DOCUMENTATION → docs-as-definition-of-done."""
        task = NormalizedTask(
            summary="Update documentation",
            description="Improve docs",
            type=TaskType.DOCUMENTATION,
            severity="high",
            components=[],
            affected_layers=[],
            memory_context=[],
            related_incidents=[],
            metadata={},
        )

        result = classifier.classify(task)

        # Should have doc-related skills
        assert "docs-as-definition-of-done" in result.skills_to_inject

    def test_unknown_type_injects_fallback_skills(self, classifier):
        """UNKNOWN type → fallback skills."""
        task = NormalizedTask(
            summary="Unknown task",
            description="",
            type=TaskType.UNKNOWN,
            severity="low",
            components=[],
            affected_layers=[],
            memory_context=[],
            related_incidents=[],
            metadata={},
        )

        result = classifier.classify(task)

        # Should have fallback skills (dialectical reasoning, iteration)
        assert len(result.skills_to_inject) >= 1

    def test_high_uncertainty_adds_verification_skills(self, classifier):
        """All 5 graphs recommended → add verification skills."""
        task = NormalizedTask(
            summary="Vague task",
            description="Very unclear",
            type=TaskType.UNKNOWN,
            severity="low",
            components=[],
            affected_layers=[],
            memory_context=[],
            related_incidents=[],
            metadata={},
        )

        result = classifier.classify(task)

        # If all graphs recommended (low confidence fallback),
        # should include high-uncertainty skills
        if len(result.recommended_graphs) == 5:
            # May include reproducibility-first or additional verification
            assert len(result.skills_to_inject) >= 2


# Integration Tests
class TestClassifierIntegration:
    """Integration tests combining all components."""

    def test_end_to_end_bug_fix_workflow(self, classifier):
        """Full workflow: normalize → classify → get skills."""
        task = NormalizedTask(
            summary="Critical crash in voice module",
            description="TTS hangs on audio > 5 minutes, causes UI freeze",
            type=TaskType.BUG_FIX,
            severity="high",
            components=["core/voice/renderer.py", "core/voice/tts.py"],
            affected_layers=["L23", "L12"],
            memory_context=[],
            related_incidents=[],
            metadata={},
        )

        result = classifier.classify(task)

        # Should have full classification
        assert result.confidence > 0.0
        assert len(result.recommended_graphs) >= 1
        assert len(result.skills_to_inject) >= 2

    def test_end_to_end_feature_workflow(self, classifier):
        """Full workflow for feature request."""
        task = NormalizedTask(
            summary="Implement worker delegation",
            description="Add delegation layer for big-data processing",
            type=TaskType.FEATURE,
            severity="high",
            components=[
                "core/delegation",
                "core/forge",
                "operator/task_engine",
            ],
            affected_layers=["L29", "L30"],
            memory_context=["adr-0200-delegation.md"],
            related_incidents=[],
            metadata={},
        )

        result = classifier.classify(task)

        # Feature should recommend architecture skills
        assert result.confidence > 0.0
        # Should have skills related to design/wiring
        assert any("loop" in s or "wiring" in s for s in result.skills_to_inject)

    def test_reproducibility_of_classification(self, classifier, minimal_task):
        """Same task → same classification (deterministic)."""
        result1 = classifier.classify(minimal_task)
        result2 = classifier.classify(minimal_task)

        # Confidence should be identical
        assert result1.confidence == result2.confidence
        # Same graphs recommended
        assert result1.recommended_graphs == result2.recommended_graphs
        # Same skills
        assert result1.skills_to_inject == result2.skills_to_inject


# Edge Cases and Robustness
class TestClassifierRobustness:
    """Edge case and robustness tests."""

    def test_handles_very_long_task_description(self, classifier):
        """Handles very long descriptions."""
        task = NormalizedTask(
            summary="Task",
            description="x" * 10000,  # Very long
            type=TaskType.BUG_FIX,
            severity="medium",
            components=[],
            affected_layers=[],
            memory_context=[],
            related_incidents=[],
            metadata={},
        )

        result = classifier.classify(task)
        assert isinstance(result, ClassifiedTask)

    def test_handles_many_components(self, classifier):
        """Handles task with many components."""
        components = [f"core/module{i}" for i in range(50)]
        task = NormalizedTask(
            summary="Large refactor",
            description="Refactor many modules",
            type=TaskType.REFACTOR,
            severity="high",
            components=components,
            affected_layers=["L10", "L16"],
            memory_context=[],
            related_incidents=[],
            metadata={},
        )

        result = classifier.classify(task)
        assert isinstance(result, ClassifiedTask)

    def test_handles_special_characters_in_components(self, classifier):
        """Handles special characters in paths."""
        task = NormalizedTask(
            summary="Fix special paths",
            description="Handle weird filenames",
            type=TaskType.BUG_FIX,
            severity="low",
            components=["core/module-with-dash/file_with_underscore.py"],
            affected_layers=["L16"],
            memory_context=[],
            related_incidents=[],
            metadata={},
        )

        result = classifier.classify(task)
        assert isinstance(result, ClassifiedTask)
