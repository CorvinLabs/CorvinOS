"""Hard gradient clipping + NaN/Inf detection — Fix #4, retested for real.

Attack shape (from the archived probe script): craft gradients large enough to
escape the intended bounds, or non-finite, and see whether the weight update is
applied anyway.

Two things were untested and one of them was broken:

* `GradientValidator` itself had **no caller anywhere in the tree**, so its
  NaN/Inf detection had no subject. It is now called from
  `NineD_LossOptimizer._step_loop` (round-4 review, F1/F7).
* NaN/Inf arriving through `feedback` never reached the validator at all: the
  9D optimizer clamped every loss with ``max(0.0, min(1.0, x))``, and that
  expression returns a NUMBER for NaN, so a poisoned batch produced a finite
  `L_total` with no error and no divergence event. Detection now replaces the
  clamp (`NonFiniteLossError`).
"""
from __future__ import annotations

import math

import pytest

from core.learning.gradient_backprop import GradientValidator
from core.learning.nine_d_loss import NineD_LossOptimizer, NonFiniteLossError


def _feedback(quality: float = 0.3):
    return {
        "memory": {"missing_context_ratio": quality, "irrelevance_score": quality / 2},
        "skills": {"composition_error_rate": quality},
        "plugins": {"quality_gain": 1.0 - quality},
    }


# ── the validator's own contract ────────────────────────────────────────────


def test_oversized_gradients_are_clipped_to_the_bound():
    validator = GradientValidator(max_gradient=1.0)
    clipped, ok = validator.validate_and_clip_gradients(
        {"a": {"grad": 1e9}, "b": {"grad": -1e9}, "c": {"grad": 0.25}}, batch_id="b1"
    )
    assert ok is True
    assert clipped["a"]["grad"] == 1.0
    assert clipped["b"]["grad"] == -1.0
    assert clipped["c"]["grad"] == 0.25
    assert clipped["a"]["was_clipped"] is True
    assert clipped["c"]["was_clipped"] is False


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_gradients_are_refused_not_clipped(bad):
    """Clipping a NaN would produce a plausible number — refuse instead."""
    validator = GradientValidator(max_gradient=1.0)
    clipped, ok = validator.validate_and_clip_gradients(
        {"a": {"grad": 0.1}, "poison": {"grad": bad}}, batch_id="b2"
    )
    assert ok is False, "a non-finite gradient must invalidate the whole batch"
    assert clipped == {}, "a refused batch must yield nothing appliable"
    assert validator.get_stats()["num_validation_failures"] == 1


# ── the validator is actually on the 9D path ────────────────────────────────


def test_the_9d_optimizer_runs_gradients_through_the_validator():
    """Proved by SUBSTITUTION: a validator that refuses must stop the step.

    Asserting that a counter moved would be weak (it can stay 0 legitimately);
    swapping in a validator that always refuses proves the call is on the path.
    """
    optimizer = NineD_LossOptimizer()
    assert isinstance(optimizer.gradient_validator, GradientValidator)

    calls = []

    class _AlwaysRefuses(GradientValidator):
        def validate_and_clip_gradients(self, gradients, batch_id):
            calls.append(batch_id)
            return {}, False

    optimizer.gradient_validator = _AlwaysRefuses()
    history_before = len(optimizer.loss_history)

    with pytest.raises(NonFiniteLossError):
        optimizer.step(_feedback())

    assert calls, "the 9D step never called the gradient validator"
    assert len(optimizer.loss_history) == history_before
    assert optimizer.invalid_gradient_batches == 1


@pytest.mark.parametrize("section,key", [
    ("memory", "missing_context_ratio"),
    ("skills", "composition_error_rate"),
    ("plugins", "quality_gain"),
])
@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_non_finite_feedback_raises_instead_of_being_clamped(section, key, bad):
    optimizer = NineD_LossOptimizer()
    optimizer.step(_feedback())
    steps_before = optimizer.step_count
    history_before = len(optimizer.loss_history)

    poisoned = _feedback()
    poisoned[section][key] = bad
    with pytest.raises(NonFiniteLossError):
        optimizer.step(poisoned)

    assert len(optimizer.loss_history) == history_before, (
        "a poisoned batch must not append a loss value"
    )
    assert optimizer.step_count == steps_before + 1  # the attempt is counted


def test_non_finite_core_loop_loss_is_refused():
    optimizer = NineD_LossOptimizer()
    before = dict(optimizer.core_loop_losses)
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(NonFiniteLossError):
            optimizer.update_core_loop_loss("routing", bad)
    assert optimizer.core_loop_losses == before, "no smoothing may have happened"


def test_a_clean_run_still_produces_a_finite_loss():
    """The counter-test: the guard must not reject legitimate batches."""
    optimizer = NineD_LossOptimizer()
    for batch in range(20):
        loss = optimizer.step(_feedback(0.4 - batch * 0.005))
        assert math.isfinite(loss) and 0.0 <= loss <= 1.0
