#!/usr/bin/env python3
"""Validation script for L5 k=3, k=4, k=5 gates.

Validates that all three gate modules can be imported and basic functionality works.
"""

import sys
import os

# Add core to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

def validate_imports():
    """Validate that all modules can be imported."""
    print("=" * 60)
    print("STEP 1: Validating imports...")
    print("=" * 60)

    try:
        from core.learning.quality_gate import QualityGate, QualityLevel
        print("✓ QualityGate imported successfully")
    except Exception as e:
        print(f"✗ QualityGate import failed: {e}")
        return False

    try:
        from core.learning.conflict_resolver import ConflictResolver, ConflictDetector
        print("✓ ConflictResolver imported successfully")
    except Exception as e:
        print(f"✗ ConflictResolver import failed: {e}")
        return False

    try:
        from core.learning.rollback_guard import RollbackGuard, Criticality
        print("✓ RollbackGuard imported successfully")
    except Exception as e:
        print(f"✗ RollbackGuard import failed: {e}")
        return False

    return True


def validate_quality_gate():
    """Validate QualityGate basic functionality."""
    print("\n" + "=" * 60)
    print("STEP 2: Validating QualityGate...")
    print("=" * 60)

    from core.learning.quality_gate import QualityGate, QualityLevel

    class MockAudit:
        def __init__(self):
            self.events = []

        def write_event(self, e):
            self.events.append(e)
            return len(self.events)

    gate = QualityGate(audit_backend=MockAudit())

    # Test 1: Compute quality score
    try:
        score = gate.compute_quality(
            "test_skill",
            "test_metric",
            [0.01, 0.02, 0.015],
            0.015,
            0.9,
            [0.7, 0.71, 0.705],
        )
        print(f"✓ Quality score computed: {score.composite_score:.2f} ({score.quality_level.value})")
    except Exception as e:
        print(f"✗ Quality score computation failed: {e}")
        return False

    # Test 2: Verify metrics are between 0 and 1
    try:
        assert 0.0 <= score.quality_metrics.overfitting_risk <= 1.0
        assert 0.0 <= score.quality_metrics.noise_ratio <= 1.0
        assert 0.0 <= score.quality_metrics.convergence_rate <= 1.0
        assert 0.0 <= score.quality_metrics.stability_score <= 1.0
        print("✓ All metrics in valid range [0.0, 1.0]")
    except AssertionError as e:
        print(f"✗ Metric validation failed: {e}")
        return False

    # Test 3: Verify audit was called
    try:
        assert len(gate.audit_backend.events) > 0
        assert gate.audit_backend.events[0]["event_type"] == "learning_quality_score_computed"
        print("✓ Audit trail recorded quality score")
    except Exception as e:
        print(f"✗ Audit validation failed: {e}")
        return False

    # Test 4: Retrieve score
    try:
        retrieved = gate.get_score("test_skill", "test_metric")
        assert retrieved is not None
        print("✓ Score retrieved from storage")
    except Exception as e:
        print(f"✗ Score retrieval failed: {e}")
        return False

    return True


def validate_conflict_resolver():
    """Validate ConflictResolver basic functionality."""
    print("\n" + "=" * 60)
    print("STEP 3: Validating ConflictResolver...")
    print("=" * 60)

    from core.learning.conflict_resolver import ConflictResolver, ConflictDetector
    from datetime import datetime, timedelta

    class MockAudit:
        def __init__(self):
            self.events = []

        def write_event(self, e):
            self.events.append(e)
            return len(self.events)

    resolver = ConflictResolver(audit_backend=MockAudit())

    # Test 1: Detect no conflict with different metrics
    try:
        pending = {
            "skill_a": {
                "metric_x": {
                    "approval_id": "a1",
                    "operator_timestamp": datetime.utcnow().isoformat() + "Z",
                    "ttl_expires": (datetime.utcnow() + timedelta(hours=1)).isoformat() + "Z",
                }
            },
            "skill_b": {
                "metric_y": {
                    "approval_id": "b1",
                    "operator_timestamp": datetime.utcnow().isoformat() + "Z",
                    "ttl_expires": (datetime.utcnow() + timedelta(hours=1)).isoformat() + "Z",
                }
            },
        }
        conflicts = ConflictDetector.detect_conflicts(pending)
        assert len(conflicts) == 0
        print("✓ No conflict detected for different metrics")
    except Exception as e:
        print(f"✗ Conflict detection (no-conflict) failed: {e}")
        return False

    # Test 2: Detect conflict with same metric, overlapping time
    try:
        now = datetime.utcnow()
        pending = {
            "skill_a": {
                "param": {
                    "approval_id": "a1",
                    "operator_timestamp": now.isoformat() + "Z",
                    "ttl_expires": (now + timedelta(hours=1)).isoformat() + "Z",
                }
            },
            "skill_b": {
                "param": {
                    "approval_id": "b1",
                    "operator_timestamp": (now + timedelta(minutes=5)).isoformat() + "Z",
                    "ttl_expires": (now + timedelta(hours=2)).isoformat() + "Z",
                }
            },
        }
        conflicts = ConflictDetector.detect_conflicts(pending)
        assert len(conflicts) == 1
        print("✓ Conflict detected for overlapping same-metric requests")
    except Exception as e:
        print(f"✗ Conflict detection (conflict) failed: {e}")
        return False

    # Test 3: Resolve with default strategy (serialize)
    try:
        resolutions = resolver.detect_and_resolve(pending)
        assert len(resolutions) == 1
        from core.learning.conflict_resolver import ConflictStrategy
        assert resolutions[0].strategy == ConflictStrategy.SERIALIZE
        print("✓ Conflict resolved via SERIALIZE strategy (default)")
    except Exception as e:
        print(f"✗ Conflict resolution failed: {e}")
        return False

    # Test 4: Audit recorded
    try:
        assert len(resolver.audit_backend.events) > 0
        assert resolver.audit_backend.events[0]["event_type"] == "learning_conflict_detected"
        print("✓ Conflict audited")
    except Exception as e:
        print(f"✗ Conflict audit failed: {e}")
        return False

    return True


def validate_rollback_guard():
    """Validate RollbackGuard basic functionality."""
    print("\n" + "=" * 60)
    print("STEP 4: Validating RollbackGuard...")
    print("=" * 60)

    from core.learning.rollback_guard import RollbackGuard, Criticality

    class MockAudit:
        def __init__(self):
            self.events = []

        def write_event(self, e):
            self.events.append(e)
            return len(self.events)

    guard = RollbackGuard(audit_backend=MockAudit())

    # Test 1: Register approval with default hold
    try:
        guard.register_approval(
            approval_id="test_a1",
            skill_id="skill_test",
            criticality=Criticality.MEDIUM,
        )
        assert guard.skill_hold_config["skill_test"] == 12
        print("✓ Approval registered with default hold (12h for MEDIUM)")
    except Exception as e:
        print(f"✗ Approval registration failed: {e}")
        return False

    # Test 2: Check can_revoke during hold period
    try:
        allowed, reason = guard.can_revoke("test_a1", "skill_test")
        assert allowed is False
        assert "remaining" in reason.lower()
        print("✓ Revoke blocked during hold period (advisory)")
    except Exception as e:
        print(f"✗ Hold period check failed: {e}")
        return False

    # Test 3: Register critical skill (1h hold)
    try:
        guard.register_approval(
            approval_id="test_c1",
            skill_id="skill_critical",
            criticality=Criticality.CRITICAL,
        )
        assert guard.skill_hold_config["skill_critical"] == 1
        print("✓ Critical skill registered with 1h hold")
    except Exception as e:
        print(f"✗ Critical skill registration failed: {e}")
        return False

    # Test 4: Force-revoke with reason
    try:
        decision = guard.request_revoke(
            approval_id="test_c1",
            skill_id="skill_critical",
            operator_id="operator:test",
            force=True,
            reason="Test force-revoke",
        )
        assert decision.allowed is True
        print("✓ Force-revoke allowed with mandatory reason")
    except Exception as e:
        print(f"✗ Force-revoke failed: {e}")
        return False

    # Test 5: Audit recorded
    try:
        assert len(guard.audit_backend.events) > 0
        event_types = [e.get("event_type") for e in guard.audit_backend.events]
        assert "skill_approval_revoke_requested" in event_types or "skill_approval_force_revoked" in event_types
        print("✓ Revoke decisions audited")
    except Exception as e:
        print(f"✗ Revoke audit failed: {e}")
        return False

    # Test 6: Force-revoke requires reason
    try:
        guard.register_approval(
            approval_id="test_a2",
            skill_id="skill_test",
            criticality=Criticality.MEDIUM,
        )
        try:
            guard.request_revoke(
                approval_id="test_a2",
                skill_id="skill_test",
                operator_id="operator:test",
                force=True,
                reason="",  # Empty reason — should fail
            )
            print("✗ Force-revoke accepted empty reason (should reject)")
            return False
        except ValueError:
            print("✓ Force-revoke rejects empty reason (fail-closed)")
    except Exception as e:
        print(f"✗ Reason validation test failed: {e}")
        return False

    return True


def main():
    """Run all validations."""
    print("\n" + "=" * 60)
    print("L5 k=3, k=4, k=5 GATE VALIDATION")
    print("=" * 60 + "\n")

    steps = [
        ("Imports", validate_imports),
        ("QualityGate", validate_quality_gate),
        ("ConflictResolver", validate_conflict_resolver),
        ("RollbackGuard", validate_rollback_guard),
    ]

    results = []
    for name, validator in steps:
        try:
            result = validator()
            results.append((name, result))
        except Exception as e:
            print(f"\n✗ FATAL ERROR in {name}: {e}")
            import traceback
            traceback.print_exc()
            results.append((name, False))

    # Summary
    print("\n" + "=" * 60)
    print("VALIDATION SUMMARY")
    print("=" * 60)

    for name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"{status}: {name}")

    all_pass = all(r for _, r in results)
    print("\n" + ("=" * 60))
    if all_pass:
        print("ALL VALIDATIONS PASSED")
        print("=" * 60 + "\n")
        return 0
    else:
        print("SOME VALIDATIONS FAILED")
        print("=" * 60 + "\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
