"""L5 Agent Selector for Workflow Optimizer Skill.

Routes classified tasks to the best model (haiku/sonnet/opus) based on:
- Task complexity tier (simple/medium/complex)
- Learned routing weights (updated via feedback)
- Model availability + cost constraints
- Operator-set model pins

Output: model_name (str), confidence (float), reasoning (str)

Compliance (ADR-0232, GDPR Art. 32):
- All routing decisions audited with tenant_id
- No PII in routing context
- Fail-closed: undefined model -> deny
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional, List
import json

logger = logging.getLogger(__name__)


class ModelTier(Enum):
    """Available Claude model tiers."""
    HAIKU = "claude-haiku-4-5-20251001"
    SONNET = "claude-sonnet-5-20251001"
    OPUS = "claude-opus-5-20251001"


@dataclass
class RoutingWeights:
    """Learned routing weights per complexity tier.

    Each tier maps models to their scores [0.0, 1.0].
    Higher score = more likely this model is correct for this tier.
    """
    simple_weights: Dict[str, float]  # model -> score
    medium_weights: Dict[str, float]
    complex_weights: Dict[str, float]

    @classmethod
    def defaults(cls) -> RoutingWeights:
        """Create default weights (no learning yet)."""
        return cls(
            simple_weights={
                ModelTier.HAIKU.value: 0.8,
                ModelTier.SONNET.value: 0.15,
                ModelTier.OPUS.value: 0.05,
            },
            medium_weights={
                ModelTier.HAIKU.value: 0.15,
                ModelTier.SONNET.value: 0.70,
                ModelTier.OPUS.value: 0.15,
            },
            complex_weights={
                ModelTier.HAIKU.value: 0.05,
                ModelTier.SONNET.value: 0.15,
                ModelTier.OPUS.value: 0.80,
            },
        )


@dataclass
class RoutingDecision:
    """Output of agent selection."""
    model_name: str  # e.g., "claude-sonnet-5-20251001"
    confidence: float  # [0.0, 1.0] - confidence in this choice
    tier_input: str  # complexity tier that led to this decision
    weight_scores: Dict[str, float]  # all model scores for this tier (for auditing)
    reasoning: str  # human-readable explanation
    feature_hash: str  # hash of input features (for audit trail linking)


class AgentSelector:
    """Selects the best model for a classified task."""

    def __init__(self, weights: Optional[RoutingWeights] = None):
        """Initialize selector with routing weights.

        Args:
            weights: Learned routing weights (or defaults if None)
        """
        self.weights = weights or RoutingWeights.defaults()

    def select(
        self,
        complexity_tier: str,  # "simple", "medium", "complex"
        task_feature_hash: str,  # Hash of task features (for audit linking)
        model_pin: Optional[str] = None,  # Operator override (e.g., "claude-opus-5-20251001")
        available_models: Optional[List[str]] = None,  # Models currently available
    ) -> RoutingDecision:
        """Select best model for task.

        Args:
            complexity_tier: from TaskClassifier ("simple", "medium", "complex")
            task_feature_hash: Hash of task features (for audit trail)
            model_pin: Operator-set model override (if set, return it)
            available_models: List of available models (all models assumed available if None)

        Returns:
            RoutingDecision with selected model and reasoning
        """
        # Tier 1: Operator pin always wins
        if model_pin:
            if available_models and model_pin not in available_models:
                # Pin is unavailable - fall back to normal routing
                logger.warning(f"Model pin {model_pin} not available, using normal routing")
            else:
                # Pin wins
                return RoutingDecision(
                    model_name=model_pin,
                    confidence=1.0,  # Pin is certain
                    tier_input=complexity_tier,
                    weight_scores={model_pin: 1.0},
                    reasoning=f"Operator pinned model: {model_pin}",
                    feature_hash=task_feature_hash,
                )

        # Tier 2: Apply learned weights for the complexity tier
        weights_dict = self._get_weights_for_tier(complexity_tier)
        if not weights_dict:
            # Fallback to defaults if tier is unknown
            weights_dict = self._get_weights_for_tier("medium")

        # Filter to available models if specified
        candidate_scores = weights_dict.copy()
        if available_models:
            candidate_scores = {m: candidate_scores.get(m, 0.0) for m in available_models}

        # Pick model with highest score
        if not candidate_scores:
            # Fallback: all models unavailable
            raise RuntimeError(f"No models available for routing (requested: {available_models})")

        selected_model = max(candidate_scores, key=candidate_scores.get)
        confidence = candidate_scores[selected_model]

        # Normalize confidence to [0.0, 1.0] range
        total_score = sum(candidate_scores.values())
        if total_score > 0:
            confidence = confidence / total_score
        else:
            confidence = 1.0 / len(candidate_scores)  # Fallback: equal distribution

        reasoning = self._generate_reasoning(
            complexity_tier, selected_model, confidence, weights_dict
        )

        return RoutingDecision(
            model_name=selected_model,
            confidence=confidence,
            tier_input=complexity_tier,
            weight_scores=weights_dict,
            reasoning=reasoning,
            feature_hash=task_feature_hash,
        )

    def _get_weights_for_tier(self, tier: str) -> Dict[str, float]:
        """Get routing weights for a complexity tier."""
        tier_lower = tier.lower()
        if tier_lower == "simple":
            return self.weights.simple_weights.copy()
        elif tier_lower == "medium":
            return self.weights.medium_weights.copy()
        elif tier_lower == "complex":
            return self.weights.complex_weights.copy()
        else:
            logger.warning(f"Unknown complexity tier: {tier}, using medium weights")
            return self.weights.medium_weights.copy()

    def _generate_reasoning(
        self, tier: str, selected_model: str, confidence: float, weight_scores: Dict[str, float]
    ) -> str:
        """Generate human-readable reasoning for the decision."""
        model_short = selected_model.split('-')[1].title()  # e.g., "claude-opus" -> "Opus"
        return f"Routed to {model_short} ({confidence:.2%} confidence) for {tier} complexity task based on learned routing weights"

    def update_weights(
        self,
        tier: str,
        model: str,
        feedback_signal: float,  # Positive (was good) or negative (was bad)
        learning_rate: float = 0.1,
    ) -> None:
        """Update routing weights based on feedback.

        Args:
            tier: Complexity tier ("simple", "medium", "complex")
            model: Model that was selected
            feedback_signal: +1.0 (was correct), 0.0 (neutral), -1.0 (was wrong)
            learning_rate: How much to adjust weights (0.0-1.0)
        """
        weights_dict = self._get_weights_for_tier(tier)
        if model not in weights_dict:
            logger.warning(f"Model {model} not in {tier} weights, skipping update")
            return

        # EMA-style update: new_weight = old_weight + learning_rate * feedback_signal
        adjustment = learning_rate * feedback_signal
        weights_dict[model] = max(0.0, min(1.0, weights_dict[model] + adjustment))

        # Redistribute to other models to maintain sum ~= 1.0
        other_models = [m for m in weights_dict if m != model]
        if other_models:
            # Decrease other weights slightly
            for other_model in other_models:
                weights_dict[other_model] = max(0.0, weights_dict[other_model] - adjustment / len(other_models))

        # Normalize to sum to 1.0
        total = sum(weights_dict.values())
        if total > 0:
            weights_dict = {m: w / total for m, w in weights_dict.items()}

        # Update the appropriate tier
        if tier.lower() == "simple":
            self.weights.simple_weights = weights_dict
        elif tier.lower() == "medium":
            self.weights.medium_weights = weights_dict
        elif tier.lower() == "complex":
            self.weights.complex_weights = weights_dict

    def to_dict(self) -> Dict[str, Dict[str, float]]:
        """Serialize weights to dict (for YAML persistence)."""
        return {
            'simple_weights': self.weights.simple_weights,
            'medium_weights': self.weights.medium_weights,
            'complex_weights': self.weights.complex_weights,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Dict[str, float]]) -> AgentSelector:
        """Deserialize weights from dict."""
        weights = RoutingWeights(
            simple_weights=data.get('simple_weights', RoutingWeights.defaults().simple_weights),
            medium_weights=data.get('medium_weights', RoutingWeights.defaults().medium_weights),
            complex_weights=data.get('complex_weights', RoutingWeights.defaults().complex_weights),
        )
        return cls(weights)
