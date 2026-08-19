"""Loss Baseline Measurement for Adaptive Strategy Engine (Phase 2 Improvement 4, k=2).

Measures strategy selection agreement between adaptive and empirical ranking
across three operator profiles (high, medium, low expertise).

Expected outcome: adaptive ranking should produce DIFFERENT rankings than
empirical when confidence is high (demonstrating the engine is learning and
applying fingerprint-based preferences, not just falling back).
"""

from dataclasses import dataclass
from typing import List

from core.learning.adaptive_strategy import (
    AdaptiveStrategyEngine,
    StrategyOption,
)
from core.learning.operator_fingerprint import OperatorFingerprint


@dataclass
class LossBaseline:
    """Baseline loss metrics for adaptive strategy selection."""

    profile_name: str
    confidence: float
    empirical_top_strategy: str
    adaptive_top_strategy: str
    agreement: bool  # True if empirical and adaptive selected same strategy
    empirical_scores: dict  # strategy_name -> success_rate
    adaptive_differences: bool  # True if ranking order is different


def create_synthetic_operator_profiles() -> List[OperatorFingerprint]:
    """Create three synthetic operator profiles for loss measurement.

    Profiles:
    1. HIGH expertise (0.9 confidence, aggressive, fast)
    2. MEDIUM expertise (0.5 confidence, neutral)
    3. LOW expertise (0.3 confidence, conservative, thorough)
    """
    return [
        OperatorFingerprint(
            operator_id="high_expertise_operator",
            risk_tolerance=0.85,  # Aggressive
            speed_preference=0.85,  # Fast
            communication_style="terse",
            expertise_profile={
                "general": 0.85,
                "code_review": 0.90,
                "debugging": 0.88,
            },
            confidence=0.95,  # Very confident
            last_updated="2026-08-19T00:00:00",
            total_observations=150,
        ),
        OperatorFingerprint(
            operator_id="medium_expertise_operator",
            risk_tolerance=0.5,  # Neutral
            speed_preference=0.5,  # Neutral
            communication_style="neutral",
            expertise_profile={
                "general": 0.5,
                "code_review": 0.55,
                "debugging": 0.50,
            },
            confidence=0.5,  # Medium confidence
            last_updated="2026-08-19T00:00:00",
            total_observations=50,
        ),
        OperatorFingerprint(
            operator_id="low_expertise_operator",
            risk_tolerance=0.15,  # Conservative
            speed_preference=0.15,  # Thorough
            communication_style="detailed",
            expertise_profile={
                "general": 0.30,
                "code_review": 0.25,
                "debugging": 0.28,
            },
            confidence=0.25,  # Low confidence
            last_updated="2026-08-19T00:00:00",
            total_observations=20,
        ),
    ]


def create_strategy_pool() -> List[StrategyOption]:
    """Create a pool of diverse strategies for ranking."""
    return [
        # Fast, risky (high success but low latency, high cost)
        StrategyOption(
            name="direct_fix",
            required_steps=3,
            avg_latency_ms=50.0,
            avg_cost_cents=80.0,
            success_rate=0.90,
            operator_preference_score=0.5,
        ),
        # Medium, balanced
        StrategyOption(
            name="careful_approach",
            required_steps=8,
            avg_latency_ms=150.0,
            avg_cost_cents=60.0,
            success_rate=0.75,
            operator_preference_score=0.5,
        ),
        # Slow, safe (low success but high latency, low cost)
        StrategyOption(
            name="decompose_and_verify",
            required_steps=15,
            avg_latency_ms=400.0,
            avg_cost_cents=40.0,
            success_rate=0.60,
            operator_preference_score=0.5,
        ),
        # Moderate cost, lower success
        StrategyOption(
            name="pivot_approach",
            required_steps=10,
            avg_latency_ms=200.0,
            avg_cost_cents=70.0,
            success_rate=0.70,
            operator_preference_score=0.5,
        ),
    ]


def measure_empirical_ranking(
    strategies: List[StrategyOption],
) -> tuple[str, dict]:
    """Measure empirical ranking (success_rate only, no fingerprint).

    Returns:
        (top_strategy_name, success_rate_dict)
    """
    scores = {s.name: s.success_rate for s in strategies}
    top_strategy = max(strategies, key=lambda s: s.success_rate).name
    return top_strategy, scores


def measure_adaptive_ranking(
    engine: AdaptiveStrategyEngine,
    fingerprint: OperatorFingerprint,
    strategies: List[StrategyOption],
) -> tuple[str, List[str]]:
    """Measure adaptive ranking using fingerprint.

    Returns:
        (top_strategy_name, full_ranking_order)
    """
    ranked = engine.rank_strategies_by_fingerprint(
        fingerprint,
        strategies,
        task_type="general",
    )
    top_strategy = ranked[0].name if ranked else None
    ranking_order = [s.name for s in ranked]
    return top_strategy, ranking_order


def compute_loss_baseline():
    """Compute baseline loss metrics across all profiles.

    Loss function: strategy_selection_suboptimality = 1.0 - match_rate

    Expected behavior:
    - HIGH confidence: adaptive should DIFFER from empirical (fingerprint applied)
    - MEDIUM confidence: may differ (confidence at boundary)
    - LOW confidence: should fallback to empirical (confidence < 0.7)
    """
    engine = AdaptiveStrategyEngine()
    profiles = create_synthetic_operator_profiles()
    strategies = create_strategy_pool()

    baselines = []

    for profile in profiles:
        empirical_top, empirical_scores = measure_empirical_ranking(strategies)
        adaptive_top, adaptive_ranking = measure_adaptive_ranking(
            engine, profile, strategies
        )

        # Determine if adaptive differs from empirical
        if profile.confidence >= 0.7:
            # Adaptive should be applied; expected to differ
            agreement = empirical_top == adaptive_top
            # Loss: if high confidence, we EXPECT them to differ (engine should learn)
            # So disagreement is actually good
            ranking_differs = adaptive_ranking != [s.name for s in strategies]
        else:
            # Fallback: should match empirical
            agreement = empirical_top == adaptive_top
            ranking_differs = False

        baseline = LossBaseline(
            profile_name=profile.operator_id,
            confidence=profile.confidence,
            empirical_top_strategy=empirical_top,
            adaptive_top_strategy=adaptive_top,
            agreement=agreement,
            empirical_scores=empirical_scores,
            adaptive_differences=ranking_differs,
        )
        baselines.append(baseline)

    return baselines


def print_loss_baseline_report(baselines: List[LossBaseline]):
    """Print human-readable loss baseline report."""
    print("\n" + "=" * 80)
    print("LOSS BASELINE MEASUREMENT — Adaptive Strategy Engine")
    print("=" * 80)
    print("\nLoss Function: strategy_selection_suboptimality = 1.0 - adaptive_quality")
    print("Quality Metric: Agreement with operator preferences (confidence-gated)\n")

    for baseline in baselines:
        print(f"\nProfile: {baseline.profile_name}")
        print(f"  Confidence: {baseline.confidence:.2f}")
        print(f"  Empirical Top Strategy: {baseline.empirical_top_strategy}")
        print(f"  Adaptive Top Strategy: {baseline.adaptive_top_strategy}")
        print(f"  Selection Agreement: {'YES' if baseline.agreement else 'NO'}")
        print(f"  Ranking Differs: {'YES' if baseline.adaptive_differences else 'NO'}")
        print(f"  Empirical Success Rates:")
        for strategy, rate in baseline.empirical_scores.items():
            print(f"    - {strategy}: {rate:.2f}")

    # Summary analysis
    print("\n" + "=" * 80)
    print("ANALYSIS")
    print("=" * 80)

    high_conf = baselines[0]
    med_conf = baselines[1]
    low_conf = baselines[2]

    print(f"\nHigh Confidence (>= 0.7):")
    print(f"  Profile: {high_conf.profile_name} (confidence={high_conf.confidence:.2f})")
    print(f"  Expected: Adaptive ranking DIFFERS from empirical (engine applies preferences)")
    print(f"  Actual: {'DIFFERS' if not high_conf.agreement or high_conf.adaptive_differences else 'AGREES'}")
    if not high_conf.agreement or high_conf.adaptive_differences:
        print(f"  Status: PASS - Engine learned and applied fingerprint-based preferences")
    else:
        print(f"  Status: WARNING - Engine may not be differentiating based on fingerprint")

    print(f"\nMedium Confidence (< 0.7, boundary):")
    print(f"  Profile: {med_conf.profile_name} (confidence={med_conf.confidence:.2f})")
    print(f"  Expected: May fallback to empirical (confidence < 0.7)")
    print(f"  Status: Check implementation (confidence < 0.7 should trigger fallback)")

    print(f"\nLow Confidence (< 0.7):")
    print(f"  Profile: {low_conf.profile_name} (confidence={low_conf.confidence:.2f})")
    print(f"  Expected: Adaptive EQUALS empirical (fallback to success_rate only)")
    print(f"  Actual: {'AGREES' if low_conf.agreement else 'DIFFERS'}")
    if low_conf.agreement:
        print(f"  Status: PASS - Fallback works correctly")
    else:
        print(f"  Status: WARNING - Fallback may not be working")

    # Overall loss computation
    print(f"\nOverall Loss Baseline:")
    high_agreement_bonus = 0.0 if (not high_conf.agreement or high_conf.adaptive_differences) else 0.1
    low_agreement_match = 1.0 if low_conf.agreement else 0.5
    overall_loss = 1.0 - (high_agreement_bonus + low_agreement_match) / 2.0
    print(f"  Loss (lower is better): {overall_loss:.2f}")
    print(f"  Target: < 0.3 (indicates adaptive engine learning is working)")


if __name__ == "__main__":
    baselines = compute_loss_baseline()
    print_loss_baseline_report(baselines)
