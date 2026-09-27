"""
ML Feedback Loop Pipeline Tests (Phase 3c)
"""

import pytest
from core.console.corvin_console.services.ml_feedback_pipeline import (
    FeedbackCollector,
    ModelRegistry,
    ModelVersion,
    ModelStatus,
    ABTestFramework,
    get_feedback_collector,
    get_model_registry,
    get_ab_framework,
)


class TestFeedbackCollector:
    """Phase 3c: Feedback collection for retraining"""

    def test_add_feedback(self):
        """Test: Collect user feedback"""
        collector = FeedbackCollector(retraining_threshold=100)

        collector.add_feedback(
            session_id="sess-001",
            strategy="syntax_aware",
            summary="def route(): pass",
            user_correction="def route(task): return dispatch(task)",
            quality_score=3.5,
        )

        stats = collector.get_feedback_stats("syntax_aware")
        assert stats["count"] == 1
        assert stats["avg_score"] == 3.5

    def test_retraining_threshold(self):
        """Test: Trigger retraining when threshold reached"""
        collector = FeedbackCollector(retraining_threshold=3)

        # Below threshold → no trigger
        assert collector.add_feedback("s1", "syntax_aware", "code1", None, 4.0) is None
        assert collector.add_feedback("s2", "syntax_aware", "code2", None, 4.5) is None

        # The 3rd sample reaches threshold=3 → the batch of 3 is handed off
        batch = collector.add_feedback("s3", "syntax_aware", "code3", None, 4.2)
        assert [r["session_id"] for r in batch] == ["s1", "s2", "s3"]

        # ...and the buffer restarts: the next sample does NOT re-trigger
        assert collector.add_feedback("s4", "syntax_aware", "code4", None, 4.1) is None
        assert collector.get_feedback_stats("syntax_aware")["count"] == 1


class TestModelRegistry:
    """Phase 3c: Model versioning"""

    def test_register_model(self):
        """Test: Register trained model"""
        registry = ModelRegistry()

        model = ModelVersion(
            version_id="v1.0-syntax",
            strategy="syntax_aware",
            training_samples=500,
            validation_accuracy=0.92,
        )

        registry.register_model(model)
        assert model.status == ModelStatus.TRAINING

    def test_promote_model(self):
        """Test: Promote model to production"""
        registry = ModelRegistry()

        model = ModelVersion("v1.1-syntax", "syntax_aware")
        registry.register_model(model)
        registry.promote_model("v1.1-syntax")

        assert model.status == ModelStatus.PRODUCTION
        assert registry.get_production_model("syntax_aware") == model

    def test_model_versions_history(self):
        """Test: Track multiple model versions"""
        registry = ModelRegistry()

        registry.register_model(ModelVersion("v1.0", "syntax_aware"))
        registry.register_model(ModelVersion("v1.1", "syntax_aware"))
        registry.register_model(ModelVersion("v1.0", "visual_aware"))

        syntax_versions = registry.get_model_versions("syntax_aware")
        assert len(syntax_versions) == 2
        # visual_aware's v1.0 did not overwrite syntax_aware's v1.0
        assert len(registry.get_model_versions("visual_aware")) == 1

    def test_same_version_id_promotes_per_strategy(self):
        registry = ModelRegistry()
        registry.register_model(ModelVersion("v1.0", "syntax_aware"))
        registry.register_model(ModelVersion("v1.0", "visual_aware"))

        import pytest
        with pytest.raises(ValueError, match="ambiguous"):
            registry.promote_model("v1.0")
        registry.promote_model("v1.0", strategy="visual_aware")
        assert registry.get_production_model("visual_aware").strategy == "visual_aware"
        assert registry.get_production_model("syntax_aware") is None


class TestABTestFramework:
    """Phase 3c: A/B testing for model promotion"""

    def test_start_ab_test(self):
        """Test: Start A/B test"""
        framework = ABTestFramework()

        config = framework.start_test(
            model_id="v1.1-syntax",
            strategy="syntax_aware",
            baseline_model_id="v1.0-syntax",
        )

        assert config["model_id"] == "v1.1-syntax"
        assert config["test_results"]["sample_size"] == 0

    def test_record_test_results(self):
        """Test: Record A/B test results"""
        framework = ABTestFramework()
        framework.start_test("v1.1", "syntax_aware", "v1.0")

        # Candidate model results
        for _ in range(50):
            framework.record_test_result("v1.1", quality_score=0.95, is_candidate=True)

        # Baseline model results
        for _ in range(50):
            framework.record_test_result("v1.1", quality_score=0.85, is_candidate=False)

        assert framework.check_test_complete("v1.1", min_samples=50)

    def test_determine_winner(self):
        """Test: Determine A/B test winner"""
        framework = ABTestFramework()
        framework.start_test("v1.1", "syntax_aware", "v1.0")

        # Candidate wins
        for _ in range(60):
            framework.record_test_result("v1.1", quality_score=0.95, is_candidate=True)

        for _ in range(60):
            framework.record_test_result("v1.1", quality_score=0.80, is_candidate=False)

        winner = framework.get_test_winner("v1.1")
        assert winner == "v1.1"  # Candidate is better


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
