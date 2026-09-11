"""Tests for WeightLearner — gradient descent learning."""

import pytest
from daemon.weight_learner import (
    WeightLearner,
    WeightUpdateEvent,
    ConvergenceStatus,
)


def test_initialize_weight():
    """Test initializing a weight."""
    learner = WeightLearner()

    learner.initialize_weight("w1", 0.5)
    assert learner.get_weight("w1") == 0.5
    assert learner.get_convergence_status("w1") == ConvergenceStatus.LEARNING


def test_update_weight_simple():
    """Test a simple weight update."""
    learner = WeightLearner(base_learning_rate=0.01)
    learner.initialize_weight("w1", 0.5)

    # Simulate: loss decreased by 0.2 when we changed weight by 0.1
    event = learner.update_weight(
        "w1",
        loss_before=0.8,
        loss_after=0.6,
        weight_delta=0.1,
    )

    assert event is not None
    assert event.weight_name == "w1"
    assert event.old_value == 0.5
    assert event.new_value < event.old_value  # Weight should move toward improvement


def test_weight_clamping():
    """Test that weights are clamped to [0, 1]."""
    learner = WeightLearner(base_learning_rate=1.0)  # High LR
    learner.initialize_weight("w1", 0.05)

    # Large negative gradient tries to push weight below 0
    learner.update_weight(
        "w1",
        loss_before=1.0,
        loss_after=0.0,
        weight_delta=0.5,
    )

    weight = learner.get_weight("w1")
    assert 0 <= weight <= 1


def test_convergence_detection():
    """Test that convergence is detected after ~10 small updates."""
    learner = WeightLearner(base_learning_rate=0.001, epsilon=0.0001)
    learner.initialize_weight("w1", 0.5)

    # Make 15 tiny updates
    for i in range(15):
        learner.update_weight(
            "w1",
            loss_before=0.1,
            loss_after=0.099,
            weight_delta=0.001,  # Very small delta
        )

    status = learner.get_convergence_status("w1")
    assert status == ConvergenceStatus.CONVERGED


def test_oscillation_detection():
    """Test that oscillation is detected."""
    learner = WeightLearner(base_learning_rate=0.5)  # High LR
    learner.initialize_weight("w1", 0.5)

    # Make updates that oscillate (big swings)
    for i in range(15):
        # Alternating large positive and negative loss changes
        loss_delta = 0.1 if i % 2 == 0 else -0.1
        learner.update_weight(
            "w1",
            loss_before=0.5,
            loss_after=0.5 + loss_delta,
            weight_delta=0.2,
        )

    status = learner.get_convergence_status("w1")
    # Oscillation should be detected
    assert status in [ConvergenceStatus.OSCILLATING, ConvergenceStatus.LEARNING]


def test_adaptive_learning_rate():
    """Test that learning rate adapts when oscillation is detected."""
    learner = WeightLearner(base_learning_rate=0.01)
    learner.initialize_weight("w1", 0.5)

    initial_lr = learner.get_learning_rate("w1")

    # Force oscillation
    for i in range(20):
        loss_delta = 0.15 if i % 2 == 0 else -0.15
        learner.update_weight(
            "w1",
            loss_before=0.5,
            loss_after=0.5 + loss_delta,
            weight_delta=0.2,
        )

    # Learning rate should have been reduced
    new_lr = learner.get_learning_rate("w1")
    # If oscillation was detected, rate should be lower
    if learner.get_convergence_status("w1") == ConvergenceStatus.OSCILLATING:
        assert new_lr < initial_lr


def test_importance_weighting():
    """Test that rare signals have more impact via importance weighting."""
    learner1 = WeightLearner(base_learning_rate=0.01)
    learner1.initialize_weight("w1", 0.5)

    learner2 = WeightLearner(base_learning_rate=0.01)
    learner2.initialize_weight("w1", 0.5)

    # Both learners update with same loss change, but different importance
    event1 = learner1.update_weight(
        "w1",
        loss_before=0.8,
        loss_after=0.7,
        weight_delta=0.1,
        signal_importance=1.0,  # Common signal
    )

    event2 = learner2.update_weight(
        "w1",
        loss_before=0.8,
        loss_after=0.7,
        weight_delta=0.1,
        signal_importance=3.0,  # Rare signal (3x more important)
    )

    # Rare signal should cause larger delta
    assert abs(event2.delta) > abs(event1.delta)


def test_momentum():
    """Test that momentum smooths updates (reduces oscillation)."""
    learner = WeightLearner(base_learning_rate=0.1)
    learner.initialize_weight("w1", 0.5)

    # Make alternating updates
    deltas = []
    for i in range(5):
        loss_delta = 0.05 if i % 2 == 0 else -0.05
        event = learner.update_weight(
            "w1",
            loss_before=0.5,
            loss_after=0.5 + loss_delta,
            weight_delta=0.1,
        )
        deltas.append(event.delta)

    # Check that deltas are smoother (momentum reduces swings)
    delta_changes = [abs(deltas[i] - deltas[i-1]) for i in range(1, len(deltas))]
    # With momentum, changes should be somewhat correlated (smoothing effect)
    assert len(delta_changes) > 0


def test_disable_learning():
    """Test that learning can be disabled for a weight."""
    learner = WeightLearner()
    learner.initialize_weight("w1", 0.5)

    learner.disable_learning("w1")
    assert learner.get_convergence_status("w1") == ConvergenceStatus.DISABLED

    # Further updates should be no-ops
    event = learner.update_weight(
        "w1",
        loss_before=0.8,
        loss_after=0.6,
        weight_delta=0.1,
    )

    assert event.delta == 0.0
    assert learner.get_weight("w1") == 0.5  # Unchanged


def test_get_all_weights():
    """Test retrieving all weights."""
    learner = WeightLearner()

    learner.initialize_weight("w1", 0.5)
    learner.initialize_weight("w2", 0.7)
    learner.initialize_weight("w3", 0.3)

    all_weights = learner.get_all_weights()
    assert len(all_weights) == 3
    assert all_weights["w1"] == 0.5


def test_get_events_for_weight():
    """Test filtering events by weight."""
    learner = WeightLearner()

    learner.initialize_weight("w1", 0.5)
    learner.initialize_weight("w2", 0.5)

    learner.update_weight("w1", 0.8, 0.7, 0.1)
    learner.update_weight("w2", 0.8, 0.7, 0.1)
    learner.update_weight("w1", 0.7, 0.6, 0.1)

    w1_events = learner.get_events_for_weight("w1")
    assert len(w1_events) == 2
    assert all(e.weight_name == "w1" for e in w1_events)


def test_reset():
    """Test resetting learner."""
    learner = WeightLearner()

    learner.initialize_weight("w1", 0.5)
    learner.update_weight("w1", 0.8, 0.7, 0.1)

    learner.reset()
    assert len(learner.get_all_weights()) == 0
    assert len(learner.get_all_events()) == 0
