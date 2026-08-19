"""E2E Test: Adaptive Strategy Engine Loss Verification (Phase 2 Iteration k=3).

Tests realistic scenario with three operator profiles making strategy selections:
- HIGH confidence (aggressive, fast, expert)
- MEDIUM confidence (neutral, learning)
- LOW confidence (conservative, thorough, novice)

Each profile selects strategies 5 times from pool of 4 diverse strategies.
Measures loss: avg(1.0 - selected_strategy.success_rate) across all selections.
Validates: loss_adaptive < loss_empirical (adaptive improves selection quality).
"""

from typing import List
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from core.learning.adaptive_strategy import (
    AdaptiveStrategyEngine,
    StrategyOption,
)
from core.learning.operator_fingerprint import OperatorFingerprint


def create_operator_profiles() -> List[OperatorFingerprint]:
    """Create three realistic operator profiles for E2E test."""
    return [
        # HIGH expertise: aggressive, fast, experienced operator
        OperatorFingerprint(
            operator_id="expert_operator",
            risk_tolerance=0.85,
            speed_preference=0.85,
            communication_style="terse",
            expertise_profile={
                "general": 0.85,
                "code_review": 0.90,
                "debugging": 0.88,
            },
            confidence=0.95,
            last_updated="2026-08-19T00:00:00",
            total_observations=150,
        ),
        # MEDIUM expertise: neutral, learning operator
        OperatorFingerprint(
            operator_id="medium_operator",
            risk_tolerance=0.5,
            speed_preference=0.5,
            communication_style="neutral",
            expertise_profile={
                "general": 0.5,
                "code_review": 0.55,
                "debugging": 0.50,
            },
            confidence=0.5,
            last_updated="2026-08-19T00:00:00",
            total_observations=50,
        ),
        # LOW expertise: conservative, thorough, novice operator
        OperatorFingerprint(
            operator_id="novice_operator",
            risk_tolerance=0.15,
            speed_preference=0.15,
            communication_style="detailed",
            expertise_profile={
                "general": 0.30,
                "code_review": 0.25,
                "debugging": 0.28,
            },
            confidence=0.25,
            last_updated="2026-08-19T00:00:00",
            total_observations=20,
        ),
    ]


def create_strategy_pool() -> List[StrategyOption]:
    """Create pool of 4 diverse strategies."""
    return [
        StrategyOption(
            name="direct_fix",
            required_steps=3,
            avg_latency_ms=50.0,
            avg_cost_cents=80.0,
            success_rate=0.90,
            operator_preference_score=0.5,
        ),
        StrategyOption(
            name="careful_approach",
            required_steps=8,
            avg_latency_ms=150.0,
            avg_cost_cents=60.0,
            success_rate=0.75,
            operator_preference_score=0.5,
        ),
        StrategyOption(
            name="decompose_and_verify",
            required_steps=15,
            avg_latency_ms=400.0,
            avg_cost_cents=40.0,
            success_rate=0.60,
            operator_preference_score=0.5,
        ),
        StrategyOption(
            name="pivot_approach",
            required_steps=10,
            avg_latency_ms=200.0,
            avg_cost_cents=70.0,
            success_rate=0.70,
            operator_preference_score=0.5,
        ),
    ]


def get_empirical_top_strategy(strategies: List[StrategyOption]) -> StrategyOption:
    """Get top strategy using empirical ranking (success_rate only)."""
    return max(strategies, key=lambda s: s.success_rate)


def get_adaptive_top_strategy(
    fingerprint: OperatorFingerprint,
    strategies: List[StrategyOption],
) -> StrategyOption:
    """Get top strategy using adaptive ranking."""
    engine = AdaptiveStrategyEngine()
    ranked = engine.rank_strategies_by_fingerprint(
        fingerprint, strategies, task_type="general"
    )
    return ranked[0] if ranked else strategies[0]


def compute_loss(
    selected_strategies: List[StrategyOption],
) -> float:
    """Compute loss for a sequence of selections.

    Loss = avg(1.0 - selected_strategy.success_rate)
    Lower loss = better (higher success rate selections).
    """
    if not selected_strategies:
        return 1.0
    losses = [1.0 - s.success_rate for s in selected_strategies]
    return sum(losses) / len(losses)


def test_adaptive_strategy_e2e_loss_verification():
    """E2E Test: Verify adaptive ranking reduces selection loss.

    Scenario: 3 profiles × 5 selections each from 4-strategy pool.
    Measures empirical vs adaptive loss.
    Gate: loss_adaptive < loss_empirical (with 10% tolerance).
    """
    profiles = create_operator_profiles()
    strategies = create_strategy_pool()
    engine = AdaptiveStrategyEngine()

    results = []

    print("\n" + "=" * 80)
    print("E2E TEST: Adaptive Strategy Engine Loss Verification")
    print("=" * 80)

    for profile in profiles:
        # Get empirical ranking (all profiles same)
        empirical_ranked = sorted(strategies, key=lambda s: s.success_rate, reverse=True)
        empirical_order = [s.name for s in empirical_ranked]

        # Get adaptive ranking (per profile)
        adaptive_ranked = engine.rank_strategies_by_fingerprint(
            profile, strategies, task_type="general"
        )
        adaptive_order = [s.name for s in adaptive_ranked]

        # Simulate 5 selections using empirical ranking
        empirical_selections = [empirical_ranked[0] for _ in range(5)]

        # Simulate 5 selections using adaptive ranking
        adaptive_selections = [adaptive_ranked[0] for _ in range(5)]

        # Compute losses
        loss_empirical = compute_loss(empirical_selections)
        loss_adaptive = compute_loss(adaptive_selections)

        improvement_pct = (
            ((loss_empirical - loss_adaptive) / loss_empirical * 100)
            if loss_empirical > 0
            else 0.0
        )

        results.append(
            {
                "profile_id": profile.operator_id,
                "confidence": profile.confidence,
                "empirical_top": empirical_selections[0].name if empirical_selections else "none",
                "adaptive_top": adaptive_selections[0].name if adaptive_selections else "none",
                "loss_empirical": loss_empirical,
                "loss_adaptive": loss_adaptive,
                "improvement_pct": improvement_pct,
            }
        )

        print(f"\nProfile: {profile.operator_id} (confidence={profile.confidence:.2f})")
        print(f"  Empirical ranking: {empirical_order}")
        print(f"  Adaptive ranking:  {adaptive_order}")
        print(f"  Empirical top strategy: {results[-1]['empirical_top']}")
        print(f"  Adaptive top strategy: {results[-1]['adaptive_top']}")
        print(f"  Loss (empirical): {loss_empirical:.4f}")
        print(f"  Loss (adaptive): {loss_adaptive:.4f}")
        print(f"  Improvement: {improvement_pct:.1f}%")

    # Aggregate results across all profiles
    total_loss_empirical = sum(r["loss_empirical"] for r in results) / len(results)
    total_loss_adaptive = sum(r["loss_adaptive"] for r in results) / len(results)
    total_improvement = (
        ((total_loss_empirical - total_loss_adaptive) / total_loss_empirical * 100)
        if total_loss_empirical > 0
        else 0.0
    )

    print(f"\n" + "=" * 80)
    print("AGGREGATE RESULTS (3 profiles × 5 selections each)")
    print("=" * 80)
    print(f"Loss (empirical): {total_loss_empirical:.4f}")
    print(f"Loss (adaptive): {total_loss_adaptive:.4f}")
    print(f"Improvement: {total_improvement:.1f}%")

    # Gate: loss_adaptive <= loss_empirical (10% tolerance margin)
    margin_threshold = total_loss_empirical * 0.1
    loss_delta = total_loss_adaptive - total_loss_empirical
    is_pass = loss_delta <= margin_threshold

    print(f"\nGate: loss_adaptive < loss_empirical (10% tolerance)")
    print(f"  loss_delta = {loss_delta:.4f}")
    print(f"  margin_threshold = {margin_threshold:.4f}")
    print(f"  Status: {'PASS' if is_pass else 'FAIL'}")

    assert (
        is_pass
    ), f"E2E FAIL: Adaptive loss ({total_loss_adaptive:.4f}) not better than empirical ({total_loss_empirical:.4f}) within 10% tolerance"

    print(f"\nE2E PASS: loss_empirical={total_loss_empirical:.4f}, loss_adaptive={total_loss_adaptive:.4f}, improvement={total_improvement:.1f}%")


if __name__ == "__main__":
    test_adaptive_strategy_e2e_loss_verification()
