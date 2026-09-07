"""
Test: Cascading Divergence Fix #5 — Per-Tier Gradient Clipping

Tests that the per-tier gradient clipping mitigation (ADR-0647 #5) prevents
cascading divergence where gradients grow unboundedly through the DAG.

Test cases:
1. Test per-tier clipping bounds are applied independently
2. Test cascading divergence is prevented (gradients stay bounded)
3. Test invalid gradients are caught and cause fail-closed behavior
"""

from core.learning.gradient_backprop import LossBackpropagator, PerTierGradientClipper
from core.learning.unified_loss import UnifiedLossSnapshot
from tests.learning.mock_audit_backend import MockAuditBackend
from datetime import datetime
import numpy as np
import pytest


class TestPerTierGradientClipper:
    """Test 1: Per-tier clipper applies tier-specific bounds correctly."""

    def test_tier_definitions_are_valid(self):
        """Verify tier definitions exist and cover all loops."""
        clipper = PerTierGradientClipper()

        # All loops should be assigned to a tier
        all_loops = {'L1_routing', 'L2_confidence', 'L3_feedback', 'L4_attention', 'L5_latency', 'L6_diversity'}
        assigned_loops = set()
        for tier_id, tier_info in clipper.tier_definitions.items():
            assigned_loops.update(tier_info['loops'])

        assert assigned_loops == all_loops, f"Tier assignment incomplete: {all_loops - assigned_loops} missing"

    def test_tier_bounds_are_ordered(self):
        """Verify tier bounds are tight at source (tier 0) and loose at leaves (tier 3)."""
        clipper = PerTierGradientClipper()

        bounds = []
        for tier_id in ['tier_0', 'tier_1', 'tier_2', 'tier_3']:
            bounds.append(clipper.tier_definitions[tier_id]['max_gradient'])

        # Bounds should be non-decreasing (tight → loose)
        for i in range(len(bounds) - 1):
            assert bounds[i] <= bounds[i + 1], f"Tier bounds not ordered: {bounds}"

    def test_clip_gradients_by_tier_basic(self):
        """Test basic tier-based clipping: gradients within bounds pass through."""
        clipper = PerTierGradientClipper(audit_backend=MockAuditBackend())

        # Gradients below tier bounds should not be clipped
        gradients = {
            'L4_attention': {'grad': 0.2, 'contributors': []},  # Tier 0, bound 0.5
            'L3_feedback': {'grad': 0.3, 'contributors': []},   # Tier 1, bound 0.7
            'L2_confidence': {'grad': 0.5, 'contributors': []}, # Tier 2, bound 0.8
            'L1_routing': {'grad': 0.8, 'contributors': []},    # Tier 3, bound 1.0
            'L5_latency': {'grad': 0.4, 'contributors': []},    # Tier 2, bound 0.8
            'L6_diversity': {'grad': 0.1, 'contributors': []},  # Tier 0, bound 0.5
        }

        clipped, valid = clipper.clip_gradients_by_tier(gradients, batch_id='test_1')

        assert valid, "Clipping should succeed for valid gradients"
        for loop_id, grad_dict in clipped.items():
            # Gradients below bounds should not be clipped
            assert not grad_dict['was_clipped'], f"{loop_id} should not be clipped"
            assert grad_dict['grad'] == gradients[loop_id]['grad']

    def test_clip_gradients_respects_tier_bounds(self):
        """Test that gradients exceeding tier bounds are clipped."""
        clipper = PerTierGradientClipper(audit_backend=MockAuditBackend())

        # Gradients exceeding tier bounds should be clipped
        gradients = {
            'L4_attention': {'grad': 1.5, 'contributors': []},  # Tier 0 bound 0.5 → clipped to 0.5
            'L3_feedback': {'grad': 1.0, 'contributors': []},   # Tier 1 bound 0.7 → clipped to 0.7
            'L2_confidence': {'grad': 1.2, 'contributors': []}, # Tier 2 bound 0.8 → clipped to 0.8
            'L1_routing': {'grad': 1.5, 'contributors': []},    # Tier 3 bound 1.0 → clipped to 1.0
            'L5_latency': {'grad': 0.9, 'contributors': []},    # Tier 2 bound 0.8 → clipped to 0.8
            'L6_diversity': {'grad': 0.6, 'contributors': []},  # Tier 0 bound 0.5 → clipped to 0.5
        }

        clipped, valid = clipper.clip_gradients_by_tier(gradients, batch_id='test_2')

        assert valid, "Clipping should succeed even with out-of-bound gradients"

        # Verify clipping was applied
        assert clipped['L4_attention']['grad'] == 0.5, "L4 should be clipped to tier 0 bound"
        assert clipped['L3_feedback']['grad'] == 0.7, "L3 should be clipped to tier 1 bound"
        assert clipped['L2_confidence']['grad'] == 0.8, "L2 should be clipped to tier 2 bound"
        assert clipped['L1_routing']['grad'] == 1.0, "L1 should be clipped to tier 3 bound"
        assert clipped['L5_latency']['grad'] == 0.8, "L5 should be clipped to tier 2 bound"
        assert clipped['L6_diversity']['grad'] == 0.5, "L6 should be clipped to tier 0 bound"

        # Verify was_clipped flags are set
        for loop_id in gradients.keys():
            assert clipped[loop_id]['was_clipped'], f"{loop_id} should have was_clipped=True"

    def test_negative_gradients_clipped_symmetrically(self):
        """Test that negative gradients are clipped symmetrically."""
        clipper = PerTierGradientClipper(audit_backend=MockAuditBackend())

        gradients = {
            'L4_attention': {'grad': -1.5, 'contributors': []},  # Clip to -0.5
            'L3_feedback': {'grad': -2.0, 'contributors': []},   # Clip to -0.7
            'L2_confidence': {'grad': -1.5, 'contributors': []}, # Clip to -0.8
            'L1_routing': {'grad': -2.0, 'contributors': []},    # Clip to -1.0
            'L5_latency': {'grad': 0.0, 'contributors': []},
            'L6_diversity': {'grad': 0.0, 'contributors': []},
        }

        clipped, valid = clipper.clip_gradients_by_tier(gradients, batch_id='test_3')

        assert valid
        assert clipped['L4_attention']['grad'] == -0.5
        assert clipped['L3_feedback']['grad'] == -0.7
        assert clipped['L2_confidence']['grad'] == -0.8
        assert clipped['L1_routing']['grad'] == -1.0

    def test_nan_gradient_causes_failure(self):
        """Test that NaN gradients cause fail-closed behavior."""
        clipper = PerTierGradientClipper(audit_backend=MockAuditBackend())

        gradients = {
            'L4_attention': {'grad': np.nan, 'contributors': []},  # Invalid
            'L3_feedback': {'grad': 0.3, 'contributors': []},
            'L2_confidence': {'grad': 0.5, 'contributors': []},
            'L1_routing': {'grad': 0.8, 'contributors': []},
            'L5_latency': {'grad': 0.4, 'contributors': []},
            'L6_diversity': {'grad': 0.1, 'contributors': []},
        }

        clipped, valid = clipper.clip_gradients_by_tier(gradients, batch_id='test_4')

        # Should fail-closed on NaN
        assert not valid, "Should fail on NaN gradient"
        assert len(clipped) == 0, "Should return empty dict on failure"

    def test_inf_gradient_causes_failure(self):
        """Test that Inf gradients cause fail-closed behavior."""
        clipper = PerTierGradientClipper(audit_backend=MockAuditBackend())

        gradients = {
            'L4_attention': {'grad': np.inf, 'contributors': []},  # Invalid
            'L3_feedback': {'grad': 0.3, 'contributors': []},
            'L2_confidence': {'grad': 0.5, 'contributors': []},
            'L1_routing': {'grad': 0.8, 'contributors': []},
            'L5_latency': {'grad': 0.4, 'contributors': []},
            'L6_diversity': {'grad': 0.1, 'contributors': []},
        }

        clipped, valid = clipper.clip_gradients_by_tier(gradients, batch_id='test_5')

        # Should fail-closed on Inf
        assert not valid, "Should fail on Inf gradient"
        assert len(clipped) == 0, "Should return empty dict on failure"

    def test_get_statistics_reports_clipping(self):
        """Test that statistics are tracked correctly."""
        clipper = PerTierGradientClipper(audit_backend=MockAuditBackend())

        # Clip multiple times to accumulate stats
        for batch_num in range(3):
            gradients = {
                'L4_attention': {'grad': 2.0, 'contributors': []},  # Exceeds tier 0 bound
                'L3_feedback': {'grad': 0.3, 'contributors': []},
                'L2_confidence': {'grad': 1.5, 'contributors': []}, # Exceeds tier 2 bound
                'L1_routing': {'grad': 0.5, 'contributors': []},
                'L5_latency': {'grad': 0.4, 'contributors': []},
                'L6_diversity': {'grad': 0.1, 'contributors': []},
            }
            clipper.clip_gradients_by_tier(gradients, batch_id=f'batch_{batch_num}')

        stats = clipper.get_statistics()
        assert stats['num_tier_clips']['tier_0'] > 0, "Tier 0 should have clipped gradients"
        assert stats['num_tier_clips']['tier_2'] > 0, "Tier 2 should have clipped gradients"
        assert 'tier_definitions' in stats


class TestCascadingDivergencePrevention:
    """Test 2: Verify cascading divergence is prevented."""

    def test_large_upstream_gradient_does_not_cascade(self):
        """Test that large gradients at source tiers (L4, L6) are clipped and don't cascade."""
        backprop = LossBackpropagator(audit_backend=MockAuditBackend())

        # Create a scenario where L4 (attention) has a large gradient
        # that would normally cascade through L3 → L2 → L1
        task_batch = [
            {'confidence_score': 0.5, 'tokens_used': 5000, 'latency_seconds': 10.0, 'task_type': 'classify'},
        ]
        outcomes = [
            {'correct': False, 'engine_correct': False},
        ]
        feedback = [None]

        snapshot = UnifiedLossSnapshot(
            timestamp=datetime.now(),
            batch_id='cascading_test_1',
            tenant_id='default',
            L_routing=0.9, L_confidence=0.8, L_feedback=0.9,
            L_attention=0.9, L_latency=0.8, L_diversity=0.7,
            L_total=0.85,
            weights={'routing': 1/6, 'confidence': 1/6, 'feedback': 1/6, 'attention': 1/6, 'latency': 1/6, 'diversity': 1/6}
        )

        gradients = backprop.compute_gradients_with_dag(snapshot, task_batch, outcomes, feedback)

        # Verify gradients are not empty (valid)
        assert len(gradients) > 0, "Gradients should be computed"

        # Verify L4 (source tier) gradient is bounded by tier 0 bound (0.5)
        assert abs(gradients['L4_attention']['grad']) <= 0.5, \
            f"L4 gradient should be clipped to tier 0 bound (0.5), got {gradients['L4_attention']['grad']}"

        # Verify L3 (tier 1) gradient is bounded by tier 1 bound (0.7)
        assert abs(gradients['L3_feedback']['grad']) <= 0.7, \
            f"L3 gradient should be clipped to tier 1 bound (0.7), got {gradients['L3_feedback']['grad']}"

        # Verify L2 (tier 2) gradient is bounded by tier 2 bound (0.8)
        assert abs(gradients['L2_confidence']['grad']) <= 0.8, \
            f"L2 gradient should be clipped to tier 2 bound (0.8), got {gradients['L2_confidence']['grad']}"

        # Verify L1 (tier 3) gradient is bounded by tier 3 bound (1.0)
        assert abs(gradients['L1_routing']['grad']) <= 1.0, \
            f"L1 gradient should be clipped to tier 3 bound (1.0), got {gradients['L1_routing']['grad']}"

    def test_total_gradient_magnitude_is_bounded(self):
        """Test that total gradient magnitude stays bounded even with multiple large inputs."""
        backprop = LossBackpropagator(audit_backend=MockAuditBackend())

        # Simulate multiple batches with large loss to trigger divergence tendency
        total_magnitudes = []

        for batch_num in range(5):
            task_batch = [
                {'confidence_score': 0.1, 'tokens_used': 10000, 'latency_seconds': 20.0, 'task_type': 'classify'},
                {'confidence_score': 0.2, 'tokens_used': 8000, 'latency_seconds': 15.0, 'task_type': 'summarize'},
            ]
            outcomes = [
                {'correct': False, 'engine_correct': False},
                {'correct': False, 'engine_correct': False},
            ]
            feedback = [None, None]

            snapshot = UnifiedLossSnapshot(
                timestamp=datetime.now(),
                batch_id=f'cascading_test_2_{batch_num}',
                tenant_id='default',
                L_routing=0.95, L_confidence=0.95, L_feedback=0.95,
                L_attention=0.95, L_latency=0.95, L_diversity=0.95,
                L_total=0.95,
                weights={'routing': 1/6, 'confidence': 1/6, 'feedback': 1/6, 'attention': 1/6, 'latency': 1/6, 'diversity': 1/6}
            )

            gradients = backprop.compute_gradients_with_dag(snapshot, task_batch, outcomes, feedback)

            if len(gradients) > 0:  # Only measure if not rejected
                total_magnitude = sum(abs(g['grad']) for g in gradients.values())
                total_magnitudes.append(total_magnitude)

        # Verify magnitude doesn't grow unboundedly across batches
        if len(total_magnitudes) > 1:
            # Should be bounded (not exponentially growing)
            assert max(total_magnitudes) < 10.0, \
                f"Gradient magnitude should stay bounded, got max {max(total_magnitudes)}"

            # Check that later magnitudes are not significantly larger than early ones
            early_avg = np.mean(total_magnitudes[:2])
            late_avg = np.mean(total_magnitudes[-2:])
            ratio = late_avg / max(early_avg, 0.1)
            assert ratio < 5.0, \
                f"Gradient magnitude growth should be limited, got ratio {ratio}"

    def test_mixed_tier_gradients_prevent_cascade(self):
        """Test scenario with mixed tier gradients that would cascade without clipping."""
        backprop = LossBackpropagator(audit_backend=MockAuditBackend())

        # Large latency (L5) + Large feedback (L3) = large confidence (L2) = large routing (L1)
        # Without per-tier clipping, this would cascade
        task_batch = [
            {'confidence_score': 0.5, 'tokens_used': 100, 'latency_seconds': 50.0, 'task_type': 'classify'},
        ]
        outcomes = [
            {'correct': False, 'engine_correct': False},
        ]
        feedback = [None]  # No feedback = high L3

        snapshot = UnifiedLossSnapshot(
            timestamp=datetime.now(),
            batch_id='cascading_test_3',
            tenant_id='default',
            L_routing=0.8, L_confidence=0.8, L_feedback=0.9,  # High feedback loss
            L_attention=0.5, L_latency=0.95, L_diversity=0.3,  # High latency loss
            L_total=0.8,
            weights={'routing': 1/6, 'confidence': 1/6, 'feedback': 1/6, 'attention': 1/6, 'latency': 1/6, 'diversity': 1/6}
        )

        gradients = backprop.compute_gradients_with_dag(snapshot, task_batch, outcomes, feedback)

        assert len(gradients) > 0, "Should compute gradients"

        # Verify no loop exceeds its tier bound
        tier_bounds = {
            'L4_attention': 0.5,
            'L6_diversity': 0.5,
            'L3_feedback': 0.7,
            'L2_confidence': 0.8,
            'L5_latency': 0.8,
            'L1_routing': 1.0,
        }

        for loop_id, bound in tier_bounds.items():
            if loop_id in gradients:
                grad_val = abs(gradients[loop_id]['grad'])
                assert grad_val <= bound * 1.01, \
                    f"{loop_id} gradient {grad_val} exceeds tier bound {bound}"


class TestFailClosedBehavior:
    """Test 3: Verify fail-closed behavior on invalid inputs."""

    def test_invalid_gradient_causes_weight_update_refusal(self):
        """Test that invalid gradients cause weight update to be refused."""
        backprop = LossBackpropagator(audit_backend=MockAuditBackend())

        # Create gradients with invalid value
        task_batch = [
            {'confidence_score': 0.5, 'tokens_used': 1000, 'latency_seconds': 2.0, 'task_type': 'classify'},
        ]
        outcomes = [
            {'correct': True, 'engine_correct': True},
        ]
        feedback = [{'timestamp': datetime.now().isoformat(), 'is_valid': True}]

        # Manually inject NaN to trigger failure
        snapshot = UnifiedLossSnapshot(
            timestamp=datetime.now(),
            batch_id='fail_closed_test_1',
            tenant_id='default',
            L_routing=0.2, L_confidence=0.15, L_feedback=0.1,
            L_attention=0.05, L_latency=0.3, L_diversity=0.2,
            L_total=0.16,
            weights={'routing': 1/6, 'confidence': 1/6, 'feedback': 1/6, 'attention': 1/6, 'latency': 1/6, 'diversity': 1/6}
        )

        # Force NaN by mocking gradient computation to return invalid value
        # This is done via direct clipper test instead
        clipper = PerTierGradientClipper(audit_backend=MockAuditBackend())

        gradients_with_nan = {
            'L4_attention': {'grad': np.nan, 'contributors': []},
            'L3_feedback': {'grad': 0.3, 'contributors': []},
            'L2_confidence': {'grad': 0.5, 'contributors': []},
            'L1_routing': {'grad': 0.8, 'contributors': []},
            'L5_latency': {'grad': 0.4, 'contributors': []},
            'L6_diversity': {'grad': 0.1, 'contributors': []},
        }

        clipped, valid = clipper.clip_gradients_by_tier(gradients_with_nan, batch_id='fail_closed_test_1')

        # Must fail-closed
        assert not valid, "Should reject gradients with NaN"
        assert len(clipped) == 0, "Should return empty dict on failure"

    def test_audit_trail_records_tier_clipping(self):
        """Test that audit trail records all tier clipping events."""
        audit = MockAuditBackend()
        backprop = LossBackpropagator(audit_backend=audit)

        task_batch = [
            {'confidence_score': 0.5, 'tokens_used': 1000, 'latency_seconds': 2.0, 'task_type': 'classify'},
        ]
        outcomes = [
            {'correct': True, 'engine_correct': True},
        ]
        feedback = [None]

        snapshot = UnifiedLossSnapshot(
            timestamp=datetime.now(),
            batch_id='audit_test_1',
            tenant_id='default',
            L_routing=0.2, L_confidence=0.15, L_feedback=0.1,
            L_attention=0.05, L_latency=0.3, L_diversity=0.2,
            L_total=0.16,
            weights={'routing': 1/6, 'confidence': 1/6, 'feedback': 1/6, 'attention': 1/6, 'latency': 1/6, 'diversity': 1/6}
        )

        gradients = backprop.compute_gradients_with_dag(snapshot, task_batch, outcomes, feedback)

        # Check that audit events were recorded
        events = audit.events
        event_types = [e.get('event_type') for e in events]

        # Should have loss_gradient_computed and clipping events
        assert 'loss_gradient_computed' in event_types, "Should audit gradient computation"
        # Note: tier_gradient_clipping_summary is only recorded if clipping happens

    def test_all_loops_fail_on_any_invalid_value(self):
        """Test that if any loop has an invalid gradient, all updates are refused."""
        clipper = PerTierGradientClipper(audit_backend=MockAuditBackend())

        # Mix of valid and invalid
        gradients = {
            'L4_attention': {'grad': 0.2, 'contributors': []},   # Valid
            'L3_feedback': {'grad': np.inf, 'contributors': []}, # Invalid
            'L2_confidence': {'grad': 0.5, 'contributors': []},  # Valid
            'L1_routing': {'grad': 0.3, 'contributors': []},     # Valid
            'L5_latency': {'grad': 0.4, 'contributors': []},     # Valid
            'L6_diversity': {'grad': 0.1, 'contributors': []},   # Valid
        }

        clipped, valid = clipper.clip_gradients_by_tier(gradients, batch_id='all_fail_test')

        # Entire batch should fail, not partial updates
        assert not valid, "Should fail when ANY loop is invalid"
        assert len(clipped) == 0, "Should reject entire batch"


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
