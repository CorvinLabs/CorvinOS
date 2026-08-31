"""Tests for Bayesian Template Tuning (Phase 1, Week 1-2)."""

from __future__ import annotations

import math
import pytest

from core.learning.bayesian_tuner import (
    TaskTemplate,
    TaskOutcome,
    BetaDistribution,
    GaussianDistribution,
    BayesianTemplateTuner,
    TemplateRegistry,
)


class TestBetaDistribution:
    """Test Beta distribution math."""

    def test_beta_mean(self):
        """Test: Beta mean = α/(α+β)."""
        beta = BetaDistribution(alpha=2.0, beta=2.0)
        assert beta.mean() == pytest.approx(0.5)

        beta = BetaDistribution(alpha=3.0, beta=1.0)
        assert beta.mean() == pytest.approx(0.75)

        beta = BetaDistribution(alpha=1.0, beta=3.0)
        assert beta.mean() == pytest.approx(0.25)

    def test_beta_variance(self):
        """Test: Beta variance = αβ/((α+β)²(α+β+1))."""
        beta = BetaDistribution(alpha=2.0, beta=2.0)
        expected_var = (2 * 2) / ((2 + 2) ** 2 * (2 + 2 + 1))
        assert beta.variance() == pytest.approx(expected_var)

    def test_beta_std_dev(self):
        """Test: Standard deviation is sqrt(variance)."""
        beta = BetaDistribution(alpha=2.0, beta=2.0)
        expected_std = math.sqrt(beta.variance())
        assert beta.std_dev() == pytest.approx(expected_std)


class TestGaussianDistribution:
    """Test Gaussian distribution math."""

    def test_gaussian_mean(self):
        """Test: Gaussian mean is direct property."""
        gauss = GaussianDistribution(mean=100.0, variance=25.0)
        assert gauss.mean == 100.0

    def test_gaussian_std_dev(self):
        """Test: Standard deviation is sqrt(variance)."""
        gauss = GaussianDistribution(mean=100.0, variance=25.0)
        assert gauss.std_dev() == pytest.approx(5.0)


class TestBayesianTemplateTuner:
    """Test Bayesian template tuning."""

    def test_tuner_initialization(self):
        """Test: Tuner initializes with correct priors."""
        template = TaskTemplate(
            template_id="t1",
            task_type="code_gen",
            engine="haiku",
            prompt_style="concise",
            temperature=0.7,
            max_tokens=2048,
        )

        tuner = BayesianTemplateTuner(template)

        # Check priors
        assert tuner.accuracy_prior.alpha == 2.0
        assert tuner.accuracy_prior.beta == 2.0
        assert tuner.latency_prior.mean == 100.0

        # Check posteriors start as priors
        assert tuner.accuracy_posterior.mean() == pytest.approx(0.5)
        assert tuner.latency_posterior.mean == 100.0

    def test_accuracy_update_success(self):
        """Test: Update with successful outcome increases alpha."""
        template = TaskTemplate(
            template_id="t1",
            task_type="code_gen",
            engine="haiku",
            prompt_style="concise",
            temperature=0.7,
            max_tokens=2048,
        )

        tuner = BayesianTemplateTuner(template)
        initial_alpha = tuner.accuracy_posterior.alpha

        outcome = TaskOutcome(
            task_id="task-1",
            template_id="t1",
            accuracy=0.9,  # > 0.5, counts as success
            latency_ms=50,
            cost_cents=10,
            quality_score=0.85,
        )

        tuner.update(outcome)

        # Alpha should increase
        assert tuner.accuracy_posterior.alpha == initial_alpha + 1

    def test_accuracy_update_failure(self):
        """Test: Update with failed outcome increases beta."""
        template = TaskTemplate(
            template_id="t1",
            task_type="code_gen",
            engine="haiku",
            prompt_style="concise",
            temperature=0.7,
            max_tokens=2048,
        )

        tuner = BayesianTemplateTuner(template)
        initial_beta = tuner.accuracy_posterior.beta

        outcome = TaskOutcome(
            task_id="task-1",
            template_id="t1",
            accuracy=0.3,  # < 0.5, counts as failure
            latency_ms=50,
            cost_cents=10,
            quality_score=0.25,
        )

        tuner.update(outcome)

        # Beta should increase
        assert tuner.accuracy_posterior.beta == initial_beta + 1

    def test_accuracy_converges_with_many_successes(self):
        """Test: Accuracy posterior converges after many successes."""
        template = TaskTemplate(
            template_id="t1",
            task_type="code_gen",
            engine="haiku",
            prompt_style="concise",
            temperature=0.7,
            max_tokens=2048,
        )

        tuner = BayesianTemplateTuner(template)

        # Add 50 successful outcomes
        for i in range(50):
            outcome = TaskOutcome(
                task_id=f"task-{i}",
                template_id="t1",
                accuracy=0.85 + (i * 0.001),  # > 0.5, all count as successes
                latency_ms=50 + (i % 20),
                cost_cents=10,
                quality_score=0.8,
            )
            tuner.update(outcome)

        # Posterior should have high alpha, low beta
        assert tuner.accuracy_posterior.alpha > tuner.accuracy_posterior.beta
        assert tuner.accuracy_posterior.mean() > 0.7

        # Variance should be small
        variance = tuner.accuracy_posterior.variance()
        assert variance < 0.05

    def test_latency_update(self):
        """Test: Latency posterior updates correctly."""
        template = TaskTemplate(
            template_id="t1",
            task_type="code_gen",
            engine="haiku",
            prompt_style="concise",
            temperature=0.7,
            max_tokens=2048,
        )

        tuner = BayesianTemplateTuner(template)
        initial_mean = tuner.latency_posterior.mean

        # Add observation at 50ms (lower than prior of 100ms)
        outcome = TaskOutcome(
            task_id="task-1",
            template_id="t1",
            accuracy=0.85,
            latency_ms=50,
            cost_cents=10,
            quality_score=0.8,
        )

        tuner.update(outcome)

        # Posterior mean should move towards 50ms
        new_mean = tuner.latency_posterior.mean
        assert new_mean < initial_mean
        assert new_mean > 50  # But not all the way there yet

    def test_convergence_check_requires_50_observations(self):
        """Test: Convergence requires at least 50 observations."""
        template = TaskTemplate(
            template_id="t1",
            task_type="code_gen",
            engine="haiku",
            prompt_style="concise",
            temperature=0.7,
            max_tokens=2048,
        )

        tuner = BayesianTemplateTuner(template)

        # Add 49 observations
        for i in range(49):
            outcome = TaskOutcome(
                task_id=f"task-{i}",
                template_id="t1",
                accuracy=0.85,
                latency_ms=60,
                cost_cents=10,
                quality_score=0.8,
            )
            tuner.update(outcome)

        # Should not converge
        assert tuner.convergence_check() is False

        # Add 1 more
        outcome = TaskOutcome(
            task_id="task-50",
            template_id="t1",
            accuracy=0.85,
            latency_ms=60,
            cost_cents=10,
            quality_score=0.8,
        )
        tuner.update(outcome)

        # Should converge (assuming variance is low)
        assert tuner.convergence_check() is True

    def test_convergence_check_requires_low_variance(self):
        """Test: Convergence requires low variance."""
        template = TaskTemplate(
            template_id="t1",
            task_type="code_gen",
            engine="haiku",
            prompt_style="concise",
            temperature=0.7,
            max_tokens=2048,
        )

        tuner = BayesianTemplateTuner(template)

        # Add 50 mixed-accuracy observations (high variance)
        for i in range(50):
            # Alternating high and low accuracy
            accuracy = 0.9 if i % 2 == 0 else 0.1
            outcome = TaskOutcome(
                task_id=f"task-{i}",
                template_id="t1",
                accuracy=accuracy,
                latency_ms=60,
                cost_cents=10,
                quality_score=accuracy,
            )
            tuner.update(outcome)

        # Should not converge (high variance due to mixed outcomes)
        assert tuner.convergence_check() is False

    def test_confidence_interval(self):
        """Test: Confidence interval is computed correctly."""
        template = TaskTemplate(
            template_id="t1",
            task_type="code_gen",
            engine="haiku",
            prompt_style="concise",
            temperature=0.7,
            max_tokens=2048,
        )

        tuner = BayesianTemplateTuner(template)

        # Add observations
        for i in range(100):
            outcome = TaskOutcome(
                task_id=f"task-{i}",
                template_id="t1",
                accuracy=0.8 + (i * 0.001),
                latency_ms=50,
                cost_cents=10,
                quality_score=0.8,
            )
            tuner.update(outcome)

        ci = tuner.get_confidence_interval(percentile=0.95)

        assert "mean" in ci
        assert "lower" in ci
        assert "upper" in ci
        assert ci["lower"] < ci["mean"] < ci["upper"]
        assert ci["lower"] >= 0.0
        assert ci["upper"] <= 1.0

    def test_recommend_action_adopt(self):
        """Test: Recommend 'adopt' for converged, accurate templates."""
        template = TaskTemplate(
            template_id="t1",
            task_type="code_gen",
            engine="haiku",
            prompt_style="concise",
            temperature=0.7,
            max_tokens=2048,
        )

        tuner = BayesianTemplateTuner(template)

        # Add 60 high-accuracy outcomes
        for i in range(60):
            outcome = TaskOutcome(
                task_id=f"task-{i}",
                template_id="t1",
                accuracy=0.85 + (i * 0.0005),
                latency_ms=50,
                cost_cents=10,
                quality_score=0.85,
            )
            tuner.update(outcome)

        recommendation = tuner.recommend_action()
        assert recommendation == "adopt"

    def test_recommend_action_iterate(self):
        """Test: Recommend 'iterate' for improving templates."""
        template = TaskTemplate(
            template_id="t1",
            task_type="code_gen",
            engine="haiku",
            prompt_style="concise",
            temperature=0.7,
            max_tokens=2048,
        )

        tuner = BayesianTemplateTuner(template)

        # Add 30 moderate-accuracy outcomes
        for i in range(30):
            outcome = TaskOutcome(
                task_id=f"task-{i}",
                template_id="t1",
                accuracy=0.7 + (i * 0.001),
                latency_ms=50,
                cost_cents=10,
                quality_score=0.7,
            )
            tuner.update(outcome)

        recommendation = tuner.recommend_action()
        assert recommendation == "iterate"

    def test_recommend_action_reject(self):
        """Test: Recommend 'reject' for low-accuracy templates."""
        template = TaskTemplate(
            template_id="t1",
            task_type="code_gen",
            engine="haiku",
            prompt_style="concise",
            temperature=0.7,
            max_tokens=2048,
        )

        tuner = BayesianTemplateTuner(template)

        # Add 30 low-accuracy outcomes
        for i in range(30):
            outcome = TaskOutcome(
                task_id=f"task-{i}",
                template_id="t1",
                accuracy=0.3 + (i * 0.001),
                latency_ms=50,
                cost_cents=10,
                quality_score=0.3,
            )
            tuner.update(outcome)

        recommendation = tuner.recommend_action()
        assert recommendation == "reject"


class TestTemplateRegistry:
    """Test template registry."""

    def test_registry_register_template(self):
        """Test: Registry registers templates."""
        registry = TemplateRegistry()

        template = TaskTemplate(
            template_id="t1",
            task_type="code_gen",
            engine="haiku",
            prompt_style="concise",
            temperature=0.7,
            max_tokens=2048,
        )

        tuner = registry.register_template(template)
        assert "t1" in registry.tuners
        assert registry.get_tuner("t1") == tuner

    def test_registry_update_outcome(self):
        """Test: Registry updates templates with outcomes."""
        registry = TemplateRegistry()

        template = TaskTemplate(
            template_id="t1",
            task_type="code_gen",
            engine="haiku",
            prompt_style="concise",
            temperature=0.7,
            max_tokens=2048,
        )

        registry.register_template(template)

        outcome = TaskOutcome(
            task_id="task-1",
            template_id="t1",
            accuracy=0.85,
            latency_ms=50,
            cost_cents=10,
            quality_score=0.8,
        )

        registry.update_outcome(outcome)

        assert len(registry.get_tuner("t1").outcomes) == 1

    def test_registry_get_converged_templates(self):
        """Test: Registry identifies converged templates."""
        registry = TemplateRegistry()

        template1 = TaskTemplate(
            template_id="t1",
            task_type="code_gen",
            engine="haiku",
            prompt_style="concise",
            temperature=0.7,
            max_tokens=2048,
        )

        template2 = TaskTemplate(
            template_id="t2",
            task_type="analysis",
            engine="haiku",
            prompt_style="verbose",
            temperature=0.5,
            max_tokens=4096,
        )

        registry.register_template(template1)
        registry.register_template(template2)

        # Add many outcomes to t1
        for i in range(60):
            outcome = TaskOutcome(
                task_id=f"task-1-{i}",
                template_id="t1",
                accuracy=0.85,
                latency_ms=50,
                cost_cents=10,
                quality_score=0.8,
            )
            registry.update_outcome(outcome)

        # Add few outcomes to t2
        for i in range(10):
            outcome = TaskOutcome(
                task_id=f"task-2-{i}",
                template_id="t2",
                accuracy=0.75,
                latency_ms=60,
                cost_cents=15,
                quality_score=0.7,
            )
            registry.update_outcome(outcome)

        converged = registry.get_converged_templates()
        assert "t1" in converged
        assert "t2" not in converged

    def test_registry_get_stats(self):
        """Test: Registry computes statistics."""
        registry = TemplateRegistry()

        template1 = TaskTemplate(
            template_id="t1",
            task_type="code_gen",
            engine="haiku",
            prompt_style="concise",
            temperature=0.7,
            max_tokens=2048,
        )

        template2 = TaskTemplate(
            template_id="t2",
            task_type="analysis",
            engine="haiku",
            prompt_style="verbose",
            temperature=0.5,
            max_tokens=4096,
        )

        registry.register_template(template1)
        registry.register_template(template2)

        # Add outcomes
        for i in range(60):
            outcome = TaskOutcome(
                task_id=f"task-1-{i}",
                template_id="t1",
                accuracy=0.85,
                latency_ms=50,
                cost_cents=10,
                quality_score=0.8,
            )
            registry.update_outcome(outcome)

        stats = registry.get_stats()
        assert stats["total_templates"] == 2
        assert stats["total_outcomes"] == 60
        assert stats["converged_templates"] >= 0
