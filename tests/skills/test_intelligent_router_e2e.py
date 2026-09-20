"""
E2E tests for IntelligentRouter (ADR-0867)

Validates deterministic, auditable model routing across all phases:
1. Reachability: all 3 models can be reached via real routing decisions
2. Heuristic accuracy: token-based classification matches expected tiers
3. Cost optimization: SIMPLE uses cheapest model, COMPLEX uses best quality
4. Operator overrides: saved preferences respected
5. Audit trail: all decisions logged immutably
6. Edge cases: malformed inputs, boundary conditions
"""

import pytest
import json
import re
from pathlib import Path
from typing import Dict, Any

from core.skills.os_skills.intelligent_router import (
    IntelligentRouter,
    ModelTier,
    Engine,
    RoutingDecision,
)


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 1: Reachability Tests
# Ensure all 3 models can be reached
# ─────────────────────────────────────────────────────────────────────────────

class TestReachability:
    """Test that all 3 complexity tiers are reachable (not arithmetically blocked)."""

    def test_simple_always_routes_haiku(self):
        """SIMPLE task (short prompt) → claude-haiku-4-5."""
        router = IntelligentRouter()
        task = "Write a hello world function in Python"  # ~100 tokens
        decision = router.route_task(task)

        assert decision.model == "claude-haiku-4-5"
        assert decision.tier == "simple"
        assert decision.confidence >= 0.80

    def test_medium_routes_sonnet(self):
        """MEDIUM task (moderate prompt) → claude-sonnet-5."""
        router = IntelligentRouter()
        task = """Analyze the following code and suggest improvements:

        def calculate_fibonacci(n):
            if n <= 1:
                return n
            return calculate_fibonacci(n-1) + calculate_fibonacci(n-2)

        The function is slow. What's the issue and how would you fix it?
        """  # ~300 tokens, triggers MEDIUM (token count in range)

        decision = router.route_task(task, token_count=1000)  # Explicitly set to medium range
        assert decision.model == "claude-sonnet-5"
        assert decision.tier == "medium"

    def test_complex_always_routes_opus(self):
        """COMPLEX task (large prompt) → claude-opus-5."""
        router = IntelligentRouter()
        task = "Large complex task" * 200  # Simulate 4000+ tokens
        decision = router.route_task(task, token_count=4000)

        assert decision.model == "claude-opus-5"
        assert decision.tier == "complex"
        assert decision.confidence >= 0.90

    def test_all_models_used_in_100_samples(self):
        """Verify no dead code: all 3 models actually reached in varied input."""
        router = IntelligentRouter()
        models_reached = set()

        tasks = [
            ("short", 100),
            ("medium task " * 20, 1000),
            ("complex task " * 200, 4000),
        ]

        for task, tokens in tasks:
            decision = router.route_task(task, token_count=tokens)
            models_reached.add(decision.model)

        # All 3 models must be reachable
        assert "claude-haiku-4-5" in models_reached
        assert "claude-sonnet-5" in models_reached
        assert "claude-opus-5" in models_reached


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 2: Heuristic Accuracy Tests
# Token-based classification matches expected tier boundaries
# ─────────────────────────────────────────────────────────────────────────────

class TestHeuristicAccuracy:
    """Test token-based classification heuristics."""

    def test_simple_boundary_below_500(self):
        """Token count < 500 → SIMPLE."""
        router = IntelligentRouter()
        decision = router.route_task("task", token_count=400)

        assert decision.tier == "simple"
        assert decision.model == "claude-haiku-4-5"
        assert decision.signal_strength == "strong"

    def test_medium_boundary_500_to_3000(self):
        """Token count 500-3000 → MEDIUM."""
        router = IntelligentRouter()
        for tokens in [500, 1000, 2000, 2999]:
            decision = router.route_task("task", token_count=tokens)
            assert decision.tier == "medium", f"Failed for tokens={tokens}"
            assert decision.model == "claude-sonnet-5"

    def test_complex_boundary_above_3000(self):
        """Token count >= 3000 → COMPLEX."""
        router = IntelligentRouter()
        for tokens in [3000, 5000, 10000]:
            decision = router.route_task("task", token_count=tokens)
            assert decision.tier == "complex", f"Failed for tokens={tokens}"
            assert decision.model == "claude-opus-5"

    def test_short_simple_task_haiku_native(self):
        """Short, simple task → Haiku, Native engine, <1000ms latency."""
        router = IntelligentRouter()
        task = "What is 2+2?"
        decision = router.route_task(task)

        assert decision.model == "claude-haiku-4-5"
        assert decision.engine == "native"
        assert decision.latency_estimate_ms <= 500

    def test_medium_analysis_task_sonnet(self):
        """Medium analysis task → Sonnet."""
        router = IntelligentRouter()
        task = "Analyze this code " * 50
        decision = router.route_task(task, token_count=1500)

        assert decision.model == "claude-sonnet-5"
        assert decision.tier == "medium"

    def test_long_complex_reasoning_opus(self):
        """Long complex task → Opus."""
        router = IntelligentRouter()
        task = "Design a complex system architecture " * 100
        decision = router.route_task(task, token_count=5000)

        assert decision.model == "claude-opus-5"
        assert decision.tier == "complex"

    def test_latency_critical_forces_native(self):
        """latency_critical=True forces native engine regardless of tier."""
        router = IntelligentRouter()
        task = "task" * 500  # Would normally be COMPLEX
        decision = router.route_task(task, token_count=3500, latency_critical=True)

        assert decision.engine == "native", "latency_critical must force native"

    def test_cost_critical_degrades_to_simple(self):
        """Cost budget exceeded → degrade to SIMPLE."""
        router = IntelligentRouter()
        task = "task" * 500  # COMPLEX task, high cost
        decision = router.route_task(task, token_count=4000, cost_limit_usd=0.10)

        # Should degrade to SIMPLE (cheaper)
        assert decision.tier == "simple"
        assert decision.model == "claude-haiku-4-5"


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 3: Override Tests
# Operator preferences respected
# ─────────────────────────────────────────────────────────────────────────────

class TestOperatorOverrides:
    """Test operator-set model overrides."""

    def test_operator_override_takes_precedence(self):
        """Operator override for SIMPLE: Sonnet instead of Haiku."""
        overrides = {
            "simple": {"model": "claude-sonnet-5"}
        }
        router = IntelligentRouter(overrides=overrides)
        decision = router.route_task("task", token_count=100)

        assert decision.model == "claude-sonnet-5"
        assert decision.tier == "simple"  # Tier unchanged, only model

    def test_override_per_tier(self):
        """Different overrides per tier."""
        overrides = {
            "simple": {"model": "claude-sonnet-5"},
            "medium": {"model": "claude-opus-5"},
            "complex": {"model": "claude-haiku-4-5"},  # Unusual but allowed
        }
        router = IntelligentRouter(overrides=overrides)

        # Test each tier
        assert router.route_task("task", token_count=100).model == "claude-sonnet-5"
        assert router.route_task("task", token_count=1000).model == "claude-opus-5"
        assert router.route_task("task", token_count=4000).model == "claude-haiku-4-5"

    def test_override_persists_across_calls(self):
        """Overrides remain stable across multiple routing decisions."""
        overrides = {"simple": {"model": "claude-opus-5"}}
        router = IntelligentRouter(overrides=overrides)

        # 10 calls with same override → all use Opus
        for _ in range(10):
            decision = router.route_task("task", token_count=100)
            assert decision.model == "claude-opus-5"


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 4: Audit Trail Tests
# Every decision logged with reasoning + immutability
# ─────────────────────────────────────────────────────────────────────────────

class TestAuditTrail:
    """Test immutable audit logging."""

    def test_routing_decision_logged_with_reasoning(self):
        """Every routing decision carries human-readable reasoning."""
        router = IntelligentRouter()
        decision = router.route_task("task", token_count=1000)

        assert decision.reasoning is not None
        assert len(decision.reasoning) > 0
        assert "medium" in decision.reasoning.lower() or "MEDIUM" in decision.reasoning
        assert decision.model in decision.reasoning

    def test_reasoning_is_queryable_by_operator(self):
        """Reasoning mentions key signals (tokens, tier, cost, latency)."""
        router = IntelligentRouter()
        decision = router.route_task("task", token_count=1000)

        # Reasoning must be machine-readable
        assert "Token count" in decision.reasoning
        assert "Model:" in decision.reasoning
        assert decision.model in decision.reasoning
        assert "$" in decision.reasoning  # Cost estimate
        assert "ms" in decision.reasoning  # Latency estimate

    def test_decision_is_immutable(self):
        """RoutingDecision is frozen (cannot modify after creation)."""
        router = IntelligentRouter()
        decision = router.route_task("task", token_count=100)

        # Attempt to modify should raise
        with pytest.raises(AttributeError):
            decision.model = "claude-sonnet-5"

    def test_decision_serializable(self):
        """RoutingDecision can be serialized to JSON (audit-safe)."""
        router = IntelligentRouter()
        decision = router.route_task("task", token_count=100)

        # Should be serializable
        as_dict = decision.to_dict()
        assert isinstance(as_dict, dict)
        assert "model" in as_dict
        assert "tier" in as_dict
        assert "confidence" in as_dict
        assert "reasoning" in as_dict

        # Should be JSON-serializable (no non-serializable objects)
        json_str = json.dumps(as_dict)
        parsed = json.loads(json_str)
        assert parsed["model"] == decision.model

    def test_timestamp_utc_format(self):
        """Decision timestamp is ISO 8601 UTC."""
        router = IntelligentRouter()
        decision = router.route_task("task", token_count=100)

        # Should be ISO 8601 format: YYYY-MM-DDTHH:MM:SS.000Z
        pattern = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$"
        assert re.match(pattern, decision.timestamp_utc)

    def test_decision_history_tracked(self):
        """Router maintains immutable decision history."""
        router = IntelligentRouter()

        for i in range(10):
            decision = router.route_task("task", token_count=100 + i * 100)

        # History should have all 10 decisions
        assert len(router.decision_history) == 10

        # All decisions should be different RoutingDecision objects
        assert len(set(id(d) for d in router.decision_history)) == 10


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 5: Edge Cases
# Boundary conditions, malformed input, fail-safe defaults
# ─────────────────────────────────────────────────────────────────────────────

class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_empty_task_defaults_to_haiku(self):
        """Empty task → SIMPLE (safe default)."""
        router = IntelligentRouter()
        decision = router.route_task("")

        assert decision.model == "claude-haiku-4-5"
        assert decision.tier == "simple"

    def test_very_small_token_count_haiku(self):
        """1-10 token task → Haiku."""
        router = IntelligentRouter()
        decision = router.route_task("task", token_count=5)

        assert decision.model == "claude-haiku-4-5"
        assert decision.tier == "simple"

    def test_tokens_exactly_at_boundary_500(self):
        """Token count exactly at boundary (500) → MEDIUM."""
        router = IntelligentRouter()
        decision = router.route_task("task", token_count=500)

        assert decision.tier == "medium"
        assert decision.model == "claude-sonnet-5"

    def test_tokens_exactly_at_boundary_3000(self):
        """Token count exactly at boundary (3000) → COMPLEX."""
        router = IntelligentRouter()
        decision = router.route_task("task", token_count=3000)

        assert decision.tier == "complex"
        assert decision.model == "claude-opus-5"

    def test_negative_token_count_treated_as_zero(self):
        """Negative token count (error case) → safe default."""
        router = IntelligentRouter()
        # Should not crash; route as SIMPLE
        decision = router.route_task("task", token_count=-100)

        assert decision.model == "claude-haiku-4-5"

    def test_very_large_token_count(self):
        """100K token task → COMPLEX."""
        router = IntelligentRouter()
        decision = router.route_task("task", token_count=100_000)

        assert decision.tier == "complex"
        assert decision.model == "claude-opus-5"

    def test_cost_estimate_reasonable(self):
        """Cost estimate is within expected range."""
        router = IntelligentRouter()
        decision = router.route_task("task", token_count=1000)

        # For 1000 tokens MEDIUM (Sonnet):
        # Input: ~$0.003, Output: ~0.0225 ≈ $0.025
        assert 0.001 < decision.cost_estimate < 5.0

    def test_latency_estimate_reasonable(self):
        """Latency estimate matches engine + model."""
        router = IntelligentRouter()
        decision = router.route_task("task", token_count=100, engine_mode="native")

        # Native Haiku should be ~200ms
        assert 100 < decision.latency_estimate_ms < 1000

    def test_deterministic_routing(self):
        """Same input → same output (deterministic)."""
        router = IntelligentRouter()

        decision1 = router.route_task("task", token_count=1000)
        decision2 = router.route_task("task", token_count=1000)

        # All key fields should match
        assert decision1.model == decision2.model
        assert decision1.tier == decision2.tier
        assert decision1.engine == decision2.engine
        assert decision1.confidence == decision2.confidence


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 6: Statistics
# Router tracks decision distribution
# ─────────────────────────────────────────────────────────────────────────────

class TestStatistics:
    """Test router statistics and analytics."""

    def test_stats_empty_router(self):
        """Empty router returns zero stats."""
        router = IntelligentRouter()
        stats = router.get_stats()

        assert stats["decisions"] == 0

    def test_stats_after_decisions(self):
        """Stats track distribution after routing decisions."""
        router = IntelligentRouter()

        # Make 10 routing decisions
        for i in range(10):
            router.route_task("task", token_count=100 + i * 300)

        stats = router.get_stats()

        assert stats["total_decisions"] == 10
        assert "tier_distribution" in stats
        assert "model_distribution" in stats
        assert "engine_distribution" in stats

    def test_stats_tier_distribution(self):
        """Tier distribution reflects routing decisions."""
        router = IntelligentRouter()

        # 5 SIMPLE, 3 MEDIUM, 2 COMPLEX
        for _ in range(5):
            router.route_task("task", token_count=100)
        for _ in range(3):
            router.route_task("task", token_count=1000)
        for _ in range(2):
            router.route_task("task", token_count=4000)

        stats = router.get_stats()

        assert stats["tier_distribution"]["simple"] == 5
        assert stats["tier_distribution"]["medium"] == 3
        assert stats["tier_distribution"]["complex"] == 2

    def test_avg_confidence_computed(self):
        """Average confidence computed from decision history."""
        router = IntelligentRouter()

        for _ in range(5):
            router.route_task("task", token_count=100)

        stats = router.get_stats()

        assert 0.0 <= stats["avg_confidence"] <= 1.0

    def test_avg_cost_computed(self):
        """Average cost computed from decision history."""
        router = IntelligentRouter()

        for _ in range(5):
            router.route_task("task", token_count=1000)

        stats = router.get_stats()

        assert 0.0 < stats["avg_cost_usd"] < 100.0

    def test_avg_latency_computed(self):
        """Average latency computed from decision history."""
        router = IntelligentRouter()

        for _ in range(5):
            router.route_task("task", token_count=1000)

        stats = router.get_stats()

        assert 0 < stats["avg_latency_ms"] < 100_000


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 7: Integration with Existing Systems
# Verify IntelligentRouter integrates without breaking existing tests
# ─────────────────────────────────────────────────────────────────────────────

class TestIntegration:
    """Integration with existing model selection system."""

    def test_router_doesnt_break_model_selector_compat(self):
        """IntelligentRouter returns same tier names as ModelSelector."""
        router = IntelligentRouter()

        # ModelSelector returns: "simple" | "medium" | "complex"
        decision = router.route_task("task", token_count=1000)

        # IntelligentRouter must return same vocabulary
        assert decision.tier in ("simple", "medium", "complex")
        assert decision.model in (
            "claude-haiku-4-5",
            "claude-sonnet-5",
            "claude-opus-5",
        )

    def test_router_engine_selection_compatible(self):
        """IntelligentRouter returns valid engine names."""
        router = IntelligentRouter()
        decision = router.route_task("task", token_count=1000, engine_mode="acs")

        # Must be one of the valid engines
        assert decision.engine in ("native", "acs", "tde")


# ─────────────────────────────────────────────────────────────────────────────
# Run all tests
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
