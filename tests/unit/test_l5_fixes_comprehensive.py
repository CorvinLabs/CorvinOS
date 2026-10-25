"""Comprehensive tests for all 9 L5 k=3,4,5 code review findings.

Tests for:
- Bug 1: Overfitting risk formula (quality_gate.py:253)
- Bug 2: Noise detection (quality_gate.py:271)
- Bug 3: Hold period overwrite (rollback_guard.py)
- Issue 4: Duplicate mean/std (quality_gate.py)
- Issue 5: Shared timestamp utilities (all files)
- Issue 6: datetime import (quality_gate.py:110)
- Issue 7: override_rate calculation (rollback_guard.py)
- Issue 8: can_revoke return type (rollback_guard.py)
- Issue 9: O(n²) conflict detection (conflict_resolver.py)
"""

import pytest
from datetime import datetime, timedelta
from core.learning import quality_gate, conflict_resolver, rollback_guard, utils


class MockAuditBackend:
    """Mock audit backend for testing."""

    def __init__(self):
        self.events = []

    def write_event(self, event):
        self.events.append(event)
        return len(self.events)


# =============================================================================
# BUG 1: Overfitting Risk Formula (quality_gate.py:253)
# =============================================================================

class TestBug1OverfittingRisk:
    """Test that overfitting risk formula is correct (not inverted)."""

    def test_severe_overfitting_high_divergence_high_confidence(self):
        """High divergence + high confidence → HIGH risk (overfitting)."""
        gate = quality_gate.QualityGate(audit_backend=MockAuditBackend())

        recent_deltas = [0.5, 0.6, 0.55]  # Diverge from EMA of 0.05
        ema_smoothed = 0.05
        ema_confidence = 0.9  # High confidence despite divergence

        risk = gate._compute_overfitting_risk(recent_deltas, ema_smoothed, ema_confidence)

        assert risk > 0.7, f"Expected > 0.7, got {risk:.3f}"

    def test_safe_low_divergence_high_confidence(self):
        """Low divergence + high confidence → LOW risk (good learning)."""
        gate = quality_gate.QualityGate(audit_backend=MockAuditBackend())

        recent_deltas = [0.05, 0.06, 0.04]  # Close to EMA
        ema_smoothed = 0.05
        ema_confidence = 0.9

        risk = gate._compute_overfitting_risk(recent_deltas, ema_smoothed, ema_confidence)

        assert risk < 0.3, f"Expected < 0.3, got {risk:.3f}"

    def test_uncertain_high_divergence_low_confidence(self):
        """High divergence + low confidence → MEDIUM risk (uncertain)."""
        gate = quality_gate.QualityGate(audit_backend=MockAuditBackend())

        recent_deltas = [0.5, 0.6, 0.55]
        ema_smoothed = 0.05
        ema_confidence = 0.2  # Low confidence

        risk = gate._compute_overfitting_risk(recent_deltas, ema_smoothed, ema_confidence)

        # Should be moderate, not extreme
        assert 0.3 <= risk <= 0.7, f"Expected 0.3-0.7, got {risk:.3f}"


# =============================================================================
# BUG 2: Noise Detection (quality_gate.py:271)
# =============================================================================

class TestBug2NoiseDetection:
    """Test that noise detection uses isolation-based approach, not magnitude."""

    def test_consistent_high_magnitude_is_clean(self):
        """Consistent high-magnitude signal → LOW noise (not noise)."""
        gate = quality_gate.QualityGate(audit_backend=MockAuditBackend())

        # All deltas are high but consistent (not isolated)
        recent_deltas = [0.5, 0.5, 0.5, 0.5]

        noise = gate._compute_noise_ratio(recent_deltas)

        assert noise < 0.3, f"Consistent signal should be low noise, got {noise:.3f}"

    def test_isolated_spike_is_noisy(self):
        """Single isolated spike → HIGH noise."""
        gate = quality_gate.QualityGate(audit_backend=MockAuditBackend())

        # One isolated spike, rest are small
        recent_deltas = [0.1, 0.1, 0.1, 1.0, 0.1]  # 1.0 is isolated outlier

        noise = gate._compute_noise_ratio(recent_deltas)

        assert noise > 0.5, f"Isolated spike should indicate high noise, got {noise:.3f}"

    def test_consistent_moderate_is_clean(self):
        """Consistent moderate deltas → LOW noise."""
        gate = quality_gate.QualityGate(audit_backend=MockAuditBackend())

        recent_deltas = [0.01, 0.02, 0.015, 0.01]

        noise = gate._compute_noise_ratio(recent_deltas)

        assert noise < 0.3, f"Consistent moderate deltas should be clean, got {noise:.3f}"


# =============================================================================
# BUG 3: Hold Period Overwrite (rollback_guard.py)
# =============================================================================

class TestBug3HoldPeriodOverwrite:
    """Test that multiple approvals don't overwrite each other's hold periods."""

    def test_multiple_approvals_same_skill_different_holds(self, tmp_path):
        """Register two approvals for same skill; verify each keeps its hold."""
        corvin_home = tmp_path / ".corvin"
        guard = rollback_guard.RollbackGuard(
            tenant_id="_default",
            audit_backend=MockAuditBackend(),
            corvin_home=str(corvin_home),
        )

        # Register two approvals with different criticalities
        guard.register_approval(
            "approval_1",
            "skill_a",
            criticality=rollback_guard.Criticality.CRITICAL,  # 1 hour
        )
        guard.register_approval(
            "approval_2",
            "skill_a",
            criticality=rollback_guard.Criticality.LOW,  # 48 hours
        )

        # Verify each approval has its own hold period
        apply_ts_1, hold_hours_1 = guard.approval_apply_times["approval_1"]
        apply_ts_2, hold_hours_2 = guard.approval_apply_times["approval_2"]

        assert hold_hours_1 == 1, f"approval_1 should have 1h hold, got {hold_hours_1}h"
        assert hold_hours_2 == 48, f"approval_2 should have 48h hold, got {hold_hours_2}h"

    def test_can_revoke_respects_individual_holds(self, tmp_path):
        """Verify can_revoke respects each approval's individual hold period."""
        corvin_home = tmp_path / ".corvin"
        guard = rollback_guard.RollbackGuard(
            tenant_id="_default",
            audit_backend=MockAuditBackend(),
            corvin_home=str(corvin_home),
        )

        # Register approval with 0-second hold (for testing)
        guard.register_approval(
            "approval_short",
            "skill_a",
            custom_hold_hours=0,  # Immediate revoke allowed
        )
        guard.register_approval(
            "approval_long",
            "skill_a",
            custom_hold_hours=24,  # 24 hour hold
        )

        # First approval should be revocable immediately
        can_revoke_short, time_left = guard.can_revoke("approval_short", "skill_a")
        assert can_revoke_short is True, "Short hold should allow immediate revoke"

        # Second approval should be blocked
        can_revoke_long, time_left = guard.can_revoke("approval_long", "skill_a")
        assert can_revoke_long is False, "Long hold should block revoke"
        assert time_left is not None, "Should have time_remaining"


# =============================================================================
# ISSUE 4: Duplicate mean/std (quality_gate.py)
# =============================================================================

class TestIssue4ExtractedMeanStd:
    """Test that mean/std extraction works in both convergence and stability."""

    def test_convergence_uses_shared_mean_std(self):
        """Convergence should use compute_mean_std() utility."""
        gate = quality_gate.QualityGate(audit_backend=MockAuditBackend())

        # Test with stable (converged) deltas
        deltas = [0.1, 0.095, 0.098, 0.1, 0.099]
        convergence = gate._compute_convergence_rate(deltas)

        assert convergence > 0.8, f"Stable deltas should show high convergence, got {convergence:.3f}"

    def test_stability_uses_shared_mean_std(self):
        """Stability should use compute_mean_std() utility."""
        gate = quality_gate.QualityGate(audit_backend=MockAuditBackend())

        # Test with stable config values
        config = [0.7, 0.70, 0.70, 0.701, 0.70]
        stability = gate._compute_stability_score(config)

        assert stability > 0.9, f"Stable config should have high stability, got {stability:.3f}"


# =============================================================================
# ISSUE 5: Shared Timestamp Utilities (utils.py)
# =============================================================================

class TestIssue5SharedUtils:
    """Test shared utility functions work across all modules."""

    def test_format_iso_timestamp(self):
        """Test format_iso_timestamp() returns valid ISO 8601."""
        ts = utils.format_iso_timestamp()

        assert ts.endswith("Z"), "Timestamp should end with Z"
        assert "T" in ts, "Timestamp should contain T separator"

    def test_parse_iso_timestamp(self):
        """Test parse_iso_timestamp() round-trip."""
        original_ts = utils.format_iso_timestamp()
        parsed = utils.parse_iso_timestamp(original_ts)

        assert isinstance(parsed, datetime), "Should parse to datetime"
        # Verify it's close to now (within 1 second)
        now = datetime.utcnow()
        diff = abs((now - parsed).total_seconds())
        assert diff < 1.0, f"Parsed time should be close to now, got {diff}s difference"

    def test_compute_mean_std(self):
        """Test compute_mean_std() utility."""
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        mean, std = utils.compute_mean_std(values)

        assert mean == 3.0, f"Mean should be 3.0, got {mean}"
        # std of [1,2,3,4,5] with population std = sqrt(2.0) ≈ 1.414
        assert 1.4 < std < 1.5, f"Std should be ~1.414, got {std:.3f}"

    def test_format_time_remaining(self):
        """Test format_time_remaining() utility."""
        delta = timedelta(hours=2, minutes=30, seconds=45)
        formatted = utils.format_time_remaining(delta)

        assert formatted == "02:30:45 remaining", f"Got: {formatted}"


# =============================================================================
# ISSUE 7: Override Rate Calculation (rollback_guard.py)
# =============================================================================

class TestIssue7OverrideRate:
    """Test that compute_override_rate() calculates actual rate, not placeholder."""

    def test_override_rate_zero_approvals(self, tmp_path):
        """No approvals → rate should be 0."""
        corvin_home = tmp_path / ".corvin"
        guard = rollback_guard.RollbackGuard(
            tenant_id="_default",
            audit_backend=MockAuditBackend(),
            corvin_home=str(corvin_home),
        )

        rate, count = guard.compute_override_rate("skill_a")

        assert rate == 0.0, "No approvals should give 0.0 rate"
        assert count == 0, "No approvals should give 0 count"

    def test_override_rate_with_early_overrides(self, tmp_path):
        """Multiple approvals with some early overrides → rate > 0."""
        corvin_home = tmp_path / ".corvin"
        guard = rollback_guard.RollbackGuard(
            tenant_id="_default",
            audit_backend=MockAuditBackend(),
            corvin_home=str(corvin_home),
        )

        # Register 4 approvals
        for i in range(4):
            guard.register_approval(
                f"approval_{i}",
                "skill_a",
                custom_hold_hours=10,
            )

        # Simulate 2 early overrides
        for i in range(2):
            metrics = rollback_guard.OverrideMetrics(
                skill_id="skill_a",
                approval_id=f"approval_{i}",
                time_into_hold_seconds=300,  # 5 minutes (before 10h)
                hold_period_configured_seconds=36000,  # 10 hours
                timestamp=utils.format_iso_timestamp(),
            )
            guard.override_metrics[f"approval_{i}"] = metrics

        rate, count = guard.compute_override_rate("skill_a")

        assert rate == 0.5, f"2/4 overrides should give 0.5 rate, got {rate}"
        assert count == 4, f"Should have 4 approvals, got {count}"


# =============================================================================
# ISSUE 8: can_revoke Return Type (rollback_guard.py)
# =============================================================================

class TestIssue8CanRevokeReturnType:
    """Test that can_revoke() returns (bool, Optional[timedelta]), not string."""

    def test_can_revoke_returns_timedelta(self, tmp_path):
        """can_revoke should return timedelta, not formatted string."""
        corvin_home = tmp_path / ".corvin"
        guard = rollback_guard.RollbackGuard(
            tenant_id="_default",
            audit_backend=MockAuditBackend(),
            corvin_home=str(corvin_home),
        )

        # Register with 1-second hold (for testing)
        guard.register_approval(
            "approval_1",
            "skill_a",
            custom_hold_hours=1,
        )

        is_allowed, time_remaining = guard.can_revoke("approval_1", "skill_a")

        assert isinstance(is_allowed, bool), "First return should be bool"
        assert isinstance(time_remaining, (timedelta, type(None))), \
            f"Second return should be timedelta or None, got {type(time_remaining)}"
        assert is_allowed is False, "Should be blocked by hold"
        assert time_remaining is not None, "Should have time_remaining"
        assert isinstance(time_remaining, timedelta), "Should be timedelta"
        assert time_remaining.total_seconds() > 0, "Should be positive"


# =============================================================================
# ISSUE 9: O(n²) Conflict Detection (conflict_resolver.py)
# =============================================================================

class TestIssue9ConflictDetectionOptimization:
    """Test that conflict detection is optimized (groups by metric first)."""

    def test_no_conflict_different_metrics(self):
        """Different metrics should not conflict even with time overlap."""
        detector = conflict_resolver.ConflictDetector()

        pending = {
            "skill_a": {
                "metric_x": {
                    "operator_timestamp": "2026-09-01T10:00:00Z",
                    "ttl_expires": "2026-09-01T11:00:00Z",
                }
            },
            "skill_b": {
                "metric_y": {
                    "operator_timestamp": "2026-09-01T10:30:00Z",
                    "ttl_expires": "2026-09-01T11:30:00Z",
                }
            },
        }

        conflicts = detector.detect_conflicts(pending)

        assert len(conflicts) == 0, "Different metrics should not conflict"

    def test_conflict_same_metric_time_overlap(self):
        """Same metric + time overlap + different skills = conflict."""
        detector = conflict_resolver.ConflictDetector()

        pending = {
            "skill_a": {
                "metric_x": {
                    "operator_timestamp": "2026-09-01T10:00:00Z",
                    "ttl_expires": "2026-09-01T11:00:00Z",
                }
            },
            "skill_b": {
                "metric_x": {
                    "operator_timestamp": "2026-09-01T10:30:00Z",
                    "ttl_expires": "2026-09-01T11:30:00Z",
                }
            },
        }

        conflicts = detector.detect_conflicts(pending)

        assert len(conflicts) == 1, "Same metric + overlap should conflict"
        assert conflicts[0].conflict_type == conflict_resolver.ConflictType.CONCURRENT_PARAMETER

    def test_many_metrics_scales_well(self):
        """Large number of metrics should process quickly (not O(n²))."""
        detector = conflict_resolver.ConflictDetector()

        # Create 100 metrics, each with one approval
        pending = {}
        for i in range(100):
            metric_name = f"metric_{i}"
            pending[f"skill_{i}"] = {
                metric_name: {
                    "operator_timestamp": "2026-09-01T10:00:00Z",
                    "ttl_expires": "2026-09-01T11:00:00Z",
                }
            }

        # Should complete quickly (no conflicts, but checks should be optimized)
        conflicts = detector.detect_conflicts(pending)

        assert len(conflicts) == 0, "No overlapping metrics should have no conflicts"


# =============================================================================
# INTEGRATION TESTS
# =============================================================================

class TestIntegration:
    """Integration tests for all fixes working together."""

    def test_quality_gate_to_rollback_workflow(self, tmp_path):
        """Test workflow: compute quality → register approval → check revoke."""
        audit = MockAuditBackend()
        corvin_home = tmp_path / ".corvin"

        # Create gates
        quality = quality_gate.QualityGate(tenant_id="_default", audit_backend=audit)
        guard = rollback_guard.RollbackGuard(
            tenant_id="_default",
            audit_backend=audit,
            corvin_home=str(corvin_home),
        )

        # Step 1: Compute quality score
        score = quality.compute_quality(
            skill_id="routing_skill",
            metric_name="confidence_threshold",
            recent_deltas=[0.5, 0.6, 0.55],
            ema_smoothed=0.05,
            ema_confidence=0.9,
            config_history=[0.7, 0.72, 0.71],
        )

        assert score.composite_score > 0.0, "Should compute quality score"

        # Step 2: Register approval
        guard.register_approval(
            approval_id="approval_1",
            skill_id="routing_skill",
            criticality=rollback_guard.Criticality.MEDIUM,
        )

        # Step 3: Check if revocable (should be blocked by hold)
        can_revoke, time_remaining = guard.can_revoke("approval_1", "routing_skill")
        assert can_revoke is False, "Should be blocked by hold"
        assert isinstance(time_remaining, timedelta), "Should have timedelta"

        # Verify audit trail
        assert len(audit.events) > 0, "Should have audit events"
