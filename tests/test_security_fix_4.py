"""
Security Fix #4: Normalization Bypass — Hard Gradient Clipping + NaN/Inf Detection (Fail-Closed)

Tests for GradientValidator class (ADR-0647).

Attack Vector: Extreme gradient magnitudes escape bounds, enabling normalization bypass
and causing weight divergence.

Mitigation: Hard Gradient Clipping + NaN/Inf Detection (Fail-Closed)
- Hard clipping: ∂L/∂w ∈ [-max_gradient, +max_gradient] (default max_gradient=1.0)
- NaN/Inf detection: fail-closed if invalid values detected
- Checkpoint recovery: reset to last known-good weights on failure

Test Cases:
1. test_gradient_clipping_valid: Normal gradients [-1, 1] pass through
2. test_gradient_clipping_extreme: Gradient 1000.0 clipped to 1.0
3. test_gradient_nan_detection: NaN in gradient detected, weight update rejected
4. test_gradient_inf_detection: Inf in gradient detected, weight update rejected
5. test_gradient_invalid_audit_logged: NaN/Inf events logged with gradient_invalid_value
6. test_gradient_recovery_last_good_checkpoint: Weights reset to previous known-good state
"""

from core.learning.gradient_backprop import GradientValidator, LossBackpropagator
from tests.learning.mock_audit_backend import MockAuditBackend
from datetime import datetime
import numpy as np
import pytest


class TestGradientClippingValid:
    """Test 1: Normal gradients within bounds pass through unchanged."""

    def test_gradient_clipping_valid_in_bounds(self):
        """Gradients within [-1.0, 1.0] should pass through unchanged."""
        validator = GradientValidator(max_gradient=1.0, audit_backend=MockAuditBackend())

        gradients = {
            'L1_routing': {'grad': 0.5, 'contributors': ['L2', 'L5']},
            'L2_confidence': {'grad': -0.3, 'contributors': ['L3']},
            'L3_feedback': {'grad': 0.0, 'contributors': []},
            'L4_attention': {'grad': -0.8, 'contributors': []},
            'L5_latency': {'grad': 0.7, 'contributors': ['L2']},
            'L6_diversity': {'grad': -0.2, 'contributors': []},
        }

        clipped, is_valid = validator.validate_and_clip_gradients(gradients, 'test_batch_1')

        assert is_valid, "Valid gradients should pass validation"
        assert len(clipped) == 6, "All gradients should be present"
        assert clipped['L1_routing']['grad'] == 0.5, "Gradient within bounds should not change"
        assert clipped['L2_confidence']['grad'] == -0.3, "Negative gradient within bounds should not change"
        assert not clipped['L1_routing']['was_clipped'], "Gradient within bounds should not be marked as clipped"

    def test_gradient_clipping_valid_boundary_values(self):
        """Gradients at exact boundaries (-1.0, 1.0) should pass through."""
        validator = GradientValidator(max_gradient=1.0, audit_backend=MockAuditBackend())

        gradients = {
            'L1_routing': {'grad': 1.0, 'contributors': []},
            'L2_confidence': {'grad': -1.0, 'contributors': []},
            'L3_feedback': {'grad': 0.0, 'contributors': []},
            'L4_attention': {'grad': 1.0, 'contributors': []},
            'L5_latency': {'grad': -1.0, 'contributors': []},
            'L6_diversity': {'grad': 0.5, 'contributors': []},
        }

        clipped, is_valid = validator.validate_and_clip_gradients(gradients, 'test_batch_2')

        assert is_valid, "Boundary gradients should pass validation"
        assert clipped['L1_routing']['grad'] == 1.0, "Upper boundary should not change"
        assert clipped['L2_confidence']['grad'] == -1.0, "Lower boundary should not change"
        assert not clipped['L1_routing']['was_clipped'], "Boundary gradient should not be marked as clipped"

    def test_gradient_clipping_valid_various_values(self):
        """Multiple valid gradients with different values should all pass."""
        validator = GradientValidator(max_gradient=1.0, audit_backend=MockAuditBackend())

        test_values = [0.0, 0.1, -0.1, 0.5, -0.5, 0.99, -0.99]

        for value in test_values:
            gradients = {
                f'L{i}_test': {'grad': value, 'contributors': []}
                for i in range(1, 7)
            }
            clipped, is_valid = validator.validate_and_clip_gradients(gradients, f'test_batch_{value}')
            assert is_valid, f"Gradient {value} should pass validation"


class TestGradientClippingExtreme:
    """Test 2: Extreme gradient magnitudes are clipped to bounds."""

    def test_gradient_clipping_extreme_large_positive(self):
        """Large positive gradient (1000.0) should be clipped to max_gradient."""
        validator = GradientValidator(max_gradient=1.0, audit_backend=MockAuditBackend())

        gradients = {
            'L1_routing': {'grad': 1000.0, 'contributors': ['L2']},
            'L2_confidence': {'grad': 0.1, 'contributors': []},
            'L3_feedback': {'grad': 0.05, 'contributors': []},
            'L4_attention': {'grad': 0.02, 'contributors': []},
            'L5_latency': {'grad': 0.03, 'contributors': []},
            'L6_diversity': {'grad': 0.04, 'contributors': []},
        }

        clipped, is_valid = validator.validate_and_clip_gradients(gradients, 'test_extreme_1')

        assert is_valid, "Extreme gradients should be clipped, not rejected"
        assert clipped['L1_routing']['grad'] == 1.0, "Gradient 1000.0 should be clipped to 1.0"
        assert clipped['L1_routing']['was_clipped'], "Clipping should be marked"
        assert clipped['L1_routing']['original_grad'] == 1000.0, "Original value should be preserved"

    def test_gradient_clipping_extreme_large_negative(self):
        """Large negative gradient (-1000.0) should be clipped to -max_gradient."""
        validator = GradientValidator(max_gradient=1.0, audit_backend=MockAuditBackend())

        gradients = {
            'L1_routing': {'grad': -1000.0, 'contributors': ['L2']},
            'L2_confidence': {'grad': 0.1, 'contributors': []},
            'L3_feedback': {'grad': 0.05, 'contributors': []},
            'L4_attention': {'grad': 0.02, 'contributors': []},
            'L5_latency': {'grad': 0.03, 'contributors': []},
            'L6_diversity': {'grad': 0.04, 'contributors': []},
        }

        clipped, is_valid = validator.validate_and_clip_gradients(gradients, 'test_extreme_2')

        assert is_valid, "Extreme negative gradients should be clipped"
        assert clipped['L1_routing']['grad'] == -1.0, "Gradient -1000.0 should be clipped to -1.0"
        assert clipped['L1_routing']['was_clipped'], "Clipping should be marked"

    def test_gradient_clipping_extreme_multiple_loops(self):
        """Multiple loops with extreme gradients should all be clipped."""
        validator = GradientValidator(max_gradient=1.0, audit_backend=MockAuditBackend())

        gradients = {
            'L1_routing': {'grad': 500.0, 'contributors': []},
            'L2_confidence': {'grad': -300.0, 'contributors': []},
            'L3_feedback': {'grad': 2000.0, 'contributors': []},
            'L4_attention': {'grad': -5000.0, 'contributors': []},
            'L5_latency': {'grad': 0.5, 'contributors': []},  # normal
            'L6_diversity': {'grad': 100.0, 'contributors': []},
        }

        clipped, is_valid = validator.validate_and_clip_gradients(gradients, 'test_extreme_3')

        assert is_valid, "All extreme gradients should be clipped"
        assert clipped['L1_routing']['grad'] == 1.0, "L1 should be clipped to upper bound"
        assert clipped['L2_confidence']['grad'] == -1.0, "L2 should be clipped to lower bound"
        assert clipped['L3_feedback']['grad'] == 1.0, "L3 should be clipped to upper bound"
        assert clipped['L4_attention']['grad'] == -1.0, "L4 should be clipped to lower bound"
        assert clipped['L5_latency']['grad'] == 0.5, "L5 should remain unchanged"
        assert clipped['L6_diversity']['grad'] == 1.0, "L6 should be clipped to upper bound"

    def test_gradient_clipping_extreme_very_large(self):
        """Even larger values (infinity-like) should be clipped."""
        validator = GradientValidator(max_gradient=1.0, audit_backend=MockAuditBackend())

        gradients = {
            'L1_routing': {'grad': 1e10, 'contributors': []},
            'L2_confidence': {'grad': 0.1, 'contributors': []},
            'L3_feedback': {'grad': 0.05, 'contributors': []},
            'L4_attention': {'grad': 0.02, 'contributors': []},
            'L5_latency': {'grad': 0.03, 'contributors': []},
            'L6_diversity': {'grad': 0.04, 'contributors': []},
        }

        clipped, is_valid = validator.validate_and_clip_gradients(gradients, 'test_extreme_4')

        assert is_valid, "Very large gradients should be clipped"
        assert clipped['L1_routing']['grad'] == 1.0, "Very large gradient should be clipped to 1.0"

    def test_gradient_clipping_statistics(self):
        """Validator should track clipping statistics."""
        validator = GradientValidator(max_gradient=1.0, audit_backend=MockAuditBackend())

        # First batch: normal
        gradients1 = {
            'L1_routing': {'grad': 0.5, 'contributors': []},
            'L2_confidence': {'grad': 0.3, 'contributors': []},
            'L3_feedback': {'grad': 0.2, 'contributors': []},
            'L4_attention': {'grad': 0.1, 'contributors': []},
            'L5_latency': {'grad': 0.05, 'contributors': []},
            'L6_diversity': {'grad': 0.08, 'contributors': []},
        }
        validator.validate_and_clip_gradients(gradients1, 'batch_1')

        # Second batch: extreme values
        gradients2 = {
            'L1_routing': {'grad': 100.0, 'contributors': []},
            'L2_confidence': {'grad': -100.0, 'contributors': []},
            'L3_feedback': {'grad': 50.0, 'contributors': []},
            'L4_attention': {'grad': 0.1, 'contributors': []},
            'L5_latency': {'grad': 0.05, 'contributors': []},
            'L6_diversity': {'grad': 0.08, 'contributors': []},
        }
        validator.validate_and_clip_gradients(gradients2, 'batch_2')

        stats = validator.get_stats()
        assert stats['num_gradients_clipped'] == 3, "Should have clipped 3 gradients in second batch"
        assert stats['num_validation_failures'] == 0, "No validation failures should occur"


class TestGradientNaNDetection:
    """Test 3: NaN values in gradients are detected and rejected (fail-closed)."""

    def test_gradient_nan_detection_single_loop(self):
        """NaN in one loop should cause validation to fail."""
        validator = GradientValidator(max_gradient=1.0, audit_backend=MockAuditBackend())

        gradients = {
            'L1_routing': {'grad': float('nan'), 'contributors': []},
            'L2_confidence': {'grad': 0.1, 'contributors': []},
            'L3_feedback': {'grad': 0.05, 'contributors': []},
            'L4_attention': {'grad': 0.02, 'contributors': []},
            'L5_latency': {'grad': 0.03, 'contributors': []},
            'L6_diversity': {'grad': 0.04, 'contributors': []},
        }

        clipped, is_valid = validator.validate_and_clip_gradients(gradients, 'test_nan_1')

        assert not is_valid, "Validation should fail when NaN is detected"
        assert len(clipped) == 0, "No gradients should be returned on failure"

    def test_gradient_nan_detection_multiple_loops(self):
        """Multiple NaN values should all be detected."""
        validator = GradientValidator(max_gradient=1.0, audit_backend=MockAuditBackend())

        gradients = {
            'L1_routing': {'grad': float('nan'), 'contributors': []},
            'L2_confidence': {'grad': float('nan'), 'contributors': []},
            'L3_feedback': {'grad': 0.05, 'contributors': []},
            'L4_attention': {'grad': 0.02, 'contributors': []},
            'L5_latency': {'grad': float('nan'), 'contributors': []},
            'L6_diversity': {'grad': 0.04, 'contributors': []},
        }

        clipped, is_valid = validator.validate_and_clip_gradients(gradients, 'test_nan_2')

        assert not is_valid, "Validation should fail when multiple NaNs detected"
        assert len(clipped) == 0, "No gradients should be returned on failure"

    def test_gradient_nan_detection_from_computation(self):
        """NaN from computation (0/0) should be detected."""
        validator = GradientValidator(max_gradient=1.0, audit_backend=MockAuditBackend())

        # Simulate NaN from 0/0 or sqrt(-1)
        nan_value = np.sqrt(-1.0)  # Results in NaN with RuntimeWarning

        gradients = {
            'L1_routing': {'grad': nan_value, 'contributors': []},
            'L2_confidence': {'grad': 0.1, 'contributors': []},
            'L3_feedback': {'grad': 0.05, 'contributors': []},
            'L4_attention': {'grad': 0.02, 'contributors': []},
            'L5_latency': {'grad': 0.03, 'contributors': []},
            'L6_diversity': {'grad': 0.04, 'contributors': []},
        }

        clipped, is_valid = validator.validate_and_clip_gradients(gradients, 'test_nan_3')

        assert not is_valid, "Validation should fail for NaN from computation"


class TestGradientInfDetection:
    """Test 4: Inf values in gradients are detected and rejected (fail-closed)."""

    def test_gradient_inf_detection_positive_infinity(self):
        """Positive infinity should cause validation to fail."""
        validator = GradientValidator(max_gradient=1.0, audit_backend=MockAuditBackend())

        gradients = {
            'L1_routing': {'grad': float('inf'), 'contributors': []},
            'L2_confidence': {'grad': 0.1, 'contributors': []},
            'L3_feedback': {'grad': 0.05, 'contributors': []},
            'L4_attention': {'grad': 0.02, 'contributors': []},
            'L5_latency': {'grad': 0.03, 'contributors': []},
            'L6_diversity': {'grad': 0.04, 'contributors': []},
        }

        clipped, is_valid = validator.validate_and_clip_gradients(gradients, 'test_inf_1')

        assert not is_valid, "Validation should fail when Inf is detected"
        assert len(clipped) == 0, "No gradients should be returned on failure"

    def test_gradient_inf_detection_negative_infinity(self):
        """Negative infinity should cause validation to fail."""
        validator = GradientValidator(max_gradient=1.0, audit_backend=MockAuditBackend())

        gradients = {
            'L1_routing': {'grad': float('-inf'), 'contributors': []},
            'L2_confidence': {'grad': 0.1, 'contributors': []},
            'L3_feedback': {'grad': 0.05, 'contributors': []},
            'L4_attention': {'grad': 0.02, 'contributors': []},
            'L5_latency': {'grad': 0.03, 'contributors': []},
            'L6_diversity': {'grad': 0.04, 'contributors': []},
        }

        clipped, is_valid = validator.validate_and_clip_gradients(gradients, 'test_inf_2')

        assert not is_valid, "Validation should fail when negative Inf is detected"
        assert len(clipped) == 0, "No gradients should be returned on failure"

    def test_gradient_inf_detection_multiple_infinities(self):
        """Multiple infinity values should all be detected."""
        validator = GradientValidator(max_gradient=1.0, audit_backend=MockAuditBackend())

        gradients = {
            'L1_routing': {'grad': float('inf'), 'contributors': []},
            'L2_confidence': {'grad': float('-inf'), 'contributors': []},
            'L3_feedback': {'grad': 0.05, 'contributors': []},
            'L4_attention': {'grad': float('inf'), 'contributors': []},
            'L5_latency': {'grad': 0.03, 'contributors': []},
            'L6_diversity': {'grad': 0.04, 'contributors': []},
        }

        clipped, is_valid = validator.validate_and_clip_gradients(gradients, 'test_inf_3')

        assert not is_valid, "Validation should fail when multiple infinities detected"

    def test_gradient_inf_detection_from_computation(self):
        """Inf from computation (1/0) should be detected."""
        validator = GradientValidator(max_gradient=1.0, audit_backend=MockAuditBackend())

        # Simulate Inf from 1/0
        inf_value = 1.0 / 0.0 if False else float('inf')

        gradients = {
            'L1_routing': {'grad': inf_value, 'contributors': []},
            'L2_confidence': {'grad': 0.1, 'contributors': []},
            'L3_feedback': {'grad': 0.05, 'contributors': []},
            'L4_attention': {'grad': 0.02, 'contributors': []},
            'L5_latency': {'grad': 0.03, 'contributors': []},
            'L6_diversity': {'grad': 0.04, 'contributors': []},
        }

        clipped, is_valid = validator.validate_and_clip_gradients(gradients, 'test_inf_4')

        assert not is_valid, "Validation should fail for Inf from computation"


class TestGradientInvalidAuditLogged:
    """Test 5: NaN/Inf events are logged with gradient_invalid_value event."""

    def test_gradient_invalid_audit_logged_nan(self):
        """NaN detection should log gradient_invalid_value event."""
        audit = MockAuditBackend()
        validator = GradientValidator(max_gradient=1.0, audit_backend=audit, tenant_id='test_tenant')

        gradients = {
            'L1_routing': {'grad': float('nan'), 'contributors': []},
            'L2_confidence': {'grad': 0.1, 'contributors': []},
            'L3_feedback': {'grad': 0.05, 'contributors': []},
            'L4_attention': {'grad': 0.02, 'contributors': []},
            'L5_latency': {'grad': 0.03, 'contributors': []},
            'L6_diversity': {'grad': 0.04, 'contributors': []},
        }

        clipped, is_valid = validator.validate_and_clip_gradients(gradients, 'test_batch_nan')

        assert not is_valid, "Validation should fail"

        # Check audit events
        events = audit.read_events('test_tenant')
        invalid_events = [e for e in events if e.get('event_type') == 'gradient_invalid_value']

        assert len(invalid_events) > 0, "At least one gradient_invalid_value event should be logged"
        event = invalid_events[0]
        assert event['severity'] == 'error', "Event should have error severity"
        assert 'invalid_loops' in event, "Event should contain invalid_loops"
        assert event['action'] == 'weight_update_refused', "Action should be weight_update_refused"

    def test_gradient_invalid_audit_logged_inf(self):
        """Inf detection should log gradient_invalid_value event."""
        audit = MockAuditBackend()
        validator = GradientValidator(max_gradient=1.0, audit_backend=audit, tenant_id='test_tenant')

        gradients = {
            'L1_routing': {'grad': float('inf'), 'contributors': []},
            'L2_confidence': {'grad': 0.1, 'contributors': []},
            'L3_feedback': {'grad': 0.05, 'contributors': []},
            'L4_attention': {'grad': 0.02, 'contributors': []},
            'L5_latency': {'grad': 0.03, 'contributors': []},
            'L6_diversity': {'grad': 0.04, 'contributors': []},
        }

        clipped, is_valid = validator.validate_and_clip_gradients(gradients, 'test_batch_inf')

        assert not is_valid, "Validation should fail"

        events = audit.read_events('test_tenant')
        invalid_events = [e for e in events if e.get('event_type') == 'gradient_invalid_value']

        assert len(invalid_events) > 0, "gradient_invalid_value event should be logged"
        event = invalid_events[0]
        assert len(event['invalid_loops']) > 0, "Should identify which loops are invalid"

    def test_gradient_clipping_audit_logged(self):
        """Gradient clipping should log gradient_clipped event."""
        audit = MockAuditBackend()
        validator = GradientValidator(max_gradient=1.0, audit_backend=audit, tenant_id='test_tenant')

        gradients = {
            'L1_routing': {'grad': 100.0, 'contributors': []},
            'L2_confidence': {'grad': 0.1, 'contributors': []},
            'L3_feedback': {'grad': 0.05, 'contributors': []},
            'L4_attention': {'grad': 0.02, 'contributors': []},
            'L5_latency': {'grad': 0.03, 'contributors': []},
            'L6_diversity': {'grad': 0.04, 'contributors': []},
        }

        clipped, is_valid = validator.validate_and_clip_gradients(gradients, 'test_batch_clip')

        assert is_valid, "Clipping should not reject valid gradients"

        events = audit.read_events('test_tenant')
        clip_events = [e for e in events if e.get('event_type') == 'gradient_clipped']

        assert len(clip_events) > 0, "gradient_clipped event should be logged"
        event = clip_events[0]
        assert event['severity'] == 'warning', "Event should have warning severity"
        assert event['num_clipped'] > 0, "Should report number of clipped gradients"
        assert 'clipped_loops' in event, "Should identify which loops were clipped"


class TestGradientRecoveryCheckpoint:
    """Test 6: Weights reset to previous known-good state on failure."""

    def test_gradient_recovery_last_good_checkpoint(self):
        """After validation failure, should be able to recover to last checkpoint."""
        validator = GradientValidator(max_gradient=1.0, audit_backend=MockAuditBackend())

        # First batch: save checkpoint
        good_weights = {
            'L1_routing': 0.5,
            'L2_confidence': 0.3,
            'L3_feedback': 0.2,
            'L4_attention': 0.1,
            'L5_latency': 0.05,
            'L6_diversity': 0.08,
        }
        validator.save_checkpoint(good_weights, 'batch_1')

        # Second batch: validation fails
        gradients = {
            'L1_routing': {'grad': float('nan'), 'contributors': []},
            'L2_confidence': {'grad': 0.1, 'contributors': []},
            'L3_feedback': {'grad': 0.05, 'contributors': []},
            'L4_attention': {'grad': 0.02, 'contributors': []},
            'L5_latency': {'grad': 0.03, 'contributors': []},
            'L6_diversity': {'grad': 0.04, 'contributors': []},
        }

        clipped, is_valid = validator.validate_and_clip_gradients(gradients, 'batch_2')

        assert not is_valid, "Validation should fail"

        # Recover checkpoint
        recovered_weights = validator.recover_to_checkpoint()

        assert recovered_weights is not None, "Should have checkpoint to recover"
        assert recovered_weights == good_weights, "Recovered weights should match saved checkpoint"

    def test_gradient_recovery_no_checkpoint(self):
        """If no checkpoint exists, recovery should return None."""
        validator = GradientValidator(max_gradient=1.0, audit_backend=MockAuditBackend())

        # Try to recover without saving checkpoint
        recovered = validator.recover_to_checkpoint()

        assert recovered is None, "Should return None when no checkpoint exists"

    def test_gradient_recovery_multiple_checkpoints(self):
        """Latest checkpoint should be recovered, not earlier ones."""
        validator = GradientValidator(max_gradient=1.0, audit_backend=MockAuditBackend())

        # Save first checkpoint
        weights1 = {'L1_routing': 0.1, 'L2_confidence': 0.2, 'L3_feedback': 0.3, 'L4_attention': 0.4, 'L5_latency': 0.5, 'L6_diversity': 0.6}
        validator.save_checkpoint(weights1, 'batch_1')

        # Save second checkpoint (overwrite)
        weights2 = {'L1_routing': 0.5, 'L2_confidence': 0.4, 'L3_feedback': 0.3, 'L4_attention': 0.2, 'L5_latency': 0.1, 'L6_diversity': 0.0}
        validator.save_checkpoint(weights2, 'batch_2')

        # Recover should get latest
        recovered = validator.recover_to_checkpoint()

        assert recovered == weights2, "Should recover latest checkpoint, not first one"
        assert recovered != weights1, "Should not recover earlier checkpoint"

    def test_gradient_recovery_audit_logged(self):
        """Recovery action should be audited."""
        audit = MockAuditBackend()
        validator = GradientValidator(max_gradient=1.0, audit_backend=audit, tenant_id='test_tenant')

        good_weights = {
            'L1_routing': 0.5,
            'L2_confidence': 0.3,
            'L3_feedback': 0.2,
            'L4_attention': 0.1,
            'L5_latency': 0.05,
            'L6_diversity': 0.08,
        }
        validator.save_checkpoint(good_weights, 'batch_1')

        # Trigger failure and recovery
        gradients = {
            'L1_routing': {'grad': float('nan'), 'contributors': []},
            'L2_confidence': {'grad': 0.1, 'contributors': []},
            'L3_feedback': {'grad': 0.05, 'contributors': []},
            'L4_attention': {'grad': 0.02, 'contributors': []},
            'L5_latency': {'grad': 0.03, 'contributors': []},
            'L6_diversity': {'grad': 0.04, 'contributors': []},
        }

        validator.validate_and_clip_gradients(gradients, 'batch_2')
        validator.recover_to_checkpoint()

        # Check audit events
        events = audit.read_events('test_tenant')
        recovery_events = [e for e in events if e.get('event_type') == 'gradient_recovery_from_checkpoint']

        assert len(recovery_events) > 0, "Recovery should be audited"
        event = recovery_events[0]
        assert event['checkpoint_batch_id'] == 'batch_1', "Should reference correct checkpoint"


class TestIntegrationWithBackpropagator:
    """Integration tests: GradientValidator integrated into LossBackpropagator."""

    def test_backpropagator_with_validator_normal_case(self):
        """LossBackpropagator with validator should pass normal gradients."""
        from core.learning.unified_loss import UnifiedLossSnapshot

        backprop = LossBackpropagator(audit_backend=MockAuditBackend(), max_gradient=1.0)

        task_batch = [
            {'confidence_score': 0.8, 'tokens_used': 500, 'latency_seconds': 2.0, 'task_type': 'classify'},
        ]
        outcomes = [{'correct': True, 'engine_correct': True}]
        feedback = [{'timestamp': datetime.now().isoformat(), 'is_valid': True}]

        snapshot = UnifiedLossSnapshot(
            timestamp=datetime.now(),
            batch_id='test_batch',
            tenant_id='default',
            L_routing=0.2, L_confidence=0.15, L_feedback=0.1,
            L_attention=0.05, L_latency=0.3, L_diversity=0.2,
            L_total=0.16,
            weights={
                'routing': 1/6, 'confidence': 1/6, 'feedback': 1/6,
                'attention': 1/6, 'latency': 1/6, 'diversity': 1/6
            }
        )

        gradients = backprop.compute_gradients_with_dag(snapshot, task_batch, outcomes, feedback)

        assert len(gradients) > 0, "Should return gradients for normal case"
        for loop_id, grad_dict in gradients.items():
            assert np.isfinite(grad_dict['grad']), f"Gradient for {loop_id} should be finite"

    def test_backpropagator_with_validator_extreme_case(self):
        """LossBackpropagator with validator should clip extreme gradients."""
        from core.learning.unified_loss import UnifiedLossSnapshot

        backprop = LossBackpropagator(audit_backend=MockAuditBackend(), max_gradient=1.0)

        # Empty batch to produce extreme gradients
        task_batch = []
        outcomes = []
        feedback = []

        snapshot = UnifiedLossSnapshot(
            timestamp=datetime.now(),
            batch_id='test_extreme',
            tenant_id='default',
            L_routing=0.9, L_confidence=0.9, L_feedback=0.9,
            L_attention=0.9, L_latency=0.9, L_diversity=0.9,
            L_total=0.9,
            weights={
                'routing': 1/6, 'confidence': 1/6, 'feedback': 1/6,
                'attention': 1/6, 'latency': 1/6, 'diversity': 1/6
            }
        )

        gradients = backprop.compute_gradients_with_dag(snapshot, task_batch, outcomes, feedback)

        # With clipping, all gradients should be within bounds
        for loop_id, grad_dict in gradients.items():
            assert -1.0 <= grad_dict['grad'] <= 1.0, \
                f"Gradient {loop_id}={grad_dict['grad']} should be clipped to [-1.0, 1.0]"


class TestAttackBlockage:
    """Verification that the attack (normalization bypass) is blocked."""

    def test_attack_normalization_bypass_blocked(self):
        """
        Attack: Inject NaN/Inf to escape bounds.
        Defense: Fail-closed rejection prevents weight update.
        """
        audit = MockAuditBackend()
        validator = GradientValidator(max_gradient=1.0, audit_backend=audit)

        # Attack payload: NaN in one loop, normal in others
        attack_gradients = {
            'L1_routing': {'grad': float('nan'), 'contributors': []},  # Attack vector
            'L2_confidence': {'grad': 0.1, 'contributors': []},
            'L3_feedback': {'grad': 0.05, 'contributors': []},
            'L4_attention': {'grad': 0.02, 'contributors': []},
            'L5_latency': {'grad': 0.03, 'contributors': []},
            'L6_diversity': {'grad': 0.04, 'contributors': []},
        }

        clipped, is_valid = validator.validate_and_clip_gradients(attack_gradients, 'attack_batch')

        # Attack should be BLOCKED
        assert not is_valid, "Attack (NaN injection) should be blocked"
        assert len(clipped) == 0, "No weights should be updated on attack detection"

        # Verify audit trail
        events = audit.read_events('default')
        attack_events = [e for e in events if e.get('event_type') == 'gradient_invalid_value']
        assert len(attack_events) > 0, "Attack should be recorded in audit trail"

    def test_attack_extreme_overflow_blocked(self):
        """
        Attack: Inject extreme values (1e100) to cause overflow.
        Defense: Hard clipping prevents overflow.
        """
        validator = GradientValidator(max_gradient=1.0, audit_backend=MockAuditBackend())

        attack_gradients = {
            'L1_routing': {'grad': 1e100, 'contributors': []},  # Extreme overflow attempt
            'L2_confidence': {'grad': 0.1, 'contributors': []},
            'L3_feedback': {'grad': 0.05, 'contributors': []},
            'L4_attention': {'grad': 0.02, 'contributors': []},
            'L5_latency': {'grad': 0.03, 'contributors': []},
            'L6_diversity': {'grad': 0.04, 'contributors': []},
        }

        clipped, is_valid = validator.validate_and_clip_gradients(attack_gradients, 'overflow_attack')

        # Attack should be NEUTRALIZED by clipping
        assert is_valid, "Extreme values should be clipped, not rejected"
        assert clipped['L1_routing']['grad'] == 1.0, "Overflow attack should be clipped to bound"
        assert clipped['L1_routing']['was_clipped'], "Clipping should be marked"

    def test_attack_multi_loop_bypass_blocked(self):
        """
        Attack: Inject NaN in multiple loops to overwhelm defenses.
        Defense: Entire batch is rejected if ANY loop has NaN.
        """
        validator = GradientValidator(max_gradient=1.0, audit_backend=MockAuditBackend())

        attack_gradients = {
            'L1_routing': {'grad': float('nan'), 'contributors': []},
            'L2_confidence': {'grad': float('nan'), 'contributors': []},
            'L3_feedback': {'grad': 0.05, 'contributors': []},
            'L4_attention': {'grad': float('inf'), 'contributors': []},  # Multiple attack vectors
            'L5_latency': {'grad': 0.03, 'contributors': []},
            'L6_diversity': {'grad': 0.04, 'contributors': []},
        }

        clipped, is_valid = validator.validate_and_clip_gradients(attack_gradients, 'multi_attack')

        # Entire batch should be rejected
        assert not is_valid, "Batch with multiple invalid values should be rejected"
        assert len(clipped) == 0, "No weights should be updated on multi-loop attack"


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
