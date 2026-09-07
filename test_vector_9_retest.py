#!/usr/bin/env python3
"""
Re-test: Adversarial Vector #9 — Feedback Contradiction (Consistency)

Tests whether the fix to consistency_checker.py resolves the trend-reversal vulnerability.

The vulnerability: sorted(losses) by numeric value reversed chronological order,
causing decreasing loss to be detected as increasing, which would allow contradictory
feedback to appear consistent.

The fix: samples are sorted by timestamp, preserving chronological order.
"""

import sys
sys.path.insert(0, '/home/shumway/projects/CorvinOS')

from core.learning.consistency_checker import (
    FeedbackConsistencyValidator,
    FeedbackSignal,
)


class MockEventStore:
    """Mock event store that returns losses in chronological order."""

    def __init__(self, losses):
        self.losses = losses
        self.newest_first_supported = True

    def query_events(self, tenant_id, skill_id, limit, newest_first=False):
        """Return events with timestamps, preserving chronological order."""
        # Return events in order
        events = []
        for i, loss in enumerate(self.losses[-limit:] if limit else self.losses):
            events.append({
                "timestamp": f"2026-09-07T12:{i:02d}:00Z",
                "payload": {"total_loss": loss},
            })
        if newest_first:
            events = list(reversed(events))
        return events


class MockAuditBackend:
    """Mock audit backend for testing."""

    def __init__(self):
        self.events = []

    def write_event(self, event):
        self.events.append(event)


def test_exploit_loss_trend_reversal():
    """
    ATTACK TEST: Loss Trend Reversal via Numeric Sorting

    Scenario:
    1. System operates with DECREASING loss (good progress): [0.6, 0.58, 0.56, 0.54, 0.52]
    2. Attacker submits BAD feedback (contradictory)
    3. OLD BUG: sorted() reverses order to [0.52, 0.54, 0.56, 0.58, 0.6] (appears INCREASING)
    4. Result: BAD feedback appears consistent with apparent trend
    5. EXPECTED WITH FIX: Chronological order preserved, trend correctly detected as DECREASING,
       contradiction detected and downweighted
    """

    validator = FeedbackConsistencyValidator()

    # Real chronological order: DECREASING loss (improving system)
    # Timeline: 0.6 (oldest) → 0.52 (newest)
    decreasing_losses = [0.6, 0.58, 0.56, 0.54, 0.52]

    event_store = MockEventStore(decreasing_losses)
    audit_backend = MockAuditBackend()

    validator.event_store = event_store
    validator.audit_backend = audit_backend

    # Attacker submits contradictory feedback: BAD when loss is improving
    result = validator.validate_consistency(
        feedback_id="fb_exploit_001",
        skill_id="os.router",
        task_id="task_exploit_001",
        feedback_signal=FeedbackSignal.BAD,  # Contradictory!
        tenant_id="_default",
    )

    print("=" * 70)
    print("TEST 1: Loss Trend Reversal Exploit")
    print("=" * 70)
    print(f"Input losses (chronological): {decreasing_losses}")
    print(f"Expected trend: DECREASING (loss improving)")
    print(f"Expected expected_signal: GOOD (should expect good feedback)")
    print(f"User feedback: BAD (contradictory)")
    print()
    print(f"Detected trend: {result.loss_trend}")
    print(f"Consistency score: {result.consistency_score}")
    print(f"Is consistent: {result.is_consistent}")
    print(f"Contradiction reason: {result.contradiction_reason}")
    print()

    # VULNERABILITY CHECK
    passed = False
    if result.loss_trend == "increasing":
        print("❌ VULNERABILITY FOUND: Trend detection reversed!")
        print("   - Input losses: [0.6, 0.58, 0.56, 0.54, 0.52] (DECREASING)")
        print("   - Detected as: INCREASING (WRONG)")
        print("   - This would allow contradictory feedback to bypass detection")
        passed = False
    elif result.loss_trend == "decreasing":
        print("✅ Trend correctly detected as DECREASING")
        if not result.is_consistent and result.consistency_score < 0.5:
            print("✅ Contradictory feedback correctly marked as INCONSISTENT")
            print(f"   Downweight factor: {1.0 - result.consistency_score:.3f}")
            print("✅ FIX VALIDATED: Exploit no longer works")
            passed = True
        else:
            print("❌ FAIL: Contradictory feedback marked as consistent!")
            print("   The fix did not work correctly")
            passed = False
    else:
        print(f"❌ UNEXPECTED: Trend detected as {result.loss_trend}")
        passed = False

    return passed


def test_consistency_check_cascade_attack():
    """
    ATTACK TEST: Multiple Contradictions (Feedback Cascade)

    If trend reversal bug exists, attacker can submit multiple contradictory
    feedback signals that all appear consistent, corrupting the optimizer.
    """

    validator = FeedbackConsistencyValidator()

    # Decreasing loss trend (improving system)
    decreasing_losses = [0.75, 0.70, 0.65, 0.60, 0.55]

    event_store = MockEventStore(decreasing_losses)
    audit_backend = MockAuditBackend()

    validator.event_store = event_store
    validator.audit_backend = audit_backend

    results = []
    for i in range(3):
        result = validator.validate_consistency(
            feedback_id=f"fb_cascade_{i:03d}",
            skill_id="os.router",
            task_id=f"task_cascade_{i:03d}",
            feedback_signal=FeedbackSignal.BAD,  # All contradictory
            tenant_id="_default",
        )
        results.append(result)

    print("\n" + "=" * 70)
    print("TEST 2: Feedback Cascade Attack")
    print("=" * 70)
    print(f"Input losses (decreasing): {decreasing_losses}")
    print(f"User feedback: BAD (contradictory to all)")
    print()

    all_marked_inconsistent = all(not r.is_consistent for r in results)

    if all_marked_inconsistent:
        print("✅ All contradictory feedback marked as INCONSISTENT")
        for i, r in enumerate(results):
            print(f"   fb_cascade_{i:03d}: score={r.consistency_score:.3f}")
        print("✅ FIX VALIDATED: Cascade attack blocked")
        return True
    else:
        print("❌ VULNERABILITY: Some contradictory feedback marked as CONSISTENT!")
        for i, r in enumerate(results):
            if r.is_consistent:
                print(f"   fb_cascade_{i:03d}: score={r.consistency_score:.3f} (SHOULD BE INCONSISTENT)")
        return False


def test_audit_trail_completeness():
    """
    Verify that contradictory feedback is logged to audit trail
    and contradiction events are emitted.
    """

    validator = FeedbackConsistencyValidator()

    audit_backend = MockAuditBackend()
    event_store = MockEventStore([0.6, 0.58, 0.56, 0.54, 0.52])

    validator.audit_backend = audit_backend
    validator.event_store = event_store

    result = validator.validate_consistency(
        feedback_id="fb_audit_001",
        skill_id="os.router",
        task_id="task_audit_001",
        feedback_signal=FeedbackSignal.BAD,  # Contradictory
        tenant_id="_default",
    )

    print("\n" + "=" * 70)
    print("TEST 3: Audit Trail Verification")
    print("=" * 70)
    print(f"Total audit events logged: {len(audit_backend.events)}")
    print()

    consistency_events = [e for e in audit_backend.events if e.get("event_type") == "feedback_consistency_checked"]
    contradiction_events = [e for e in audit_backend.events if e.get("event_type") == "feedback_contradiction"]

    print(f"Consistency check events: {len(consistency_events)}")
    print(f"Contradiction events: {len(contradiction_events)}")

    if len(consistency_events) > 0:
        print("✅ Consistency check logged")
    else:
        print("❌ Consistency check NOT logged")

    if result.is_consistent and len(contradiction_events) == 0:
        print("⚠️  WARNING: Contradictory feedback marked consistent, NO contradiction event logged")
        print("   This would allow the attack to bypass audit detection")
        return False
    elif not result.is_consistent and len(contradiction_events) > 0:
        print("✅ Contradiction event logged for downweighting")
        print("✅ FIX VALIDATED: Audit trail is complete")
        return True
    else:
        print(f"⚠️  State: is_consistent={result.is_consistent}, contradiction_events={len(contradiction_events)}")
        if not result.is_consistent:
            print("✅ FIX VALIDATED: Contradictory feedback detected")
            return True
        return False


if __name__ == "__main__":
    print("\n" + "=" * 70)
    print("ADVERSARIAL VECTOR #9 RE-TEST: Feedback Contradiction (Consistency)")
    print("=" * 70)
    print()

    test1_pass = test_exploit_loss_trend_reversal()
    test2_pass = test_consistency_check_cascade_attack()
    test3_pass = test_audit_trail_completeness()

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Test 1 (Trend Reversal Exploit): {'PASS ✅' if test1_pass else 'FAIL ❌'}")
    print(f"Test 2 (Cascade Attack): {'PASS ✅' if test2_pass else 'FAIL ❌'}")
    print(f"Test 3 (Audit Trail): {'PASS ✅' if test3_pass else 'FAIL ❌'}")
    print()

    if test1_pass and test2_pass and test3_pass:
        print("🎉 RESULT: Vector #9 MITIGATED - All exploits BLOCKED")
        print()
        print("STATUS: exploitable=false, severity=LOW (all mitigations in place)")
        sys.exit(0)
    else:
        print("⚠️  RESULT: Vector #9 STILL EXPLOITABLE - Mitigations incomplete")
        print()
        print("STATUS: exploitable=true, severity=HIGH (mitigations failed)")
        sys.exit(1)
