"""Composite loss computation and attribution analysis (ADR-0028)."""
from __future__ import annotations

from typing import Any
import math


def compute_root_loss(
    sub_losses: dict[str, float | None],
    weights: dict[str, float],
    mode: str = "weighted_sum",
) -> float:
    """Compute root loss from sub-manager losses.

    sub_losses: manager_id -> primary_loss (None = not completed)
    weights: manager_id -> weight
    """
    if mode == "weighted_sum":
        total_w = 0.0
        total_l = 0.0
        for mid, loss in sub_losses.items():
            if loss is None:
                continue
            w = weights.get(mid, 1.0 / max(len(sub_losses), 1))
            total_l += w * loss
            total_w += w
        return (total_l / total_w) if total_w > 0 else 1.0
    elif mode == "pareto":
        # Root loss is max of sub_losses (worst case)
        losses = [l for l in sub_losses.values() if l is not None]
        return max(losses) if losses else 1.0
    elif mode == "cascade":
        # Last sub_loss if all others meet threshold (not supported fully, use weighted_sum)
        return compute_root_loss(sub_losses, weights, mode="weighted_sum")
    return 1.0


def compute_attribution(
    sub_losses: dict[str, float | None],
    weights: dict[str, float],
    mode: str = "weighted_sum",
) -> dict[str, float]:
    """Compute attribution fractions (sum to 1.0).

    Uses a leave-one-out Shapley approximation:
      attribution_i = (root_loss_without_i - root_loss_with_i) / normalization

    Returns manager_id -> fraction in [0, 1].
    """
    completed = {mid: l for mid, l in sub_losses.items() if l is not None}
    if not completed:
        return {}

    root = compute_root_loss(completed, weights, mode)

    # Leave-one-out Shapley approximation:
    # Replace sub_i with the best observed loss — attribution measures how much
    # the root loss would improve if this sub-manager were "fixed" (achieved best).
    best_loss = min(completed.values())
    attributions: dict[str, float] = {}

    for mid in completed:
        counterfactual = {**completed, mid: best_loss}
        cf_root = compute_root_loss(counterfactual, weights, mode)
        # Attribution = how much we gain by fixing this sub-manager
        attributions[mid] = max(0.0, root - cf_root)

    # Normalize to sum to 1.0
    total = sum(attributions.values())
    if total > 0:
        return {mid: v / total for mid, v in attributions.items()}
    # Fallback: proportional to raw loss
    total_loss = sum(completed.values())
    if total_loss > 0:
        return {mid: l / total_loss for mid, l in completed.items()}
    return {mid: 1.0 / len(completed) for mid in completed}


def check_convergence(
    root_loss_history: list[float],
    epsilon: float,
    window: int,
) -> bool:
    """Return True if root loss has converged (delta < epsilon over last `window` rounds)."""
    if len(root_loss_history) < window + 1:
        return False
    recent = root_loss_history[-(window + 1):]
    for i in range(1, len(recent)):
        if abs(recent[i] - recent[i - 1]) >= epsilon:
            return False
    return True
