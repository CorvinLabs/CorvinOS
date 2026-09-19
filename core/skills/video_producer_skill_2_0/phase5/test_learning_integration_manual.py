#!/usr/bin/env python3
"""Manual validation script for Blocker 6 Learning Integration

This script can be run without pytest to verify core functionality:
  python3 test_learning_integration_manual.py

Tests:
1. RenderOutcome creation and confidence calculation
2. Learning optimizer initialization and metrics tracking
3. Tier performance statistics
4. Optimizer feedback recommendations
5. Audit trail event compatibility

Exit code: 0 = all tests passed, 1 = failures
"""

import sys
from datetime import datetime
from learning_integration import (
    LearningOptimizer,
    RenderOutcome,
    RenderFeedback,
    TierConfidence,
)


class ManualTestRunner:
    """Manual test runner without pytest dependency"""

    def __init__(self):
        self.tests_passed = 0
        self.tests_failed = 0
        self.failures = []

    def test(self, name: str, condition: bool, expected=True):
        """Run a single test assertion"""
        if condition == expected:
            self.tests_passed += 1
            print(f"  ✅ {name}")
        else:
            self.tests_failed += 1
            self.failures.append(f"{name} (expected {expected}, got {condition})")
            print(f"  ❌ {name}")

    def section(self, title: str):
        """Print test section header"""
        print(f"\n{title}")
        print("-" * len(title))

    def summary(self):
        """Print test summary and return exit code"""
        print(f"\n{'='*60}")
        print(f"Test Results: {self.tests_passed} passed, {self.tests_failed} failed")
        print(f"{'='*60}")

        if self.failures:
            print("\nFailures:")
            for failure in self.failures:
                print(f"  - {failure}")
            return 1
        else:
            print("\n✅ All tests passed!")
            return 0


def test_render_outcome_creation():
    """Test: RenderOutcome dataclass creation"""
    runner = ManualTestRunner()
    runner.section("Test: RenderOutcome Creation")

    outcome = RenderOutcome(
        success=True,
        tier="TIER_2_RICH",
        animation_id="test_001",
        render_time_ms=25000,
        quality_score=0.85,
        fallback_used=False
    )

    runner.test("outcome.success", outcome.success)
    runner.test("outcome.tier", outcome.tier == "TIER_2_RICH")
    runner.test("outcome.animation_id", outcome.animation_id == "test_001")
    runner.test("outcome.render_time_ms", outcome.render_time_ms == 25000)
    runner.test("outcome.quality_score", outcome.quality_score == 0.85)
    runner.test("outcome.fallback_used", outcome.fallback_used == False)
    runner.test("outcome.confidence default", outcome.confidence == 0.5)

    return runner


def test_confidence_calculation():
    """Test: Confidence score calculation"""
    runner = ManualTestRunner()
    runner.section("Test: Confidence Calculation")

    optimizer = LearningOptimizer()

    # Success case: high confidence
    outcome_success = RenderOutcome(
        success=True, tier="TIER_2_RICH", animation_id="conf_1",
        render_time_ms=25000, quality_score=0.9, fallback_used=False
    )
    conf_success = optimizer.calculate_confidence(outcome_success)
    runner.test("Success confidence > 0.8", conf_success > 0.8)
    runner.test("Success confidence <= 1.0", conf_success <= 1.0)

    # Failure case: low confidence
    outcome_fail = RenderOutcome(
        success=False, tier="TIER_2_RICH", animation_id="conf_2",
        render_time_ms=0, quality_score=0.0, fallback_used=True
    )
    conf_fail = optimizer.calculate_confidence(outcome_fail)
    runner.test("Failure confidence < 0.3", conf_fail < 0.3)
    runner.test("Failure confidence > 0.0", conf_fail > 0.0)

    # Generic failure: medium confidence
    outcome_generic_fail = RenderOutcome(
        success=False, tier="TIER_2_RICH", animation_id="conf_3",
        render_time_ms=0, quality_score=0.0, fallback_used=False
    )
    conf_generic = optimizer.calculate_confidence(outcome_generic_fail)
    runner.test("Generic failure confidence > 0.2", conf_generic > 0.2)
    runner.test("Generic failure confidence < 0.6", conf_generic < 0.6)

    return runner


def test_metrics_tracking():
    """Test: Metrics tracking per tier"""
    runner = ManualTestRunner()
    runner.section("Test: Metrics Tracking")

    optimizer = LearningOptimizer()

    # Record 5 successes and 1 failure for TIER_2_RICH
    for i in range(5):
        outcome = RenderOutcome(
            success=True, tier="TIER_2_RICH", animation_id=f"track_{i}",
            render_time_ms=25000, quality_score=0.8, fallback_used=False
        )
        optimizer.record_render_outcome(outcome)

    outcome_fail = RenderOutcome(
        success=False, tier="TIER_2_RICH", animation_id="track_fail",
        render_time_ms=0, quality_score=0.0, fallback_used=True
    )
    optimizer.record_render_outcome(outcome_fail)

    # Verify metrics
    success_count = optimizer.tier_success_counts["TIER_2_RICH"]
    fail_count = optimizer.tier_fail_counts["TIER_2_RICH"]
    success_rate = optimizer.get_tier_success_rate("TIER_2_RICH")
    avg_time = optimizer.get_tier_avg_render_time("TIER_2_RICH")

    runner.test("success_count", success_count == 5)
    runner.test("fail_count", fail_count == 1)
    runner.test("success_rate == 0.83 (5/6)", abs(success_rate - 0.83) < 0.01)
    runner.test("avg_time == 25000", avg_time == 25000.0)
    runner.test("render_times list has 5 entries", len(optimizer.render_times["TIER_2_RICH"]) == 5)

    return runner


def test_optimizer_feedback():
    """Test: Optimizer feedback recommendations"""
    runner = ManualTestRunner()
    runner.section("Test: Optimizer Feedback")

    optimizer = LearningOptimizer()

    # Tier 1: 100% success, fast
    for i in range(5):
        outcome = RenderOutcome(
            success=True, tier="TIER_1_QUICK", animation_id="feedback_test",
            render_time_ms=5000, quality_score=0.7, fallback_used=False
        )
        optimizer.record_render_outcome(outcome)

    # Tier 2: 50% success, slower
    for i in range(5):
        outcome = RenderOutcome(
            success=(i % 2 == 0), tier="TIER_2_RICH", animation_id="feedback_test",
            render_time_ms=(25000 if i % 2 == 0 else 0),
            quality_score=(0.8 if i % 2 == 0 else 0.0),
            fallback_used=(i % 2 != 0)
        )
        optimizer.record_render_outcome(outcome)

    # Optimizer should prefer TIER_1
    next_tier = optimizer.optimizer_feedback_next_tier("feedback_test")
    runner.test("Optimizer recommends TIER_1_QUICK", next_tier == "TIER_1_QUICK")

    # Check tier success rates
    tier1_success = optimizer.get_tier_success_rate("TIER_1_QUICK")
    tier2_success = optimizer.get_tier_success_rate("TIER_2_RICH")
    runner.test("TIER_1 success rate == 1.0", tier1_success == 1.0)
    runner.test("TIER_2 success rate == 0.6", abs(tier2_success - 0.6) < 0.01)

    return runner


def test_audit_trail_compatibility():
    """Test: Events are audit-trail compatible"""
    runner = ManualTestRunner()
    runner.section("Test: Audit Trail Compatibility")

    optimizer = LearningOptimizer()

    outcome = RenderOutcome(
        success=True, tier="TIER_2_RICH", animation_id="audit_test",
        render_time_ms=25000, quality_score=0.85, fallback_used=False
    )
    result = optimizer.record_render_outcome(outcome)

    # Simulate audit event creation
    audit_event = {
        "event_type": "skill_executed",
        "skill_id": "video_producer.tier_dispatcher",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "tenant_id": "_default",
        "input": {
            "tier": result.tier,
            "animation_id": result.animation_id,
        },
        "output": {
            "success": result.success,
            "render_time_ms": result.render_time_ms,
            "quality_score": result.quality_score,
        },
        "confidence": result.confidence,
    }

    runner.test("event_type", audit_event["event_type"] == "skill_executed")
    runner.test("skill_id", "tier_dispatcher" in audit_event["skill_id"])
    runner.test("timestamp present", "T" in audit_event["timestamp"])
    runner.test("tenant_id", audit_event["tenant_id"] == "_default")
    runner.test("input.tier", audit_event["input"]["tier"] == "TIER_2_RICH")
    runner.test("output.success", audit_event["output"]["success"] is True)
    runner.test("confidence value", 0.0 <= audit_event["confidence"] <= 1.0)

    return runner


def test_statistics_generation():
    """Test: Statistics generation"""
    runner = ManualTestRunner()
    runner.section("Test: Statistics Generation")

    optimizer = LearningOptimizer()

    # Add feedback
    feedback = RenderFeedback(
        animation_id="stats_test",
        render_time_ms=25000,
        quality_score=8.0,
        engagement_score=7.0,
        tier_used="TIER_2_RICH",
        user_id="test_user"
    )
    optimizer.record_feedback(feedback)

    stats = optimizer.get_statistics()
    runner.test("stats has animation_id", "stats_test" in stats)
    runner.test("avg_quality", stats["stats_test"]["avg_quality"] == 8.0)
    runner.test("avg_engagement", stats["stats_test"]["avg_engagement"] == 7.0)
    runner.test("avg_render_time_ms", stats["stats_test"]["avg_render_time_ms"] == 25000)
    runner.test("num_samples", stats["stats_test"]["num_samples"] == 1)

    return runner


def test_no_data_defaults():
    """Test: Sensible defaults when no data"""
    runner = ManualTestRunner()
    runner.section("Test: No-Data Defaults")

    optimizer = LearningOptimizer()

    # Query with no data
    success_rate = optimizer.get_tier_success_rate("TIER_2_RICH")
    avg_time = optimizer.get_tier_avg_render_time("TIER_1_QUICK")
    stats = optimizer.get_statistics()

    runner.test("no_data success_rate == 0.5", success_rate == 0.5)
    runner.test("no_data avg_time == 0.0", avg_time == 0.0)
    runner.test("no_data stats == {}", stats == {})

    return runner


def main():
    """Run all manual tests"""
    print("\n" + "=" * 60)
    print("Blocker 6: Learning Integration — Manual Validation Tests")
    print("=" * 60)

    runners = [
        test_render_outcome_creation(),
        test_confidence_calculation(),
        test_metrics_tracking(),
        test_optimizer_feedback(),
        test_audit_trail_compatibility(),
        test_statistics_generation(),
        test_no_data_defaults(),
    ]

    total_passed = sum(r.tests_passed for r in runners)
    total_failed = sum(r.tests_failed for r in runners)

    print(f"\n{'='*60}")
    print(f"FINAL RESULTS: {total_passed} passed, {total_failed} failed")
    print(f"{'='*60}")

    if total_failed > 0:
        print("\n❌ Some tests failed!")
        return 1
    else:
        print("\n✅ All manual validation tests passed!")
        print("\nLearning integration is ready for production integration.")
        print("See: LEARNING_INTEGRATION_REFERENCE.md for API details")
        return 0


if __name__ == "__main__":
    sys.exit(main())
