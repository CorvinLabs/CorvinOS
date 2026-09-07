"""
Tests for Gradient Validator Security Fix #4 (ADR-0647)

Normalization Bypass Prevention Tests:
  - Hard clipping bounds enforcement
  - NaN/Inf detection (fail-closed)
  - Checkpoint recovery mechanism
  - Audit event generation
  - Tenant isolation

Test Suite: ≥3 test cases covering all attack vectors
"""

import pytest
import numpy as np
from core.learning.gradient_validator import GradientValidator, ValidationResult


class MockAuditBackend:
    """Mock audit backend for testing."""

    def __init__(self):
        self.events = []

    def write_event(self, event: dict):
        """Record audit event."""
        self.events.append(event)

    def get_events(self):
        """Retrieve all recorded events."""
        return self.events

    def clear_events(self):
        """Clear event history."""
        self.events = []


class TestGradientValidatorHardClipping:
    """Test Case 1: Hard Clipping Enforcement"""

    def test_clip_extreme_positive_gradients(self):
        """
        ATTACK: Extreme positive gradients escape bounds
        DEFENSE: Hard clipping to max_gradient

        Test Input: gradient = 999.0, max_gradient = 1.0
        Expected: clipped to 1.0
        """
        validator = GradientValidator(max_gradient=1.0)

        gradients = {
            'L1_routing': {'grad': 999.0, 'contributors': []},
            'L2_confidence': {'grad': 500.0, 'contributors': []},
            'L3_feedback': {'grad': 0.5, 'contributors': []},
        }

        result = validator.validate_and_clip_gradients(gradients, batch_id='test_batch_1')

        assert result.is_valid
        assert result.clipped_gradients['L1_routing']['grad'] == 1.0
        assert result.clipped_gradients['L2_confidence']['grad'] == 1.0
        assert result.clipped_gradients['L3_feedback']['grad'] == 0.5
        assert result.num_clipped == 2
        assert not result.clipped_gradients['L3_feedback']['was_clipped']
        assert result.clipped_gradients['L1_routing']['was_clipped']

    def test_clip_extreme_negative_gradients(self):
        """
        ATTACK: Extreme negative gradients escape bounds
        DEFENSE: Hard clipping to -max_gradient

        Test Input: gradient = -999.0, max_gradient = 1.0
        Expected: clipped to -1.0
        """
        validator = GradientValidator(max_gradient=1.0)

        gradients = {
            'L1_routing': {'grad': -999.0, 'contributors': []},
            'L2_confidence': {'grad': -500.0, 'contributors': []},
            'L3_feedback': {'grad': -0.3, 'contributors': []},
        }

        result = validator.validate_and_clip_gradients(gradients, batch_id='test_batch_2')

        assert result.is_valid
        assert result.clipped_gradients['L1_routing']['grad'] == -1.0
        assert result.clipped_gradients['L2_confidence']['grad'] == -1.0
        assert result.clipped_gradients['L3_feedback']['grad'] == -0.3
        assert result.num_clipped == 2

    def test_custom_clip_bound(self):
        """
        DEFENSE: Custom clipping bounds are respected

        Test Input: max_gradient = 5.0, gradient = 100.0
        Expected: clipped to 5.0
        """
        validator = GradientValidator(max_gradient=5.0)

        gradients = {
            'L1_routing': {'grad': 100.0, 'contributors': []},
            'L2_confidence': {'grad': -100.0, 'contributors': []},
        }

        result = validator.validate_and_clip_gradients(gradients, batch_id='test_batch_3')

        assert result.is_valid
        assert result.clipped_gradients['L1_routing']['grad'] == 5.0
        assert result.clipped_gradients['L2_confidence']['grad'] == -5.0


class TestGradientValidatorNaNInfDetection:
    """Test Case 2: NaN/Inf Detection (Fail-Closed)"""

    def test_reject_nan_values(self):
        """
        ATTACK: NaN values propagate through weight updates
        DEFENSE: Fail-closed rejection of NaN

        Test Input: gradient = NaN
        Expected: is_valid=False, empty clipped_gradients, audit event
        """
        audit = MockAuditBackend()
        validator = GradientValidator(max_gradient=1.0, audit_backend=audit, tenant_id='test_tenant')

        gradients = {
            'L1_routing': {'grad': float('nan'), 'contributors': []},
            'L2_confidence': {'grad': 0.5, 'contributors': []},
        }

        result = validator.validate_and_clip_gradients(gradients, batch_id='test_batch_nan')

        # Fail-closed: invalid result
        assert not result.is_valid
        assert result.clipped_gradients == {}
        assert result.num_invalid == 1
        assert len(result.invalid_loops) == 1
        assert result.invalid_loops[0]['type'] == 'NaN'

        # Audit event recorded
        assert len(audit.events) == 1
        audit_event = audit.events[0]
        assert audit_event['event_type'] == 'gradient_invalid_value'
        assert audit_event['severity'] == 'error'
        assert audit_event['tenant_id'] == 'test_tenant'

    def test_reject_positive_infinity(self):
        """
        ATTACK: +Inf values escape bounds
        DEFENSE: Fail-closed rejection of +Inf

        Test Input: gradient = +Infinity
        Expected: is_valid=False, audit event
        """
        audit = MockAuditBackend()
        validator = GradientValidator(max_gradient=1.0, audit_backend=audit)

        gradients = {
            'L1_routing': {'grad': float('inf'), 'contributors': []},
        }

        result = validator.validate_and_clip_gradients(gradients, batch_id='test_batch_inf')

        assert not result.is_valid
        assert result.clipped_gradients == {}
        assert result.num_invalid == 1
        assert result.invalid_loops[0]['type'] == 'Inf'

    def test_reject_negative_infinity(self):
        """
        ATTACK: -Inf values escape bounds
        DEFENSE: Fail-closed rejection of -Inf

        Test Input: gradient = -Infinity
        Expected: is_valid=False, audit event
        """
        audit = MockAuditBackend()
        validator = GradientValidator(max_gradient=1.0, audit_backend=audit)

        gradients = {
            'L2_confidence': {'grad': float('-inf'), 'contributors': []},
        }

        result = validator.validate_and_clip_gradients(gradients, batch_id='test_batch_ninf')

        assert not result.is_valid
        assert result.num_invalid == 1
        assert result.invalid_loops[0]['type'] == 'Inf'

    def test_detect_multiple_invalid_values(self):
        """
        ATTACK: Multiple NaN/Inf values in batch
        DEFENSE: Detect all invalid values, comprehensive audit

        Test Input: gradient dict with 2 NaN + 1 Inf + 1 valid
        Expected: Catch all 3 invalid, is_valid=False
        """
        audit = MockAuditBackend()
        validator = GradientValidator(max_gradient=1.0, audit_backend=audit, tenant_id='test_tenant_multi')

        gradients = {
            'L1_routing': {'grad': float('nan'), 'contributors': []},
            'L2_confidence': {'grad': float('inf'), 'contributors': []},
            'L3_feedback': {'grad': float('-inf'), 'contributors': []},
            'L4_attention': {'grad': 0.5, 'contributors': []},
        }

        result = validator.validate_and_clip_gradients(gradients, batch_id='test_batch_multi_invalid')

        assert not result.is_valid
        assert result.num_invalid == 3
        assert len(result.invalid_loops) == 3
        # Audit event should list all invalid loops
        audit_event = audit.events[0]
        assert audit_event['num_invalid_loops'] == 3


class TestGradientValidatorCheckpointRecovery:
    """Test Case 3: Checkpoint Rollback Recovery"""

    def test_checkpoint_save_and_recover(self):
        """
        DEFENSE: Rollback to known-good weights after validation failure

        Scenario:
          1. Save checkpoint before validation
          2. Validation fails (NaN detected)
          3. Recover to checkpoint
        """
        audit = MockAuditBackend()
        validator = GradientValidator(max_gradient=1.0, audit_backend=audit)

        # Save checkpoint
        checkpoint_weights = {
            'L1_routing': 0.1,
            'L2_confidence': 0.2,
            'L3_feedback': 0.3,
        }
        validator.save_checkpoint(checkpoint_weights, batch_id='checkpoint_batch_1')

        # Attempt validation with NaN (will fail)
        bad_gradients = {
            'L1_routing': {'grad': float('nan'), 'contributors': []},
        }
        result = validator.validate_and_clip_gradients(bad_gradients, batch_id='bad_batch_1')

        assert not result.is_valid

        # Recover to checkpoint
        recovered_weights = validator.recover_to_checkpoint()

        assert recovered_weights is not None
        assert recovered_weights == checkpoint_weights
        assert recovered_weights is not checkpoint_weights  # Deep copy

        # Audit should show recovery action
        audit_events = audit.events
        recovery_events = [e for e in audit_events if e.get('event_type') == 'gradient_recovery_from_checkpoint']
        assert len(recovery_events) == 1
        assert recovery_events[0]['recovered_loop_count'] == 3

    def test_checkpoint_missing_returns_none(self):
        """
        DEFENSE: No checkpoint available → graceful None return

        Scenario: No checkpoint saved, recovery attempted
        """
        validator = GradientValidator()

        recovered_weights = validator.recover_to_checkpoint()

        assert recovered_weights is None

    def test_checkpoint_deep_copy(self):
        """
        DEFENSE: Checkpoint is deep-copied, modifications don't affect checkpoint

        Scenario:
          1. Save checkpoint
          2. Modify recovered weights
          3. Verify checkpoint unchanged
        """
        validator = GradientValidator()

        checkpoint_weights = {'L1': 0.5, 'L2': 0.6}
        validator.save_checkpoint(checkpoint_weights, batch_id='test_batch')

        recovered = validator.recover_to_checkpoint()
        recovered['L1'] = 999.0  # Modify recovered copy

        # Recover again and verify checkpoint unchanged
        recovered_again = validator.recover_to_checkpoint()
        assert recovered_again['L1'] == 0.5


class TestGradientValidatorStatistics:
    """Test Case 4: Statistics and Audit Tracking"""

    def test_statistics_tracking(self):
        """
        DEFENSE: Track validation failures and clipping for audit

        Scenario: Multiple validations with clipping and failures
        """
        audit = MockAuditBackend()
        validator = GradientValidator(max_gradient=1.0, audit_backend=audit)

        # Validation 1: successful with clipping
        gradients_1 = {'L1_routing': {'grad': 100.0, 'contributors': []}}
        result_1 = validator.validate_and_clip_gradients(gradients_1, batch_id='batch_1')
        assert result_1.is_valid
        assert result_1.num_clipped == 1

        # Validation 2: failure with NaN
        gradients_2 = {'L2_confidence': {'grad': float('nan'), 'contributors': []}}
        result_2 = validator.validate_and_clip_gradients(gradients_2, batch_id='batch_2')
        assert not result_2.is_valid

        # Check stats
        stats = validator.get_stats()
        assert stats['num_validation_failures'] == 1
        assert stats['num_gradients_clipped'] == 1
        assert stats['num_invalid_values_detected'] == 1

    def test_audit_event_contains_tenant_id(self):
        """
        DEFENSE: All audit events are tenant-scoped (GDPR Art. 5, 6, 32)

        Scenario: Validation event includes tenant_id
        """
        audit = MockAuditBackend()
        validator = GradientValidator(
            max_gradient=1.0,
            audit_backend=audit,
            tenant_id='customer_xyz'
        )

        gradients = {
            'L1_routing': {'grad': 100.0, 'contributors': []},
        }
        validator.validate_and_clip_gradients(gradients, batch_id='batch_tenant')

        # Verify tenant_id in audit event
        assert len(audit.events) == 1
        assert audit.events[0]['tenant_id'] == 'customer_xyz'


class TestGradientValidatorEdgeCases:
    """Test Case 5: Edge Cases and Boundary Conditions"""

    def test_zero_max_gradient_bound(self):
        """
        EDGE CASE: max_gradient = 0 (no gradients allowed)

        Test Input: max_gradient = 0.0, gradient = 0.5
        Expected: Clipped to 0.0
        """
        validator = GradientValidator(max_gradient=0.0)

        gradients = {'L1_routing': {'grad': 0.5, 'contributors': []}}
        result = validator.validate_and_clip_gradients(gradients, batch_id='zero_bound')

        assert result.is_valid
        assert result.clipped_gradients['L1_routing']['grad'] == 0.0

    def test_empty_gradients_dict(self):
        """
        EDGE CASE: Empty gradients dictionary

        Test Input: {} (no gradients)
        Expected: Valid result, no clipping
        """
        validator = GradientValidator()

        result = validator.validate_and_clip_gradients({}, batch_id='empty_batch')

        assert result.is_valid
        assert result.clipped_gradients == {}
        assert result.num_clipped == 0

    def test_very_small_gradients_not_clipped(self):
        """
        DEFENSE: Very small gradients remain unclipped

        Test Input: gradient = 1e-10, max_gradient = 1.0
        Expected: Not clipped
        """
        validator = GradientValidator(max_gradient=1.0)

        gradients = {'L1_routing': {'grad': 1e-10, 'contributors': []}}
        result = validator.validate_and_clip_gradients(gradients, batch_id='tiny_grad')

        assert result.is_valid
        assert result.clipped_gradients['L1_routing']['grad'] == 1e-10
        assert not result.clipped_gradients['L1_routing']['was_clipped']

    def test_nan_from_computation(self):
        """
        ATTACK: NaN produced by invalid arithmetic (e.g., 0/0, inf-inf)
        DEFENSE: Detect and reject

        Test Input: gradient = computed NaN
        Expected: Fail-closed
        """
        validator = GradientValidator()

        # Simulate NaN from invalid computation
        gradients = {
            'L1_routing': {'grad': np.nan, 'contributors': []},
        }
        result = validator.validate_and_clip_gradients(gradients, batch_id='computed_nan')

        assert not result.is_valid
        assert result.num_invalid == 1


class TestGradientValidatorIntegration:
    """Integration Tests: Full Workflow"""

    def test_full_validation_workflow(self):
        """
        INTEGRATION TEST: Complete validation workflow

        Scenario:
          1. Save checkpoint
          2. Compute gradients
          3. Validate (clipping + NaN detection)
          4. If failed, recover checkpoint
        """
        audit = MockAuditBackend()
        validator = GradientValidator(max_gradient=1.0, audit_backend=audit, tenant_id='integration_test')

        # Step 1: Save checkpoint
        initial_weights = {'L1_routing': 0.1, 'L2_confidence': 0.2}
        validator.save_checkpoint(initial_weights, batch_id='batch_0')

        # Step 2: Compute gradients (simulated)
        gradients = {
            'L1_routing': {'grad': 50.0, 'contributors': ['L2']},  # Will be clipped
            'L2_confidence': {'grad': 0.3, 'contributors': []},
        }

        # Step 3: Validate
        result = validator.validate_and_clip_gradients(gradients, batch_id='batch_1')

        # Verify clipping worked
        assert result.is_valid
        assert result.clipped_gradients['L1_routing']['grad'] == 1.0
        assert result.num_clipped == 1

        # Step 4: Use clipped gradients to update weights
        new_weights = {
            k: initial_weights[k] + result.clipped_gradients[k]['grad']
            for k in initial_weights.keys()
        }

        # Verify audit trail
        assert len(audit.events) >= 1  # At least gradient_clipped event
        clipping_events = [e for e in audit.events if e.get('event_type') == 'gradient_clipped']
        assert len(clipping_events) >= 1

    def test_recovery_after_nan_detection(self):
        """
        INTEGRATION TEST: Recover from NaN detection

        Scenario:
          1. Save valid checkpoint
          2. Validation fails (NaN detected)
          3. Recover to checkpoint (no weight update)
          4. Next batch succeeds
        """
        audit = MockAuditBackend()
        validator = GradientValidator(max_gradient=1.0, audit_backend=audit)

        # Batch 1: Valid weights, save checkpoint
        valid_weights = {'L1_routing': 0.1, 'L2_confidence': 0.2}
        validator.save_checkpoint(valid_weights, batch_id='batch_1')

        # Batch 2: NaN detected, recover
        bad_gradients = {'L1_routing': {'grad': float('nan'), 'contributors': []}}
        result_bad = validator.validate_and_clip_gradients(bad_gradients, batch_id='batch_2_bad')
        assert not result_bad.is_valid

        # Recover to checkpoint (no weight update occurred)
        recovered = validator.recover_to_checkpoint()
        assert recovered == valid_weights

        # Batch 3: Valid gradients, new checkpoint
        good_gradients = {'L1_routing': {'grad': 0.05, 'contributors': []}}
        result_good = validator.validate_and_clip_gradients(good_gradients, batch_id='batch_3_good')
        assert result_good.is_valid
        validator.save_checkpoint(valid_weights, batch_id='batch_3')

        # Stats should show 1 failure
        stats = validator.get_stats()
        assert stats['num_validation_failures'] == 1


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
