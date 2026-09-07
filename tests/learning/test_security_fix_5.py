"""
Security Fix #5: Per-Tier Gradient Damping Tests

Mitigation: Cascading Divergence via Per-Tier Gradient Damping (Tiered α-Scaling)
ADR Reference: ADR-0617 (Phase 2 Mitigation)

Tests verify:
1. Gradients are scaled by tier damping factors
2. Meta-Skills tier responds slowly (α=0.01)
3. Infrastructure tier responds at medium speed (α=0.05)
4. Learning tier responds quickly (α=0.1)
5. Damping prevents oscillation in 500-sample convergence
6. Audit events are logged for every damped gradient
"""

import pytest
from datetime import datetime, timedelta
import numpy as np
from core.learning.unified_loss import (
    UnifiedLossOptimizer,
    TIER_DAMPING_FACTORS,
    COMPONENT_TIER_MAP,
)
from tests.learning.mock_audit_backend import MockAuditBackend


@pytest.fixture
def audit_backend():
    """Create mock audit backend."""
    return MockAuditBackend()


@pytest.fixture
def optimizer_with_damping(audit_backend):
    """Create optimizer with default tier damping."""
    return UnifiedLossOptimizer(
        tenant_id='_default',
        audit_backend=audit_backend,
        tier_damping=TIER_DAMPING_FACTORS,
    )


@pytest.fixture
def optimizer_no_damping(audit_backend):
    """Create optimizer with disabled tier damping (all α=1.0 for comparison)."""
    no_damping = {1: 1.0, 2: 1.0, 3: 1.0}
    return UnifiedLossOptimizer(
        tenant_id='_default',
        audit_backend=audit_backend,
        tier_damping=no_damping,
    )


# ============================================================================
# Test 1: Gradients Scaled by Tier Damping Factors
# ============================================================================

def test_tier_damping_applied(optimizer_with_damping):
    """Verify gradients are scaled by tier damping factors."""
    # Create loss deltas (positive = loss increased)
    loss_deltas = {
        'routing': 0.1,
        'confidence': 0.1,
        'feedback': 0.05,
        'attention': 0.05,
        'latency': 0.05,
        'diversity': 0.02,
    }

    gradients = optimizer_with_damping.compute_gradients(loss_deltas)
    new_weights, audit_event = optimizer_with_damping.apply_damped_gradients(
        gradients,
        learning_rate=1.0,
    )

    # Verify audit event structure
    assert audit_event['event_type'] == 'gradient_damped_by_tier'
    assert audit_event['tenant_id'] == '_default'
    assert 'damped_gradients' in audit_event

    # Verify damping was applied per tier
    damped = audit_event['damped_gradients']
    for component in ['routing', 'confidence']:  # Tier 3
        tier = COMPONENT_TIER_MAP[component]
        assert tier == 3
        assert damped[component]['damping_factor'] == pytest.approx(0.1)
        assert damped[component]['tier'] == 3

    for component in ['feedback', 'attention', 'latency']:  # Tier 2
        tier = COMPONENT_TIER_MAP[component]
        assert tier == 2
        assert damped[component]['damping_factor'] == pytest.approx(0.05)
        assert damped[component]['tier'] == 2

    for component in ['diversity']:  # Tier 1
        tier = COMPONENT_TIER_MAP[component]
        assert tier == 1
        assert damped[component]['damping_factor'] == pytest.approx(0.01)
        assert damped[component]['tier'] == 1


def test_tier_damping_calculation(optimizer_with_damping):
    """Verify damped gradient = α_tier * original_gradient."""
    loss_deltas = {
        'routing': 0.1,
        'confidence': 0.1,
        'feedback': 0.05,
        'attention': 0.05,
        'latency': 0.05,
        'diversity': 0.02,
    }

    gradients = optimizer_with_damping.compute_gradients(loss_deltas)
    new_weights, audit_event = optimizer_with_damping.apply_damped_gradients(
        gradients,
        learning_rate=1.0,
    )

    damped = audit_event['damped_gradients']

    # routing: Tier 3, α=0.1
    # original_gradient = -0.1 (descent), damped = 0.1 * (-0.1) = -0.01
    assert damped['routing']['original_gradient'] == pytest.approx(-0.1)
    assert damped['routing']['damped_gradient'] == pytest.approx(-0.01)

    # diversity: Tier 1, α=0.01
    # original_gradient = -0.02, damped = 0.01 * (-0.02) = -0.0002
    assert damped['diversity']['original_gradient'] == pytest.approx(-0.02)
    assert damped['diversity']['damped_gradient'] == pytest.approx(-0.0002)


# ============================================================================
# Test 2: Meta-Skills Tier Responds Slowly (α=0.01)
# ============================================================================

def test_meta_skills_tier_slow_response(optimizer_with_damping):
    """Verify Meta-Skills tier (Tier 1) updates weights very slowly."""
    # Diversity is Tier 1
    assert COMPONENT_TIER_MAP['diversity'] == 1

    initial_diversity_weight = optimizer_with_damping.weights['diversity']

    # Apply large gradient to diversity
    large_loss_delta = 0.5  # Large loss increase
    gradients = optimizer_with_damping.compute_gradients({'diversity': large_loss_delta})
    new_weights, _ = optimizer_with_damping.apply_damped_gradients(gradients, learning_rate=1.0)

    # Weight should barely move (damped by α=0.01)
    weight_change = abs(new_weights['diversity'] - initial_diversity_weight)
    # Damped gradient = 0.01 * 0.5 = 0.005, so change is very small
    assert weight_change < 0.01, f"Tier 1 weight changed too much: {weight_change}"


def test_meta_skills_tier_convergence_slow(optimizer_with_damping):
    """Verify Meta-Skills tier convergence requires many iterations."""
    # Simulate 10 iterations of gradient descent on diversity
    gradient_steps = 10
    target_weight = 0.05

    current_weight = optimizer_with_damping.weights['diversity']
    for _ in range(gradient_steps):
        loss_delta = (current_weight - target_weight) * 2  # Proportional error
        gradients = optimizer_with_damping.compute_gradients({'diversity': loss_delta})
        new_weights, _ = optimizer_with_damping.apply_damped_gradients(gradients, learning_rate=1.0)
        current_weight = new_weights['diversity']

    # After 10 steps with α=0.01, should not have converged yet
    # Each step moves by ~0.01 * error, so slow convergence
    assert abs(current_weight - target_weight) > 0.02, \
        f"Tier 1 converged too fast: {abs(current_weight - target_weight)}"


# ============================================================================
# Test 3: Infrastructure Tier Responds Medium Speed (α=0.05)
# ============================================================================

def test_infrastructure_tier_medium_response(optimizer_with_damping):
    """Verify Infrastructure tier (Tier 2) updates at medium speed."""
    # Feedback is Tier 2
    assert COMPONENT_TIER_MAP['feedback'] == 2

    initial_feedback_weight = optimizer_with_damping.weights['feedback']

    # Apply moderate gradient
    loss_delta = 0.2
    gradients = optimizer_with_damping.compute_gradients({'feedback': loss_delta})
    new_weights, _ = optimizer_with_damping.apply_damped_gradients(gradients, learning_rate=1.0)

    # Weight should move more than Tier 1, but less than Tier 3
    weight_change = abs(new_weights['feedback'] - initial_feedback_weight)
    # Damped gradient = 0.05 * 0.2 = 0.01
    assert 0.005 < weight_change < 0.02, f"Tier 2 weight change unexpected: {weight_change}"


def test_infrastructure_tier_convergence_medium(optimizer_with_damping):
    """Verify Infrastructure tier converges faster than Tier 1, slower than Tier 3."""
    gradient_steps = 20
    target_weight = 0.05

    current_weight = optimizer_with_damping.weights['feedback']
    for _ in range(gradient_steps):
        loss_delta = (current_weight - target_weight) * 2
        gradients = optimizer_with_damping.compute_gradients({'feedback': loss_delta})
        new_weights, _ = optimizer_with_damping.apply_damped_gradients(gradients, learning_rate=1.0)
        current_weight = new_weights['feedback']

    # After 20 steps with α=0.05, should be closer than Tier 1 after 10 steps
    assert abs(current_weight - target_weight) < 0.03


# ============================================================================
# Test 4: Learning Tier Responds Quickly (α=0.1)
# ============================================================================

def test_learning_tier_fast_response(optimizer_with_damping):
    """Verify Learning tier (Tier 3) responds quickly to gradients."""
    # Routing and Confidence are Tier 3
    assert COMPONENT_TIER_MAP['routing'] == 3
    assert COMPONENT_TIER_MAP['confidence'] == 3

    initial_routing_weight = optimizer_with_damping.weights['routing']

    # Apply gradient
    loss_delta = 0.2
    gradients = optimizer_with_damping.compute_gradients({'routing': loss_delta})
    new_weights, _ = optimizer_with_damping.apply_damped_gradients(gradients, learning_rate=1.0)

    # Weight should move noticeably (damped by α=0.1, which is largest)
    weight_change = abs(new_weights['routing'] - initial_routing_weight)
    assert weight_change > 0.015, f"Tier 3 weight change too small: {weight_change}"


def test_learning_tier_fast_convergence(optimizer_with_damping):
    """Verify Learning tier converges quickly."""
    gradient_steps = 20
    target_weight = 0.05

    current_weight = optimizer_with_damping.weights['routing']
    for _ in range(gradient_steps):
        loss_delta = (current_weight - target_weight) * 2
        gradients = optimizer_with_damping.compute_gradients({'routing': loss_delta})
        new_weights, _ = optimizer_with_damping.apply_damped_gradients(gradients, learning_rate=1.0)
        current_weight = new_weights['routing']

    # After 20 steps with α=0.1, should converge significantly
    assert abs(current_weight - target_weight) < 0.01, \
        f"Tier 3 did not converge fast enough: {abs(current_weight - target_weight)}"


# ============================================================================
# Test 5: Damping Prevents Oscillation (500-sample convergence test)
# ============================================================================

def test_damping_prevents_oscillation_500_samples(optimizer_with_damping, optimizer_no_damping):
    """
    Verify damping prevents oscillation in loss convergence over 500 samples.

    Simulates a learning scenario where:
    - Loss for each component fluctuates
    - Without damping: oscillation occurs
    - With damping: smooth convergence
    """
    samples = 500
    with_damping_losses = []
    no_damping_losses = []

    np.random.seed(42)

    # Target loss vector (what we're trying to reach)
    target_losses = {
        'routing': 0.15,
        'confidence': 0.12,
        'feedback': 0.10,
        'attention': 0.08,
        'latency': 0.18,
        'diversity': 0.05,
    }

    # Simulate optimization with damping
    for sample_idx in range(samples):
        # Perturb around target (add noise to simulate real fluctuations)
        current_losses = {
            k: target_losses[k] + np.random.normal(0, 0.02)
            for k in target_losses
        }

        # Compute deltas (how far off we are)
        deltas = {k: current_losses[k] for k in current_losses}
        gradients = optimizer_with_damping.compute_gradients(deltas)
        new_weights, _ = optimizer_with_damping.apply_damped_gradients(
            gradients,
            learning_rate=0.1,
        )

        # Compute aggregated loss (this should decrease over time)
        agg_loss = sum(
            optimizer_with_damping.weights[k] * current_losses[k]
            for k in current_losses
        )
        with_damping_losses.append(agg_loss)

    # Simulate optimization WITHOUT damping (α=1.0 for all tiers)
    for sample_idx in range(samples):
        current_losses = {
            k: target_losses[k] + np.random.normal(0, 0.02)
            for k in target_losses
        }
        deltas = {k: current_losses[k] for k in current_losses}
        gradients = optimizer_no_damping.compute_gradients(deltas)
        new_weights, _ = optimizer_no_damping.apply_damped_gradients(
            gradients,
            learning_rate=0.1,
        )

        agg_loss = sum(
            optimizer_no_damping.weights[k] * current_losses[k]
            for k in current_losses
        )
        no_damping_losses.append(agg_loss)

    # Analyze convergence smoothness
    with_damping_array = np.array(with_damping_losses)
    no_damping_array = np.array(no_damping_losses)

    # Compute variance of losses (higher variance = more oscillation)
    with_damping_variance = np.var(with_damping_array[100:])  # After 100 burn-in samples
    no_damping_variance = np.var(no_damping_array[100:])

    # Damped version should have lower variance (less oscillation)
    assert with_damping_variance < no_damping_variance, \
        f"Damped variance {with_damping_variance} >= no_damping {no_damping_variance}"

    # Damped convergence should be smoother (lower derivative variance)
    with_damping_deltas = np.diff(with_damping_array[100:])
    no_damping_deltas = np.diff(no_damping_array[100:])
    with_damping_delta_variance = np.var(with_damping_deltas)
    no_damping_delta_variance = np.var(no_damping_deltas)

    assert with_damping_delta_variance < no_damping_delta_variance, \
        f"Damped delta variance {with_damping_delta_variance} >= no_damping {no_damping_delta_variance}"


def test_damping_convergence_no_divergence(optimizer_with_damping):
    """Verify damped gradients converge without diverging."""
    samples = 100
    losses = []

    np.random.seed(42)
    target_loss = 0.15

    for _ in range(samples):
        current_loss = target_loss + np.random.normal(0, 0.05)

        # Create loss delta for all components
        loss_deltas = {k: current_loss for k in ['routing', 'confidence', 'feedback', 'attention', 'latency', 'diversity']}

        gradients = optimizer_with_damping.compute_gradients(loss_deltas)
        new_weights, _ = optimizer_with_damping.apply_damped_gradients(gradients, learning_rate=0.05)

        agg_loss = sum(optimizer_with_damping.weights[k] * loss_deltas[k] for k in loss_deltas)
        losses.append(agg_loss)

    # Loss should not diverge (grow unboundedly)
    max_loss = max(losses)
    assert max_loss < 1.0, f"Loss diverged: max_loss={max_loss}"


# ============================================================================
# Test 6: Audit Events Logged for Damped Gradients
# ============================================================================

def test_damping_audit_logged(optimizer_with_damping, audit_backend):
    """Verify gradient_damped_by_tier events are logged to audit chain."""
    loss_deltas = {
        'routing': 0.1,
        'confidence': 0.1,
        'feedback': 0.05,
        'attention': 0.05,
        'latency': 0.05,
        'diversity': 0.02,
    }

    gradients = optimizer_with_damping.compute_gradients(loss_deltas)
    new_weights, audit_event = optimizer_with_damping.apply_damped_gradients(
        gradients,
        learning_rate=1.0,
    )

    # Check audit event structure
    assert audit_event['event_type'] == 'gradient_damped_by_tier'
    assert audit_event['tenant_id'] == '_default'
    assert 'timestamp' in audit_event
    assert 'old_weights' in audit_event
    assert 'new_weights' in audit_event
    assert 'damped_gradients' in audit_event
    assert 'learning_rate' in audit_event
    assert 'tier_damping_factors' in audit_event

    # Verify audit event was written
    events = audit_backend.events
    last_event = events[-1] if events else None
    assert last_event is not None
    assert last_event['event_type'] == 'gradient_damped_by_tier'


def test_damping_audit_chain_integrity(optimizer_with_damping, audit_backend):
    """Verify audit chain is maintained across multiple damped gradient applications."""
    # Apply multiple gradient damping iterations
    for iteration in range(5):
        loss_deltas = {k: np.random.uniform(0, 0.1) for k in ['routing', 'confidence', 'feedback', 'attention', 'latency', 'diversity']}
        gradients = optimizer_with_damping.compute_gradients(loss_deltas)
        new_weights, _ = optimizer_with_damping.apply_damped_gradients(gradients, learning_rate=0.05)

    # Verify audit chain
    events = audit_backend.events
    damping_events = [e for e in events if e['event_type'] == 'gradient_damped_by_tier']

    # Should have 5 damping events
    assert len(damping_events) == 5

    # Verify each damping event has all required fields
    for event in damping_events:
        assert 'damped_gradients' in event
        for component in ['routing', 'confidence', 'feedback', 'attention', 'latency', 'diversity']:
            assert component in event['damped_gradients']
            grad_info = event['damped_gradients'][component]
            assert 'tier' in grad_info
            assert 'original_gradient' in grad_info
            assert 'damping_factor' in grad_info
            assert 'damped_gradient' in grad_info
            assert 'weight_delta' in grad_info


# ============================================================================
# Test 7: Validation Tests
# ============================================================================

def test_invalid_tier_damping_factor(audit_backend):
    """Verify invalid damping factors are rejected."""
    with pytest.raises(ValueError, match="damping factor.*must be in"):
        UnifiedLossOptimizer(
            tenant_id='_default',
            audit_backend=audit_backend,
            tier_damping={1: 1.5},  # Invalid: > 1.0
        )

    with pytest.raises(ValueError, match="damping factor.*must be in"):
        UnifiedLossOptimizer(
            tenant_id='_default',
            audit_backend=audit_backend,
            tier_damping={1: 0.0},  # Invalid: not > 0
        )


def test_invalid_tier_number(audit_backend):
    """Verify invalid tier numbers are rejected."""
    with pytest.raises(ValueError, match="tier must be 1, 2, or 3"):
        UnifiedLossOptimizer(
            tenant_id='_default',
            audit_backend=audit_backend,
            tier_damping={4: 0.05},  # Invalid: tier 4
        )


def test_invalid_learning_rate(optimizer_with_damping):
    """Verify invalid learning rates are rejected."""
    gradients = {'routing': -0.1}
    with pytest.raises(ValueError, match="learning_rate must be in"):
        optimizer_with_damping.apply_damped_gradients(gradients, learning_rate=0.0)

    with pytest.raises(ValueError, match="learning_rate must be in"):
        optimizer_with_damping.apply_damped_gradients(gradients, learning_rate=15.0)


# ============================================================================
# Test 8: Attack Simulation - Cascading Divergence
# ============================================================================

def test_attack_cascading_divergence_mitigated(optimizer_with_damping, optimizer_no_damping):
    """
    Simulate cascading divergence attack and verify damping mitigates it.

    Attack: Attacker injects large positive gradients to Learning tier (routing/confidence),
    which would normally propagate to Infrastructure tier and cause divergence.

    Mitigation: Per-tier damping isolates tiers, preventing cascade.
    """
    # Simulate attacker-injected large gradient to routing (Tier 3)
    attack_gradient_size = 0.5

    # Without damping: gradient propagates fully
    gradients_no_damping = {
        'routing': -attack_gradient_size,  # Large gradient
        'confidence': 0.0,
        'feedback': 0.0,
        'attention': 0.0,
        'latency': 0.0,
        'diversity': 0.0,
    }
    _, audit_no_damping = optimizer_no_damping.apply_damped_gradients(
        gradients_no_damping,
        learning_rate=1.0,
    )

    # With damping: gradient is dampened (α=0.1 for routing)
    gradients_with_damping = {
        'routing': -attack_gradient_size,
        'confidence': 0.0,
        'feedback': 0.0,
        'attention': 0.0,
        'latency': 0.0,
        'diversity': 0.0,
    }
    _, audit_with_damping = optimizer_with_damping.apply_damped_gradients(
        gradients_with_damping,
        learning_rate=1.0,
    )

    # Damped routing gradient should be much smaller
    damped_grad_with = audit_with_damping['damped_gradients']['routing']['damped_gradient']
    damped_grad_without = audit_no_damping['damped_gradients']['routing']['damped_gradient']

    # With damping (α=0.1): damped_grad ≈ 0.1 * (-0.5) = -0.05
    # Without damping (α=1.0): damped_grad ≈ 1.0 * (-0.5) = -0.5
    assert abs(damped_grad_with) < abs(damped_grad_without), \
        f"Damping did not reduce gradient: {damped_grad_with} >= {damped_grad_without}"

    # Weight change should be 10x smaller with damping
    weight_delta_with = audit_with_damping['damped_gradients']['routing']['weight_delta']
    weight_delta_without = audit_no_damping['damped_gradients']['routing']['weight_delta']
    assert abs(weight_delta_with) < abs(weight_delta_without) * 0.2, \
        f"Weight change not sufficiently damped: {weight_delta_with} vs {weight_delta_without}"


def test_attack_cascading_divergence_long_simulation(optimizer_with_damping, optimizer_no_damping):
    """
    Long-running simulation: compare stability of damped vs non-damped over 100 iterations
    under adversarial gradient injection.
    """
    iterations = 100
    with_damping_weights_history = []
    no_damping_weights_history = []

    np.random.seed(42)

    for i in range(iterations):
        # Simulate adversarial gradient injection at iteration 20 and 50
        is_attack_iter = i in [20, 50]
        attack_magnitude = 0.3 if is_attack_iter else 0.0

        # Create attack gradients (inject large positive gradient to learning tier)
        attack_gradients = {
            'routing': -attack_magnitude,
            'confidence': -attack_magnitude,
            'feedback': 0.01 * np.random.randn(),
            'attention': 0.01 * np.random.randn(),
            'latency': 0.01 * np.random.randn(),
            'diversity': 0.01 * np.random.randn(),
        }

        # Apply to damped optimizer
        _, _ = optimizer_with_damping.apply_damped_gradients(attack_gradients, learning_rate=0.05)
        with_damping_weights_history.append(dict(optimizer_with_damping.weights))

        # Apply to non-damped optimizer
        _, _ = optimizer_no_damping.apply_damped_gradients(attack_gradients, learning_rate=0.05)
        no_damping_weights_history.append(dict(optimizer_no_damping.weights))

    # Analyze stability
    with_damping_array = np.array([w['routing'] for w in with_damping_weights_history])
    no_damping_array = np.array([w['routing'] for w in no_damping_weights_history])

    # Damped version should have more stable routing weight (lower variance)
    with_damping_var = np.var(with_damping_array)
    no_damping_var = np.var(no_damping_array)

    assert with_damping_var < no_damping_var, \
        f"Damped routing weight not stable: var={with_damping_var} >= no_damping={no_damping_var}"

    # Damped version should have more stable weights after attack
    # (less oscillation/variance in the post-attack period)
    post_attack_with_var = np.var(with_damping_array[20:40])
    post_attack_without_var = np.var(no_damping_array[20:40])

    # Damped version should have lower variance after attack
    if post_attack_without_var > 0.0001:  # Only check if there's meaningful variance
        assert post_attack_with_var <= post_attack_without_var * 1.5, \
            f"Damped version did not stabilize after attack: var_with={post_attack_with_var} vs var_without={post_attack_without_var}"
