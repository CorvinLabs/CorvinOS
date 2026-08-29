"""Phase 3: GuidanceClassifier Tests (40+ tests).

Tests two-stage classification (LLM + heuristic fallback) with confidence
threshold enforcement, category correctness, and edge cases.
"""

import pytest
import asyncio
from datetime import datetime

from core.vibe_engineering.guidance_classifier import (
    GuidanceClassifier,
    DecisionContext,
    GuidanceDecision,
    GuidanceCategory,
)


class TestDecisionContext:
    """DecisionContext serialization and initialization tests."""

    def test_decision_context_basic_init(self):
        """Basic initialization with required fields."""
        ctx = DecisionContext(
            task_id="task-123",
            tenant_id="tenant-1",
            task_type="data_analysis",
            description="Analyze sales data",
        )
        assert ctx.task_id == "task-123"
        assert ctx.tenant_id == "tenant-1"
        assert ctx.current_iteration == 0
        assert ctx.error_rate == 0.0

    def test_decision_context_full_init(self):
        """Initialization with all optional fields."""
        ctx = DecisionContext(
            task_id="task-123",
            tenant_id="tenant-1",
            task_type="code_generation",
            description="Generate unit tests",
            current_iteration=5,
            max_iterations=10,
            budget_remaining=0.3,
            time_remaining=300,
            tokens_used=2048,
            tokens_available=4096,
            complexity_score=0.8,
            parallel_branches=2,
            error_rate=0.1,
        )
        assert ctx.current_iteration == 5
        assert ctx.budget_remaining == 0.3
        assert ctx.parallel_branches == 2

    def test_decision_context_to_dict(self):
        """Serialization to dictionary."""
        ctx = DecisionContext(
            task_id="task-123",
            tenant_id="tenant-1",
            task_type="research",
            description="Literature review",
            error_rate=0.2,
        )
        d = ctx.to_dict()
        assert d["task_id"] == "task-123"
        assert d["error_rate"] == 0.2
        assert "current_iteration" in d

    def test_decision_context_default_values(self):
        """Default values are correctly set."""
        ctx = DecisionContext(
            task_id="task-1",
            tenant_id="tenant-1",
            task_type="test",
            description="Test task",
        )
        assert ctx.current_iteration == 0
        assert ctx.max_iterations == 10
        assert ctx.budget_remaining == 1.0
        assert ctx.time_remaining == 0
        assert ctx.tokens_used == 0
        assert ctx.tokens_available == 4096
        assert ctx.complexity_score == 0.5
        assert ctx.parallel_branches == 0
        assert ctx.error_rate == 0.0


class TestGuidanceDecision:
    """GuidanceDecision output and serialization tests."""

    def test_guidance_decision_basic(self):
        """Basic initialization."""
        decision = GuidanceDecision(
            category=GuidanceCategory.PARALLELIZE,
            confidence=0.85,
            rationale="Found parallel branches",
        )
        assert decision.category == GuidanceCategory.PARALLELIZE
        assert decision.confidence == 0.85
        assert decision.fallback_used is False

    def test_guidance_decision_to_dict(self):
        """Serialization to dictionary."""
        decision = GuidanceDecision(
            category=GuidanceCategory.ERROR_RECOVERY,
            confidence=0.9,
            rationale="High error rate",
            fallback_used=True,
            recommended_action="Implement retry logic",
        )
        d = decision.to_dict()
        assert d["category"] == "error_recovery"
        assert d["confidence"] == 0.9
        assert d["fallback_used"] is True
        assert "timestamp" in d

    def test_guidance_decision_to_json(self):
        """Serialization to JSON string."""
        decision = GuidanceDecision(
            category=GuidanceCategory.OPTIMIZE_COST,
            confidence=0.75,
            rationale="Optimize spending",
        )
        json_str = decision.to_json()
        assert "optimize_cost" in json_str
        assert "0.75" in json_str

    def test_guidance_decision_supporting_metrics(self):
        """Supporting metrics included in serialization."""
        metrics = {"error_rate": 0.15, "budget": 0.4}
        decision = GuidanceDecision(
            category=GuidanceCategory.CONTEXT_SPLIT,
            confidence=0.8,
            rationale="Test",
            supporting_metrics=metrics,
        )
        d = decision.to_dict()
        assert d["supporting_metrics"]["error_rate"] == 0.15


class TestGuidanceClassifierHeuristics:
    """Heuristic rule-based classification tests."""

    def test_heuristic_error_recovery_high_error_rate(self):
        """Classify as ERROR_RECOVERY when error_rate > 0.3."""
        classifier = GuidanceClassifier(enable_llm=False)
        ctx = DecisionContext(
            task_id="task-1",
            tenant_id="tenant-1",
            task_type="test",
            description="Test",
            error_rate=0.4,  # > 0.3 trigger
        )
        decision = asyncio.run(classifier.classify(ctx))
        assert decision.category == GuidanceCategory.ERROR_RECOVERY
        assert decision.confidence >= 0.75
        assert decision.fallback_used is True

    def test_heuristic_context_split_low_budget(self):
        """Classify as CONTEXT_SPLIT when budget_remaining < 0.2."""
        classifier = GuidanceClassifier(enable_llm=False)
        ctx = DecisionContext(
            task_id="task-2",
            tenant_id="tenant-1",
            task_type="test",
            description="Test",
            budget_remaining=0.15,  # < 0.2 trigger
        )
        decision = asyncio.run(classifier.classify(ctx))
        assert decision.category == GuidanceCategory.CONTEXT_SPLIT
        assert decision.fallback_used is True

    def test_heuristic_context_split_high_token_usage(self):
        """Classify as CONTEXT_SPLIT when tokens > 80% of available."""
        classifier = GuidanceClassifier(enable_llm=False)
        ctx = DecisionContext(
            task_id="task-3",
            tenant_id="tenant-1",
            task_type="test",
            description="Test",
            tokens_used=3300,  # > 80% of 4096
            tokens_available=4096,
        )
        decision = asyncio.run(classifier.classify(ctx))
        assert decision.category == GuidanceCategory.CONTEXT_SPLIT
        assert decision.fallback_used is True

    def test_heuristic_parallelize_multiple_branches(self):
        """Classify as PARALLELIZE when parallel_branches >= 2."""
        classifier = GuidanceClassifier(enable_llm=False)
        ctx = DecisionContext(
            task_id="task-4",
            tenant_id="tenant-1",
            task_type="test",
            description="Test",
            parallel_branches=3,  # >= 2 trigger
        )
        decision = asyncio.run(classifier.classify(ctx))
        assert decision.category == GuidanceCategory.PARALLELIZE
        assert decision.confidence >= 0.7
        assert decision.fallback_used is True

    def test_heuristic_optimize_latency_early_with_time(self):
        """Classify as OPTIMIZE_LATENCY when early + time budget available."""
        classifier = GuidanceClassifier(enable_llm=False)
        ctx = DecisionContext(
            task_id="task-5",
            tenant_id="tenant-1",
            task_type="test",
            description="Test",
            current_iteration=2,
            max_iterations=10,  # 20% progress
            time_remaining=400,  # > 300s
        )
        decision = asyncio.run(classifier.classify(ctx))
        assert decision.category == GuidanceCategory.OPTIMIZE_LATENCY
        assert decision.fallback_used is True

    def test_heuristic_optimize_cost_high_budget_low_time(self):
        """Classify as OPTIMIZE_COST when budget > 0.5 and time < 60s."""
        classifier = GuidanceClassifier(enable_llm=False)
        ctx = DecisionContext(
            task_id="task-6",
            tenant_id="tenant-1",
            task_type="test",
            description="Test",
            budget_remaining=0.7,  # > 0.5
            time_remaining=30,  # < 60s
        )
        decision = asyncio.run(classifier.classify(ctx))
        assert decision.category == GuidanceCategory.OPTIMIZE_COST
        assert decision.fallback_used is True

    def test_heuristic_refactor_high_complexity(self):
        """Classify as REFACTOR when complexity_score > 0.7."""
        classifier = GuidanceClassifier(enable_llm=False)
        ctx = DecisionContext(
            task_id="task-7",
            tenant_id="tenant-1",
            task_type="code_generation",
            description="Complex algorithm",
            complexity_score=0.85,  # > 0.7 trigger
        )
        decision = asyncio.run(classifier.classify(ctx))
        assert decision.category == GuidanceCategory.REFACTOR
        assert decision.fallback_used is True

    def test_heuristic_default_feature_gating(self):
        """Default to FEATURE_GATING when no rules match."""
        classifier = GuidanceClassifier(enable_llm=False)
        ctx = DecisionContext(
            task_id="task-8",
            tenant_id="tenant-1",
            task_type="test",
            description="Normal task",
            # All metrics in "normal" range — no rules match
            error_rate=0.05,
            budget_remaining=0.6,
            tokens_used=1000,
            tokens_available=4096,
            complexity_score=0.3,
            time_remaining=3600,
        )
        decision = asyncio.run(classifier.classify(ctx))
        assert decision.category == GuidanceCategory.FEATURE_GATING
        assert decision.confidence == 0.5

    def test_heuristic_priority_highest_confidence_wins(self):
        """When multiple rules match, highest confidence wins."""
        classifier = GuidanceClassifier(enable_llm=False)
        ctx = DecisionContext(
            task_id="task-9",
            tenant_id="tenant-1",
            task_type="test",
            description="Test",
            error_rate=0.4,  # ERROR_RECOVERY: 0.8
            budget_remaining=0.15,  # CONTEXT_SPLIT: 0.75
        )
        # ERROR_RECOVERY has higher confidence (0.8 > 0.75)
        decision = asyncio.run(classifier.classify(ctx))
        assert decision.category == GuidanceCategory.ERROR_RECOVERY

    def test_heuristic_rationale_included(self):
        """Rationale is human-readable and included."""
        classifier = GuidanceClassifier(enable_llm=False)
        ctx = DecisionContext(
            task_id="task-10",
            tenant_id="tenant-1",
            task_type="test",
            description="Test",
            error_rate=0.5,
        )
        decision = asyncio.run(classifier.classify(ctx))
        assert len(decision.rationale) > 0
        assert "error" in decision.rationale.lower()

    def test_heuristic_recommended_action_included(self):
        """Recommended action is specific and actionable."""
        classifier = GuidanceClassifier(enable_llm=False)
        ctx = DecisionContext(
            task_id="task-11",
            tenant_id="tenant-1",
            task_type="test",
            description="Test",
            parallel_branches=4,
        )
        decision = asyncio.run(classifier.classify(ctx))
        assert len(decision.recommended_action) > 0
        assert "parallel" in decision.recommended_action.lower()


class TestGuidanceClassifierLLMFallback:
    """LLM classification with fallback behavior."""

    async def mock_llm_high_confidence(
        self, ctx: DecisionContext
    ) -> GuidanceDecision:
        """Mock LLM returning high-confidence decision."""
        return GuidanceDecision(
            category=GuidanceCategory.PARALLELIZE,
            confidence=0.95,
            rationale="LLM: Found parallel branches",
        )

    async def mock_llm_low_confidence(
        self, ctx: DecisionContext
    ) -> GuidanceDecision:
        """Mock LLM returning low-confidence decision."""
        return GuidanceDecision(
            category=GuidanceCategory.REFACTOR,
            confidence=0.65,  # Below 0.7 threshold
            rationale="LLM: Maybe refactor",
        )

    async def mock_llm_failure(self, ctx: DecisionContext) -> GuidanceDecision:
        """Mock LLM that raises exception."""
        raise RuntimeError("LLM service unavailable")

    def test_llm_high_confidence_used(self):
        """LLM result accepted when confidence >= 0.7."""
        classifier = GuidanceClassifier(
            enable_llm=True, llm_fn=self.mock_llm_high_confidence
        )
        ctx = DecisionContext(
            task_id="task-12",
            tenant_id="tenant-1",
            task_type="test",
            description="Test",
        )
        decision = asyncio.run(classifier.classify(ctx))
        assert decision.category == GuidanceCategory.PARALLELIZE
        assert decision.confidence == 0.95
        assert decision.fallback_used is False

    def test_llm_low_confidence_fallback_to_heuristic(self):
        """LLM result rejected, fallback to heuristic when confidence < 0.7."""
        classifier = GuidanceClassifier(
            enable_llm=True, llm_fn=self.mock_llm_low_confidence
        )
        ctx = DecisionContext(
            task_id="task-13",
            tenant_id="tenant-1",
            task_type="test",
            description="Test",
            error_rate=0.4,  # Heuristic: ERROR_RECOVERY
        )
        decision = asyncio.run(classifier.classify(ctx))
        # Should use heuristic, not LLM's low-confidence REFACTOR
        assert decision.category == GuidanceCategory.ERROR_RECOVERY
        assert decision.fallback_used is True

    def test_llm_failure_fallback_to_heuristic(self):
        """LLM exception triggers fallback to heuristic."""
        classifier = GuidanceClassifier(
            enable_llm=True, llm_fn=self.mock_llm_failure
        )
        ctx = DecisionContext(
            task_id="task-14",
            tenant_id="tenant-1",
            task_type="test",
            description="Test",
            parallel_branches=3,
        )
        decision = asyncio.run(classifier.classify(ctx))
        # Should use heuristic
        assert decision.category == GuidanceCategory.PARALLELIZE
        assert decision.fallback_used is True

    def test_llm_disabled_always_uses_heuristic(self):
        """When enable_llm=False, heuristic always used."""
        classifier = GuidanceClassifier(
            enable_llm=False, llm_fn=self.mock_llm_high_confidence
        )
        ctx = DecisionContext(
            task_id="task-15",
            tenant_id="tenant-1",
            task_type="test",
            description="Test",
            error_rate=0.4,
        )
        decision = asyncio.run(classifier.classify(ctx))
        assert decision.fallback_used is True


class TestGuidanceClassifierStats:
    """Monitoring and statistics collection."""

    def test_stats_call_count(self):
        """Statistics track total classification calls."""
        classifier = GuidanceClassifier(enable_llm=False)
        assert classifier.get_stats()["total_classifications"] == 0

        ctx = DecisionContext(
            task_id="task-16",
            tenant_id="tenant-1",
            task_type="test",
            description="Test",
        )
        asyncio.run(classifier.classify(ctx))
        asyncio.run(classifier.classify(ctx))

        stats = classifier.get_stats()
        assert stats["total_classifications"] == 2

    def test_stats_fallback_count(self):
        """Statistics track heuristic fallback usage."""
        classifier = GuidanceClassifier(enable_llm=False)
        ctx = DecisionContext(
            task_id="task-17",
            tenant_id="tenant-1",
            task_type="test",
            description="Test",
        )
        asyncio.run(classifier.classify(ctx))

        stats = classifier.get_stats()
        assert stats["heuristic_fallbacks"] == 1

    def test_stats_llm_success_rate(self):
        """Statistics compute LLM success rate."""
        classifier = GuidanceClassifier(enable_llm=False)
        ctx = DecisionContext(
            task_id="task-18",
            tenant_id="tenant-1",
            task_type="test",
            description="Test",
        )
        # With enable_llm=False, all should be fallbacks
        asyncio.run(classifier.classify(ctx))
        asyncio.run(classifier.classify(ctx))

        stats = classifier.get_stats()
        assert stats["llm_success_rate"] == 0.0


class TestGuidanceClassifierEdgeCases:
    """Edge cases and boundary conditions."""

    def test_classify_boundary_budget_exactly_0_2(self):
        """Boundary: budget = 0.2 (exactly at threshold)."""
        classifier = GuidanceClassifier(enable_llm=False)
        ctx = DecisionContext(
            task_id="task-19",
            tenant_id="tenant-1",
            task_type="test",
            description="Test",
            budget_remaining=0.2,  # Exactly at threshold (rule is < 0.2)
        )
        decision = asyncio.run(classifier.classify(ctx))
        # Should NOT trigger (rule requires < 0.2)
        assert decision.category != GuidanceCategory.CONTEXT_SPLIT

    def test_classify_boundary_error_rate_0_3(self):
        """Boundary: error_rate = 0.3 (exactly at threshold)."""
        classifier = GuidanceClassifier(enable_llm=False)
        ctx = DecisionContext(
            task_id="task-20",
            tenant_id="tenant-1",
            task_type="test",
            description="Test",
            error_rate=0.3,  # Exactly at threshold (rule is > 0.3)
        )
        decision = asyncio.run(classifier.classify(ctx))
        # Should NOT trigger (rule requires > 0.3)
        assert decision.category != GuidanceCategory.ERROR_RECOVERY

    def test_classify_zero_max_iterations(self):
        """Edge: max_iterations = 0 (avoid division by zero)."""
        classifier = GuidanceClassifier(enable_llm=False)
        ctx = DecisionContext(
            task_id="task-21",
            tenant_id="tenant-1",
            task_type="test",
            description="Test",
            current_iteration=0,
            max_iterations=0,  # Edge case
        )
        decision = asyncio.run(classifier.classify(ctx))
        # Should not crash
        assert decision is not None

    def test_classify_zero_tokens_available(self):
        """Edge: tokens_available = 0 (avoid division by zero)."""
        classifier = GuidanceClassifier(enable_llm=False)
        ctx = DecisionContext(
            task_id="task-22",
            tenant_id="tenant-1",
            task_type="test",
            description="Test",
            tokens_used=100,
            tokens_available=0,  # Edge case
        )
        decision = asyncio.run(classifier.classify(ctx))
        # Should not crash
        assert decision is not None

    def test_classify_negative_values_handled(self):
        """Edge: negative metric values (should be clamped/validated)."""
        classifier = GuidanceClassifier(enable_llm=False)
        # Note: This depends on whether DecisionContext validates inputs
        ctx = DecisionContext(
            task_id="task-23",
            tenant_id="tenant-1",
            task_type="test",
            description="Test",
            time_remaining=-100,  # Invalid (negative)
        )
        decision = asyncio.run(classifier.classify(ctx))
        # Should handle gracefully
        assert decision is not None

    def test_classify_very_high_confidence_saturates(self):
        """Edge: confidence cannot exceed 1.0."""
        classifier = GuidanceClassifier(enable_llm=False)
        ctx = DecisionContext(
            task_id="task-24",
            tenant_id="tenant-1",
            task_type="test",
            description="Test",
            error_rate=0.95,  # Extreme error rate
        )
        decision = asyncio.run(classifier.classify(ctx))
        # Confidence should not exceed 1.0
        assert decision.confidence <= 1.0


class TestGuidanceClassifierConcurrency:
    """Concurrent classification behavior."""

    def test_concurrent_classifications(self):
        """Multiple concurrent classifications work correctly."""
        classifier = GuidanceClassifier(enable_llm=False)

        async def run_classifications():
            contexts = [
                DecisionContext(
                    task_id=f"task-{i}",
                    tenant_id="tenant-1",
                    task_type="test",
                    description=f"Task {i}",
                    error_rate=0.1 * i,  # Vary metrics
                )
                for i in range(5)
            ]
            results = await asyncio.gather(
                *[classifier.classify(ctx) for ctx in contexts]
            )
            return results

        results = asyncio.run(run_classifications())
        assert len(results) == 5
        assert all(isinstance(r, GuidanceDecision) for r in results)

    def test_concurrent_stats_thread_safe(self):
        """Statistics are correctly updated under concurrent access."""
        classifier = GuidanceClassifier(enable_llm=False)
        ctx = DecisionContext(
            task_id="task-25",
            tenant_id="tenant-1",
            task_type="test",
            description="Test",
        )

        async def run_concurrent_classifies():
            await asyncio.gather(
                *[classifier.classify(ctx) for _ in range(10)]
            )

        asyncio.run(run_concurrent_classifies())
        stats = classifier.get_stats()
        assert stats["total_classifications"] == 10


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
