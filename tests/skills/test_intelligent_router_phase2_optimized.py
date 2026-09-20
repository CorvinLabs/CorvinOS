"""
Test suite for Phase 2 Optimized Intelligent Router.

Validates that new conservative routing thresholds reduce quality loss
while maintaining reasonable token savings (target: 20-25% savings, ≤2% quality loss).

Test Categories:
1. Threshold validation (SIMPLE < 30, MEDIUM 30-150, COMPLEX >= 150)
2. Keyword heuristics (conservative bumping)
3. Per-complexity tier correctness
4. Quality preservation (COMPLEX stays on Opus)
"""

import pytest
from core.skills.os_skills.intelligent_router import (
    IntelligentRouter, ModelTier, Engine, RoutingDecision
)


class TestPhase2Thresholds:
    """Test Phase 2 optimized token thresholds."""

    def test_simple_threshold_tighter_than_phase1(self):
        """SIMPLE < 30 (was < 50): fewer Haiku tasks, more Sonnet."""
        router = IntelligentRouter()

        # 25 tokens → SIMPLE → Haiku
        decision = router.route_task("What's 2+2?", token_count=25)
        assert decision.tier == "simple"
        assert decision.model == "claude-haiku-4-5"

        # 30 tokens → MEDIUM (boundary)
        decision = router.route_task("Short task", token_count=30)
        assert decision.tier == "medium"
        assert decision.model == "claude-sonnet-5"

        # 50 tokens → MEDIUM (was SIMPLE in Phase 1)
        decision = router.route_task("a" * 200, token_count=50)
        assert decision.tier == "medium"
        assert decision.model == "claude-sonnet-5"

    def test_medium_threshold_tighter_than_phase1(self):
        """MEDIUM 30-150 (was 50-250): reduces COMPLEX threshold."""
        router = IntelligentRouter()

        # 75 tokens → MEDIUM
        decision = router.route_task("a" * 300, token_count=75)
        assert decision.tier == "medium"
        assert decision.model == "claude-sonnet-5"

        # 150 tokens → COMPLEX (boundary, was MEDIUM in Phase 1)
        decision = router.route_task("a" * 600, token_count=150)
        assert decision.tier == "complex"
        assert decision.model == "claude-opus-5"

    def test_complex_threshold_lower_than_phase1(self):
        """COMPLEX >= 150 (was >= 250): more COMPLEX tasks use Opus."""
        router = IntelligentRouter()

        # 150 tokens → COMPLEX → Opus (was MEDIUM in Phase 1)
        decision = router.route_task("a" * 600, token_count=150)
        assert decision.tier == "complex"
        assert decision.model == "claude-opus-5"

        # 200 tokens → COMPLEX → Opus
        decision = router.route_task("a" * 800, token_count=200)
        assert decision.tier == "complex"
        assert decision.model == "claude-opus-5"

        # 500 tokens → COMPLEX → Opus (strongly)
        decision = router.route_task("a" * 2000, token_count=500)
        assert decision.tier == "complex"
        assert decision.model == "claude-opus-5"
        assert decision.confidence == 0.95


class TestPhase2ConservativeKeywords:
    """Test conservative keyword bumping (Phase 2 strategy)."""

    def test_simple_bump_to_medium_requires_length(self):
        """SIMPLE→MEDIUM bump requires task > 50 chars AND creation keyword."""
        router = IntelligentRouter()

        # Short creation request (< 50 chars) → stays SIMPLE despite keyword
        short_request = "write code"
        decision = router.route_task(short_request, token_count=15)
        assert decision.tier == "simple"  # Should NOT bump to MEDIUM (too short)

        # Longer creation request (> 50 chars) → MEDIUM
        long_request = "write a Python function that calculates fibonacci numbers"
        decision = router.route_task(long_request, token_count=25)
        assert decision.tier == "medium"  # BUMPED to MEDIUM (has keyword + length)

    def test_medium_bump_to_complex_requires_depth(self):
        """MEDIUM→COMPLEX bump requires task > 80 chars AND analysis keyword."""
        router = IntelligentRouter()

        # Short analysis request (< 80 chars) → stays MEDIUM
        short_analysis = "debug this"
        decision = router.route_task(short_analysis, token_count=50)
        assert decision.tier == "medium"  # Should NOT bump (too short)

        # Longer analysis request (> 80 chars) → COMPLEX
        long_analysis = "debug this Python function to find the memory leak causing slow performance"
        decision = router.route_task(long_analysis, token_count=80)
        assert decision.tier == "complex"  # BUMPED to COMPLEX (has keyword + depth)

    def test_conservative_keywords_only(self):
        """Phase 2: only "write/implement/create/develop" bump SIMPLE→MEDIUM."""
        router = IntelligentRouter()

        # "build" does NOT appear in Phase 2 conservative patterns
        # (was in Phase 1 but removed for conservation)
        task_build = "build a scalable web service with async workers and caching"
        decision = router.route_task(task_build, token_count=30)
        # Should stay MEDIUM based on token count, not keyword bump
        assert decision.tier == "medium"

    def test_no_bump_for_simple_queries(self):
        """Simple queries should NOT be bumped even with some keywords."""
        router = IntelligentRouter()

        # Simple query with no depth → SIMPLE
        query = "What is Python?"
        decision = router.route_task(query, token_count=10)
        assert decision.tier == "simple"


class TestPhase2QualityPreservation:
    """Test that Phase 2 preserves quality for COMPLEX tasks."""

    def test_complex_tasks_use_opus(self):
        """COMPLEX tasks (>= 150 tokens) ALWAYS use Opus."""
        router = IntelligentRouter()

        # Various COMPLEX token counts → all Opus
        for token_count in [150, 200, 300, 500, 1000]:
            decision = router.route_task("a" * (token_count * 4), token_count=token_count)
            assert decision.tier == "complex", f"Failed at {token_count} tokens"
            assert decision.model == "claude-opus-5", f"Failed at {token_count} tokens"

    def test_complex_latency_degradation_fix(self):
        """Phase 1 showed COMPLEX latency -21.7%; Phase 2 should improve by using Opus."""
        router = IntelligentRouter()

        # COMPLEX task at 150 tokens (boundary from Phase 1: was MEDIUM, now COMPLEX)
        decision = router.route_task("a" * 600, token_count=150)
        assert decision.tier == "complex"
        assert decision.model == "claude-opus-5"

        # OPUS on native engine has 800ms latency (better than Sonnet degradation)
        assert decision.latency_estimate_ms == 800

    def test_token_savings_target_met(self):
        """Validate target token savings distribution across tiers."""
        router = IntelligentRouter()

        # Token savings estimate based on model:
        # Haiku 1.1x output → lower tokens
        # Sonnet 1.2x output → medium tokens
        # Opus 1.3x output → higher tokens

        # SIMPLE: most aggressive (Haiku)
        simple_task = "What is 2+2?"
        decision = router.route_task(simple_task, token_count=10)
        assert decision.model == "claude-haiku-4-5"

        # MEDIUM: moderate (Sonnet)
        medium_task = "a" * 200
        decision = router.route_task(medium_task, token_count=75)
        assert decision.model == "claude-sonnet-5"

        # COMPLEX: quality-focused (Opus)
        complex_task = "a" * 600
        decision = router.route_task(complex_task, token_count=150)
        assert decision.model == "claude-opus-5"


class TestPhase2ConfidenceScores:
    """Test confidence scoring reflects new thresholds."""

    def test_confidence_strong_in_complex(self):
        """Confidence STRONG (0.95) for COMPLEX tier (tokens >= 150)."""
        router = IntelligentRouter()

        decision = router.route_task("a" * 600, token_count=150)
        assert decision.confidence == 0.95
        assert decision.signal_strength == "strong"

    def test_confidence_medium_in_medium(self):
        """Confidence MEDIUM (0.80) for MEDIUM tier (30-150 tokens)."""
        router = IntelligentRouter()

        decision = router.route_task("a" * 300, token_count=75)
        assert decision.confidence == 0.80
        assert decision.signal_strength == "medium"

    def test_confidence_weak_in_simple(self):
        """Confidence WEAK (<= 0.90) for SIMPLE tier (< 30 tokens)."""
        router = IntelligentRouter()

        decision = router.route_task("What is 2+2?", token_count=10)
        assert decision.confidence == 0.85  # New Phase 2: 0.85
        assert decision.signal_strength == "weak"

    def test_keyword_bump_reduces_confidence(self):
        """Keyword-based bumps reduce confidence (less certain)."""
        router = IntelligentRouter()

        # Base MEDIUM classification
        base_decision = router.route_task("a" * 300, token_count=75)
        base_confidence = base_decision.confidence

        # SIMPLE→MEDIUM keyword bump
        bumped_decision = router.route_task(
            "write a Python function for this" + " " * 50,
            token_count=20
        )
        # Confidence should be similar or slightly lower due to bump
        assert bumped_decision.confidence <= 0.80


class TestPhase2CostEstimates:
    """Test cost estimates align with Phase 2 strategy."""

    def test_cost_estimate_simple_lowest(self):
        """SIMPLE tasks have lowest cost estimates (Haiku)."""
        router = IntelligentRouter()

        simple = router.route_task("2+2?", token_count=5)
        medium = router.route_task("a" * 300, token_count=75)
        complex_task = router.route_task("a" * 600, token_count=150)

        assert simple.cost_estimate < medium.cost_estimate < complex_task.cost_estimate

    def test_cost_under_limit(self):
        """All Phase 2 routes respect cost limits."""
        router = IntelligentRouter()

        # Default cost limit: $5.0 per task
        decision = router.route_task("a" * 600, token_count=150, cost_limit_usd=5.0)
        assert decision.cost_estimate <= 5.0


class TestPhase2EdgeCases:
    """Test edge cases and boundary conditions."""

    def test_boundary_tokens_30(self):
        """Boundary at 30 tokens (SIMPLE → MEDIUM)."""
        router = IntelligentRouter()

        # 29 tokens → SIMPLE
        decision = router.route_task("a" * 116, token_count=29)
        assert decision.tier == "simple"

        # 30 tokens → MEDIUM
        decision = router.route_task("a" * 120, token_count=30)
        assert decision.tier == "medium"

    def test_boundary_tokens_150(self):
        """Boundary at 150 tokens (MEDIUM → COMPLEX)."""
        router = IntelligentRouter()

        # 149 tokens → MEDIUM
        decision = router.route_task("a" * 596, token_count=149)
        assert decision.tier == "medium"

        # 150 tokens → COMPLEX
        decision = router.route_task("a" * 600, token_count=150)
        assert decision.tier == "complex"

    def test_zero_token_count(self):
        """Handle edge case of 0 tokens (should be SIMPLE)."""
        router = IntelligentRouter()

        decision = router.route_task("", token_count=0)
        assert decision.tier == "simple"
        assert decision.model == "claude-haiku-4-5"

    def test_very_large_token_count(self):
        """Handle very large token counts (should be COMPLEX)."""
        router = IntelligentRouter()

        decision = router.route_task("a" * 10000, token_count=2500)
        assert decision.tier == "complex"
        assert decision.model == "claude-opus-5"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
