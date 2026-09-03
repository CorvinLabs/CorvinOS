"""Confidence update algorithm (Phase 1): Bayesian blending."""
from __future__ import annotations
from .models import TreeNode, LearningEvent, ConfidenceEvent, CompositionType
from datetime import datetime

# ADR-0387: Confidence-Gated Memory threshold
MEMORY_CONFIDENCE_THRESHOLD = 0.5


def update_confidence(node: TreeNode, event: LearningEvent) -> float:
    """
    Bayesian update: blend event with prior.
    
    new_confidence = 0.7 * old_conf + 0.3 * (old_conf + event_delta)
    
    Returns: new confidence ∈ [0.0, 1.0]
    """
    old_conf = node.confidence

    # Antipattern detected in anti_when context: strong penalty.
    # LearningEvent is frozen — derive the effective delta, never assign to it.
    delta = -0.3 if event.event_type == "antipattern_detected" else event.confidence_delta

    # Bayesian blend: 70% prior, 30% new evidence
    alpha = 0.3
    new_conf = (1 - alpha) * old_conf + alpha * clip(
        old_conf + delta,
        0.0, 1.0
    )
    
    # For composite nodes: re-compute from children
    if node.level in ("method", "framework") and node.children:
        child_confs = []
        # In production, get children from store
        # For now, assume they're already computed
        if child_confs:
            new_conf = aggregate_children(node, child_confs)
    
    # Record confidence change
    conf_event = ConfidenceEvent(
        timestamp=datetime.now().isoformat(),
        old_confidence=old_conf,
        new_confidence=new_conf,
        delta=new_conf - old_conf,
        event_type=event.event_type,
        reason=event.reason,
        context=event.context,
    )
    node.add_confidence_event(conf_event)
    
    return new_conf


def aggregate_children(node: TreeNode, child_confs: dict[str, float]) -> float:
    """Aggregate child confidences per composition type."""
    if not child_confs:
        return node.confidence
    
    confs = list(child_confs.values())
    
    if node.composition_type == CompositionType.AND:
        return min(confs)  # All must work
    elif node.composition_type == CompositionType.OR:
        return max(confs)  # Any works
    else:  # AVG
        return sum(confs) / len(confs)


def apply_decay(confidence: float, days_unused: int, decay_rate: float = 0.1) -> float:
    """Decay confidence for unused patterns."""
    decay = decay_rate * (days_unused // 7)  # 0.1 per week
    return max(0.0, confidence - decay)


def clip(value: float, min_val: float, max_val: float) -> float:
    """Clamp value to [min_val, max_val]."""
    return max(min_val, min(max_val, value))
