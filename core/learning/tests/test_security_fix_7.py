"""
Security Fix #7: Oscillation Attack Mitigation Tests

Tests for low-pass filtering (EMA) and frequency detection to prevent
oscillation attacks via DAG resonance.

Test coverage:
  - test_ema_filter_smooths_delta: Verify EMA smoothing works correctly
  - test_frequency_detection_normal: Normal update rate should not trigger oscillation
  - test_frequency_detection_oscillation: High frequency triggers detection
  - test_oscillation_clamps_learning_rate: Learning rate reduced when oscillating
  - test_oscillation_timeout: Oscillation clamp expires after timeout
  - test_weight_oscillation_audit_logged: Audit events logged for oscillations
"""

import pytest
import time
from unittest.mock import Mock, MagicMock, call
from datetime import datetime
from core.learning.weight_updater import (
    WeightUpdater,
    WeightUpdateRecord,
    OscillationState,
)


class MockAuditBackend:
    """Mock audit backend for testing."""

    def __init__(self):
        self.events = []

    def write_event(self, event):
        self.events.append(event)


class TestEMAFilterSmoothsDelta:
    """Test EMA filter smoothing of weight deltas."""

    def test_ema_filter_smooths_alternating_deltas(self):
        """Test that alternating deltas [1, -1, 1, -1] are smoothed to expected values."""
        updater = WeightUpdater(ema_alpha=0.3)
        audit = MockAuditBackend()

        # Apply alternating deltas: [1, -1, 1, -1]
        deltas = [1.0, -1.0, 1.0, -1.0]
        filtered_deltas = []

        for delta in deltas:
            record = updater.update_weight(
                weight_id='test_weight',
                delta=delta,
                base_learning_rate=1.0,  # LR=1.0 to see pure EMA delta
                audit_backend=audit,
            )
            filtered_deltas.append(record.ema_filtered_delta)

        # Expected EMA progression with alpha=0.3:
        # ema[0] = 0.3*1 + 0.7*0 = 0.3
        # ema[1] = 0.3*(-1) + 0.7*0.3 = -0.3 + 0.21 = -0.09
        # ema[2] = 0.3*1 + 0.7*(-0.09) = 0.3 - 0.063 = 0.237
        # ema[3] = 0.3*(-1) + 0.7*0.237 = -0.3 + 0.1659 = -0.1341
        expected = [0.3, -0.09, 0.237, -0.1341]

        for i, (filtered, expected_val) in enumerate(zip(filtered_deltas, expected)):
            assert abs(filtered - expected_val) < 0.01, (
                f"Delta {i}: filtered={filtered}, expected={expected_val}"
            )

    def test_ema_converges_to_constant_input(self):
        """Test that EMA converges to constant input value."""
        updater = WeightUpdater(ema_alpha=0.3)
        audit = MockAuditBackend()

        # Apply constant delta = 2.0 repeatedly
        for _ in range(20):
            record = updater.update_weight(
                weight_id='test_weight',
                delta=2.0,
                base_learning_rate=1.0,
                audit_backend=audit,
            )

        # After many iterations, EMA should converge close to 2.0
        final_ema = record.ema_filtered_delta
        assert abs(final_ema - 2.0) < 0.1, (
            f"EMA should converge to 2.0, got {final_ema}"
        )

    def test_ema_filter_reduces_noise(self):
        """Test that EMA reduces high-frequency noise."""
        updater = WeightUpdater(ema_alpha=0.3)
        audit = MockAuditBackend()

        # Add noise: base=1.0, noise amplitude=0.8
        base_value = 1.0
        noise = 0.8
        updates = [
            base_value + noise,
            base_value - noise,
            base_value + noise,
            base_value - noise,
        ]

        filtered_values = []
        for delta in updates:
            record = updater.update_weight(
                weight_id='noise_weight',
                delta=delta,
                base_learning_rate=1.0,
                audit_backend=audit,
            )
            filtered_values.append(record.ema_filtered_delta)

        # Verify that oscillations are dampened
        # Unfiltered would have max-min = 1.6
        unfiltered_swing = max(updates) - min(updates)
        filtered_swing = max(filtered_values) - min(filtered_values)

        assert filtered_swing < unfiltered_swing, (
            f"EMA should reduce noise: unfiltered swing={unfiltered_swing}, "
            f"filtered swing={filtered_swing}"
        )


class TestFrequencyDetectionNormal:
    """Test that normal update rates don't trigger oscillation detection."""

    def test_normal_update_rate_no_detection(self):
        """Test that 1 update per minute does not trigger oscillation."""
        updater = WeightUpdater(
            oscillation_frequency_threshold=5.0,  # 5 changes/minute
        )

        # Simulate updates at 1 per minute (normal)
        for i in range(3):
            # Wait 61 seconds between updates (simulated)
            updater.weights['normal_weight'] = OscillationState(
                weight_id='normal_weight',
                last_update_time=time.time() + (i * 61),
            )
            record = updater.update_weight(
                weight_id='normal_weight',
                delta=0.1,
                audit_backend=MockAuditBackend(),
            )
            assert not record.oscillation_detected, (
                f"Update {i}: Normal rate should not trigger oscillation"
            )

    def test_two_updates_per_minute_safe(self):
        """Test that 2 updates/minute is below threshold."""
        updater = WeightUpdater(oscillation_frequency_threshold=5.0)
        audit = MockAuditBackend()

        # Simulate 2 rapid updates, then wait
        current_time = time.time()
        updater.weights['safe_weight'] = OscillationState(weight_id='safe_weight')

        record1 = updater.update_weight(
            weight_id='safe_weight',
            delta=0.1,
            audit_backend=audit,
        )
        assert not record1.oscillation_detected

        # Simulate second update within same minute
        updater.weights['safe_weight'].update_times_window.append(current_time + 1)
        record2 = updater.update_weight(
            weight_id='safe_weight',
            delta=0.1,
            audit_backend=audit,
        )
        assert not record2.oscillation_detected


class TestFrequencyDetectionOscillation:
    """Test that high update frequencies trigger oscillation detection."""

    def test_high_frequency_triggers_detection(self):
        """Test that >5 updates/minute triggers oscillation."""
        updater = WeightUpdater(
            oscillation_frequency_threshold=5.0,  # 5 changes/minute
        )
        audit = MockAuditBackend()

        # Simulate 10 updates rapidly (within 1 minute)
        current_time = time.time()
        updater.weights['osc_weight'] = OscillationState(weight_id='osc_weight')

        # Add 10 updates to window (simulating rapid updates)
        for i in range(10):
            updater.weights['osc_weight'].update_times_window.append(
                current_time + (i * 0.1)  # 10 updates in 1 second
            )

        # Next update should detect oscillation
        record = updater.update_weight(
            weight_id='osc_weight',
            delta=0.1,
            audit_backend=audit,
        )

        assert record.oscillation_detected, (
            "High frequency (10 updates in 1 second) should trigger oscillation detection"
        )

    def test_oscillation_detected_exactly_at_threshold(self):
        """Test oscillation detection at exact threshold boundary."""
        updater = WeightUpdater(oscillation_frequency_threshold=5.0)
        audit = MockAuditBackend()

        current_time = time.time()
        updater.weights['boundary_weight'] = OscillationState(weight_id='boundary_weight')

        # Seed 4 prior updates. `update_weight()` records its OWN update in the
        # window (documented side effect of _detect_frequency_oscillation), so the
        # call below is the 5th entry — landing exactly ON the threshold.
        for i in range(4):
            updater.weights['boundary_weight'].update_times_window.append(
                current_time + (i * 0.1)
            )

        record = updater.update_weight(
            weight_id='boundary_weight',
            delta=0.1,
            audit_backend=audit,
        )

        # 5 updates is at threshold, not above, so should NOT trigger
        assert not record.oscillation_detected, (
            "Exactly at threshold (5 updates) should not trigger detection"
        )

        # The next update makes 6 updates in the window — above the threshold.
        record2 = updater.update_weight(
            weight_id='boundary_weight',
            delta=0.1,
            audit_backend=audit,
        )

        assert record2.oscillation_detected, (
            "Above threshold (6 updates) should trigger detection"
        )


class TestOscillationClampsLearningRate:
    """Test that oscillation causes learning rate to be clamped."""

    def test_oscillation_clamps_lr_to_0_1x(self):
        """Test that when oscillating, LR is clamped to 0.1x."""
        updater = WeightUpdater(
            oscillation_frequency_threshold=5.0,
            learning_rate_clamp_factor=0.1,
        )
        audit = MockAuditBackend()

        base_lr = 0.1
        current_time = time.time()
        updater.weights['clamp_weight'] = OscillationState(weight_id='clamp_weight')

        # Trigger oscillation
        for i in range(6):
            updater.weights['clamp_weight'].update_times_window.append(
                current_time + (i * 0.1)
            )

        record = updater.update_weight(
            weight_id='clamp_weight',
            delta=1.0,
            base_learning_rate=base_lr,
            audit_backend=audit,
        )

        # Effective LR should be 0.1 * 0.1 = 0.01
        expected_clamped_lr = base_lr * 0.1
        assert record.learning_rate_applied == expected_clamped_lr, (
            f"When oscillating, LR should be clamped to {expected_clamped_lr}, "
            f"got {record.learning_rate_applied}"
        )

    def test_delta_reduced_by_clamped_lr(self):
        """Test that clamped LR reduces the applied delta."""
        updater = WeightUpdater(
            oscillation_frequency_threshold=5.0,
            learning_rate_clamp_factor=0.1,
        )
        audit = MockAuditBackend()

        current_time = time.time()
        updater.weights['clamp_weight'] = OscillationState(weight_id='clamp_weight')

        # Trigger oscillation with 10 rapid updates
        for i in range(10):
            updater.weights['clamp_weight'].update_times_window.append(
                current_time + (i * 0.1)
            )

        base_lr = 0.1
        delta = 1.0

        record = updater.update_weight(
            weight_id='clamp_weight',
            delta=delta,
            base_learning_rate=base_lr,
            audit_backend=audit,
        )

        # Applied delta should be: delta * (LR_base * clamp_factor)
        # Since EMA filtered delta ≈ delta and LR_clamped ≈ 0.01
        expected_max_new_value = delta * base_lr * 0.1
        assert abs(record.new_value - expected_max_new_value) < 0.05, (
            f"Applied new_value should be ~{expected_max_new_value}, "
            f"got {record.new_value}"
        )


class TestOscillationTimeout:
    """Test that oscillation clamp expires after timeout."""

    def test_oscillation_timeout_expires(self):
        """Test that oscillation clamp is cleared after timeout duration."""
        clamp_duration = 1.0  # 1 second for test speed
        updater = WeightUpdater(
            oscillation_frequency_threshold=5.0,
            oscillation_clamp_duration_seconds=clamp_duration,
        )
        audit = MockAuditBackend()

        # Trigger oscillation
        current_time = time.time()
        updater.weights['timeout_weight'] = OscillationState(weight_id='timeout_weight')

        for i in range(6):
            updater.weights['timeout_weight'].update_times_window.append(
                current_time + (i * 0.1)
            )

        record1 = updater.update_weight(
            weight_id='timeout_weight',
            delta=0.1,
            audit_backend=audit,
        )
        assert record1.oscillation_detected

        # Verify clamp is active
        assert updater.is_oscillation_clamped('timeout_weight')

        # Wait for clamp to expire
        time.sleep(clamp_duration + 0.1)

        # Clamp should now be expired
        assert not updater.is_oscillation_clamped('timeout_weight')

    def test_oscillation_clamp_duration_respected(self):
        """Test that clamp_until timestamp is set correctly."""
        clamp_duration = 600.0
        updater = WeightUpdater(
            oscillation_frequency_threshold=5.0,
            oscillation_clamp_duration_seconds=clamp_duration,
        )
        audit = MockAuditBackend()

        current_time = time.time()
        updater.weights['duration_weight'] = OscillationState(weight_id='duration_weight')

        # Trigger oscillation
        for i in range(6):
            updater.weights['duration_weight'].update_times_window.append(
                current_time + (i * 0.1)
            )

        updater.update_weight(
            weight_id='duration_weight',
            delta=0.1,
            audit_backend=audit,
        )

        state = updater.weights['duration_weight']
        assert state.oscillation_clamp_until is not None

        # Clamp_until should be approximately current_time + clamp_duration
        time_until_expiry = state.oscillation_clamp_until - current_time
        assert abs(time_until_expiry - clamp_duration) < 1.0, (
            f"Clamp duration should be ~{clamp_duration}, "
            f"got {time_until_expiry}"
        )


class TestWeightOscillationAuditLogged:
    """Test that oscillation events are properly audit-logged."""

    def test_oscillation_audit_event_logged(self):
        """Test that weight_oscillation_detected event is logged when oscillating."""
        updater = WeightUpdater(oscillation_frequency_threshold=5.0)
        audit = MockAuditBackend()

        current_time = time.time()
        updater.weights['audit_weight'] = OscillationState(weight_id='audit_weight')

        # Trigger oscillation
        for i in range(6):
            updater.weights['audit_weight'].update_times_window.append(
                current_time + (i * 0.1)
            )

        updater.update_weight(
            weight_id='audit_weight',
            delta=0.1,
            audit_backend=audit,
        )

        # Find the oscillation event in audit logs
        osc_events = [e for e in audit.events if e['event_type'] == 'weight_oscillation_detected']
        assert len(osc_events) > 0, "Should log weight_oscillation_detected event"

        osc_event = osc_events[0]
        assert osc_event['weight_id'] == 'audit_weight'
        assert osc_event['clamp_duration_seconds'] == 600.0

    def test_weight_updated_audit_event_includes_oscillation_flag(self):
        """Test that weight_updated event includes oscillation_detected flag."""
        updater = WeightUpdater(oscillation_frequency_threshold=5.0)
        audit = MockAuditBackend()

        current_time = time.time()
        updater.weights['audit_weight2'] = OscillationState(weight_id='audit_weight2')

        # Trigger oscillation
        for i in range(6):
            updater.weights['audit_weight2'].update_times_window.append(
                current_time + (i * 0.1)
            )

        updater.update_weight(
            weight_id='audit_weight2',
            delta=0.1,
            audit_backend=audit,
        )

        # Find weight_updated event
        update_events = [e for e in audit.events if e['event_type'] == 'weight_updated']
        assert len(update_events) > 0

        update_event = update_events[0]
        assert 'oscillation_detected' in update_event
        assert update_event['oscillation_detected'] is True

    def test_audit_event_includes_lr_information(self):
        """Test that audit event logs both base and effective learning rates."""
        updater = WeightUpdater(oscillation_frequency_threshold=5.0)
        audit = MockAuditBackend()

        current_time = time.time()
        updater.weights['audit_lr'] = OscillationState(weight_id='audit_lr')

        # Trigger oscillation
        for i in range(6):
            updater.weights['audit_lr'].update_times_window.append(
                current_time + (i * 0.1)
            )

        base_lr = 0.1
        updater.update_weight(
            weight_id='audit_lr',
            delta=0.1,
            base_learning_rate=base_lr,
            audit_backend=audit,
        )

        # Check audit event
        update_events = [e for e in audit.events if e['event_type'] == 'weight_updated']
        update_event = update_events[0]

        assert update_event['base_learning_rate'] == base_lr
        assert update_event['effective_learning_rate'] < base_lr, (
            "Effective LR should be clamped lower when oscillating"
        )

    def test_audit_fail_closed_on_error(self):
        """Test that weight update is rejected if audit backend fails."""
        updater = WeightUpdater(oscillation_frequency_threshold=5.0)

        # Mock audit backend that raises an exception
        failing_audit = Mock()
        failing_audit.write_event.side_effect = RuntimeError("Audit write failed")

        with pytest.raises(RuntimeError) as exc_info:
            updater.update_weight(
                weight_id='fail_weight',
                delta=0.1,
                audit_backend=failing_audit,
            )

        assert "Audit write failed" in str(exc_info.value)
        assert "Weight update rejected" in str(exc_info.value)


class TestOscillationStatusAndHistory:
    """Test oscillation status tracking and history."""

    def test_get_oscillation_status_when_not_oscillating(self):
        """Test status report when weight is not oscillating."""
        updater = WeightUpdater()

        status = updater.get_oscillation_status('nonexistent_weight')
        assert status['found'] is False

        # Add a normal weight
        updater.weights['normal_weight'] = OscillationState(weight_id='normal_weight')
        status = updater.get_oscillation_status('normal_weight')
        assert status['found'] is True
        assert status['is_clamped'] is False

    def test_get_oscillation_status_when_oscillating(self):
        """Test status report when weight is oscillating."""
        updater = WeightUpdater(oscillation_frequency_threshold=5.0)
        audit = MockAuditBackend()

        current_time = time.time()
        updater.weights['osc_status_weight'] = OscillationState(weight_id='osc_status_weight')

        # Trigger oscillation
        for i in range(6):
            updater.weights['osc_status_weight'].update_times_window.append(
                current_time + (i * 0.1)
            )

        updater.update_weight(
            weight_id='osc_status_weight',
            delta=0.1,
            audit_backend=audit,
        )

        status = updater.get_oscillation_status('osc_status_weight')
        assert status['found'] is True
        assert status['is_clamped'] is True
        assert status['updates_per_minute'] > 5

    def test_get_update_history_filtered(self):
        """Test that update history can be filtered by weight_id."""
        updater = WeightUpdater()
        audit = MockAuditBackend()

        # Add updates for different weights
        updater.update_weight('weight_a', 0.1, audit_backend=audit)
        updater.update_weight('weight_b', 0.2, audit_backend=audit)
        updater.update_weight('weight_a', 0.3, audit_backend=audit)

        # Filter history
        history_a = updater.get_update_history('weight_a')
        assert len(history_a) == 2
        assert all(r.weight_id == 'weight_a' for r in history_a)

        history_b = updater.get_update_history('weight_b')
        assert len(history_b) == 1
        assert history_b[0].weight_id == 'weight_b'


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
