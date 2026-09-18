"""
T3.1 Model Selection Skill — Complete E2E Learning Loop Test Suite

Tests all 4 phases:
1. Phase 1: Skill instantiation & execution
2. Phase 2: Console UI (feedback form + metrics)
3. Phase 3: Multi-model learning with Bayesian updates
4. Phase 4: Full convergence verification (50+ feedback samples)

ADRs: 0845 (Architecture), 0846 (Learning Loop), 0314 (Learning Infrastructure)
"""

import pytest
import json
from datetime import datetime, timezone
from core.skills.os_skills.model_selector import ModelSelector
from core.skills.os_skills.model_selector_skill_integration import ModelSelectorSkill
from core.skills.os_skills.model_selector_learning_enhancement import (
    MultiModelSelector,
    BayesianOptimizer,
    MODELS,
    MODEL_COSTS,
)


class TestPhase1SkillInstantiation:
    """Phase 1: Basic skill instantiation and execution."""

    def test_skill_instantiation(self):
        """✅ Can instantiate ModelSelectorSkill."""
        skill = ModelSelectorSkill(tenant_id="_default", variant="variant_c")
        assert skill.tenant_id == "_default"
        assert skill.variant_name == "variant_c"

    def test_skill_execution(self):
        """✅ Can execute skill and get model recommendation."""
        skill = ModelSelectorSkill(tenant_id="_default")
        decision = skill.execute(task_input="Classify this code review", task_type="code_review")

        assert decision.recommended_model in ["claude-haiku-4-5-20251001", "claude-sonnet-5", "claude-opus-5"]
        assert decision.recommended_provider is not None
        assert 0.0 <= decision.confidence <= 1.0

    def test_feedback_recording(self):
        """✅ Can record feedback without errors."""
        skill = ModelSelectorSkill(tenant_id="_default")
        decision = skill.execute(task_input="Test", task_type="test")

        # Record success feedback
        skill.record_outcome(
            model=decision.recommended_model,
            task_type="test",
            success=True,
            cost_usd=1.50
        )


class TestPhase2ConsoleUI:
    """Phase 2: Console UI integration (feedback form + metrics)."""

    def test_metrics_payload_structure(self):
        """✅ Metrics payload has required fields."""
        selector = MultiModelSelector(tenant_id="_default")
        metrics = selector.get_metrics(task_type="code_review")

        assert "timestamp" in metrics
        assert "confidence_scores" in metrics
        assert "feedback_counts" in metrics
        assert "convergence_status" in metrics

        # Check model entries
        for model in MODELS.keys():
            assert model in metrics["confidence_scores"]
            assert "confidence" in metrics["confidence_scores"][model]
            assert "success_rate" in metrics["confidence_scores"][model]

    def test_feedback_input_creates_metrics(self):
        """✅ Recording feedback updates metrics."""
        selector = MultiModelSelector(tenant_id="_default")

        # Record 5 feedback samples
        for rating in [5, 4, 5, 3, 4]:
            selector.record_feedback("sonnet", "code_review", rating)

        metrics = selector.get_metrics("code_review")
        assert metrics["confidence_scores"]["sonnet"]["samples"] == 5


class TestPhase3LearningEnhancement:
    """Phase 3: Multi-model support and Bayesian learning."""

    def test_all_models_available(self):
        """✅ All 4 models (haiku, sonnet, opus, fable) are available."""
        assert set(MODELS.keys()) == {"haiku", "sonnet", "opus", "fable"}

        for model in MODELS.keys():
            assert MODELS[model].startswith("claude-")
            assert model in MODEL_COSTS

    def test_model_selection_respects_budget(self):
        """✅ Model selection respects budget constraint."""
        optimizer = BayesianOptimizer(tenant_id="_default")

        # With low budget, should select cheap model (haiku)
        model, conf = optimizer.select_best_model("coding", budget_constraint=2.0)
        assert model == "haiku"  # Cheapest option

    def test_bayesian_confidence_updates(self):
        """✅ Confidence scores update based on feedback."""
        optimizer = BayesianOptimizer(tenant_id="_default")
        task_type = "code_review"

        # Initial confidence (uninformed prior)
        conf_initial = optimizer.get_confidence("sonnet", task_type)
        assert conf_initial.samples == 0

        # Add successful feedback
        conf_after_success = optimizer.record_feedback("sonnet", task_type, True, quality_score=5)
        assert conf_after_success.samples == 1
        assert conf_after_success.success_rate > 0.5  # Posterior updated

        # Add failure feedback
        conf_after_failure = optimizer.record_feedback("sonnet", task_type, False, quality_score=1)
        assert conf_after_failure.samples == 2
        # Success rate should decrease after failure
        assert conf_after_failure.success_rate < conf_after_success.success_rate

    def test_model_comparison_via_learning(self):
        """✅ Can compare models via learned confidence."""
        optimizer = BayesianOptimizer(tenant_id="_default")
        task_type = "code_review"

        # Train haiku with good results
        for _ in range(10):
            optimizer.record_feedback("haiku", task_type, True, quality_score=5)

        # Train sonnet with mixed results
        for _ in range(5):
            optimizer.record_feedback("sonnet", task_type, True, quality_score=5)
        for _ in range(5):
            optimizer.record_feedback("sonnet", task_type, False, quality_score=2)

        # Haiku should be selected (higher success rate)
        best_model, _ = optimizer.select_best_model(task_type)
        assert best_model == "haiku"


class TestPhase4Convergence:
    """Phase 4: Full convergence verification (50+ feedback samples)."""

    def test_convergence_after_50_samples(self):
        """✅ Learning converges after 50+ feedback samples."""
        optimizer = BayesianOptimizer(tenant_id="_default")
        task_type = "code_review"
        model = "sonnet"

        # Simulate 50 feedback samples (80% success rate)
        for i in range(50):
            success = i % 5 != 0  # 80% success (4 out of 5 are true)
            quality = 4.5 if success else 2.0
            conf = optimizer.record_feedback(model, task_type, success, quality)

        # Verify convergence
        assert conf.samples == 50
        assert conf.success_rate > 0.70  # Should be around 0.8
        assert conf.converged  # std-dev < 5% with 50 samples

    def test_feedback_loop_convergence(self):
        """✅ Full feedback loop: record → learn → select → improve."""
        selector = MultiModelSelector(tenant_id="_default")
        task_type = "translation"

        # Phase 1: Initial random selection (no learning)
        initial = selector.select_model("Translate this", task_type)
        assert initial["confidence"] < 0.9  # Low confidence initially

        # Phase 2: Collect feedback (50 samples)
        selected_models = {}
        for i in range(50):
            rating = 5 if i < 40 else 2  # 40/50 = 80% success
            model = initial["model"]
            selector.record_feedback(model, task_type, rating)
            selected_models[model] = selected_models.get(model, 0) + 1

        # Phase 3: Verify learning improved confidence
        learned = selector.select_model("Translate this", task_type)
        assert learned["confidence"] >= initial["confidence"]
        assert learned["samples"] == 50

    def test_multi_model_learning_comparison(self):
        """✅ System learns to prefer better-performing models."""
        optimizer = BayesianOptimizer(tenant_id="_default")
        task_type = "analysis"

        # Train haiku poorly (30% success)
        for i in range(30):
            success = i < 9  # 9/30 = 30% success
            optimizer.record_feedback("haiku", task_type, success, 4.0 if success else 2.0)

        # Train opus well (90% success)
        for i in range(30):
            success = i < 27  # 27/30 = 90% success
            optimizer.record_feedback("opus", task_type, success, 4.8 if success else 2.0)

        # System should strongly prefer opus
        best_model, best_conf = optimizer.select_best_model(task_type)
        assert best_model == "opus"
        assert best_conf.success_rate > 0.80

    def test_audit_trail_for_feedback_events(self):
        """✅ All feedback events are auditable."""
        optimizer = BayesianOptimizer(tenant_id="_default")
        task_type = "qa"

        # Record feedback and verify each produces an audit-ready confidence object
        for rating in [5, 4, 5, 3, 4, 5, 4, 5]:
            conf = optimizer.record_feedback("sonnet", task_type, rating >= 3, rating)

            # Verify audit structure
            audit_data = conf.to_dict()
            assert audit_data["model"] == "sonnet"
            assert audit_data["task_type"] == task_type
            assert audit_data["updated_at"] is not None
            assert isinstance(audit_data["converged"], bool)


class TestE2EIntegration:
    """End-to-end integration tests (all phases together)."""

    def test_full_pipeline_skill_to_learning(self):
        """✅ Full pipeline: Skill execution → Feedback → Learning → Routing."""
        skill = ModelSelectorSkill(tenant_id="_default", enable_learning=True)
        selector = MultiModelSelector(tenant_id="_default")

        task_type = "test_task"

        # Step 1: Execute skill
        decision = skill.execute(task_input="Test task", task_type=task_type)
        selected_model = decision.recommended_model

        # Step 2: Collect feedback (simulated operator ratings)
        feedback_ratings = [5, 4, 5, 4, 5, 3, 4, 5]  # 7/8 = 87.5% satisfied

        for rating in feedback_ratings:
            conf = selector.record_feedback(selected_model, task_type, rating)

        # Step 3: Verify learning happened
        metrics = selector.get_metrics(task_type)
        assert metrics["confidence_scores"][selected_model.split("-")[1]]["samples"] > 0

    def test_no_regressions_from_existing_tests(self):
        """✅ Existing model selector still works (backward compatibility)."""
        selector = ModelSelector()
        result, _ = selector.classify_with_decomposition_hint(
            task_input="Analyze this code",
            tenant_id="_default",
            task_type="code_review"
        )

        assert result is not None
        assert result.recommended_model in ["claude-haiku-4-5-20251001", "claude-sonnet-5", "claude-opus-5"]
        assert 0.0 <= result.confidence <= 1.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


Co-Authored-By: Claude Haiku 4.5 <noreply@anthropic.com>
