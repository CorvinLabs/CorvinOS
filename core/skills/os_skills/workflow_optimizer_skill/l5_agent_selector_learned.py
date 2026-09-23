"""Stream 1 Phase 2: L5 Agent Selector with Learned Weights (ADR-0759).

Integrates learned routing weights into L5 (routing) decisions.
Extends AgentSelector to use learned P(correct routing | complexity, model)
instead of hardcoded thresholds.

**Contract:**
- Input: task_complexity, task_id, tenant_id, optional model_pin
- Process: Load learned weights → compute model probabilities → select best
- Output: RoutingDecision with model + confidence
- Audit: Every routing decision logged (SKILL_EXECUTED event)

**Operator Pin Override:**
- If operator manually selected a model (model_pin), use it (no learning override)
- Log override as INFO (audit trail captures it)

**Fallback:**
- If no learned weights exist, use hardcoded defaults from RoutingWeights()
- If weights file corrupted, fall back gracefully (fail-open for routing availability)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, List, Dict
from enum import Enum

from core.skills.os_skills.workflow_optimizer_skill.config_persistence import ConfigPersistence
from core.skills.os_skills.workflow_optimizer_skill.confidence_calculator import RoutingWeights

logger = logging.getLogger(__name__)


class ModelTier(str, Enum):
    """LLM model tiers (from workflow_optimizer/skill.py)."""
    HAIKU_4_5 = "haiku-4-5"
    SONNET_5 = "sonnet-5"
    OPUS_5 = "opus-5"


@dataclass(frozen=True)
class L5RoutingDecision:
    """Output of L5 learned routing decision.

    Immutable, audit-loggable.
    """
    task_id: str
    model: ModelTier
    complexity: str  # "simple", "medium", "complex"
    model_confidence: float  # P(correct | learned weights)
    classifier_confidence: float  # P(complexity | classifier)
    reasoning: str
    is_learned_routing: bool  # True if used learned weights, False if fallback
    decision_id: str = ""  # UUID for audit trail


class L5AgentSelectorLearned:
    """L5 Router with learned weight integration (Phase 2).

    **Workflow:**
    1. Load learned weights from disk (ConfigPersistence)
    2. Classify task complexity (from upstream classifier)
    3. Compute P(correct model | learned weights) for each model tier
    4. Apply operator pin override (if present)
    5. Return routing decision with confidence

    **Learning Integration:**
    - Routing decision logged as SKILL_EXECUTED event (audit trail)
    - Later, operator feedback (outcome_feedback) updates weights
    - Next routing uses updated weights
    - Feedback → update latency target: <500ms

    **Graceful Degradation:**
    - No weights file: fall back to hardcoded defaults (fail-open)
    - Corrupted weights: log error + use defaults
    - Unknown task complexity: default to MEDIUM tier
    """

    def __init__(self, tenant_id: str, config_dir: Optional[str] = None):
        """Initialize L5 router with learned weights.

        Args:
            tenant_id: Tenant for weight loading
            config_dir: Config directory (default: tenant home)
        """
        self.tenant_id = tenant_id
        self.config_persistence = ConfigPersistence(config_dir=config_dir)
        self.weights: Optional[RoutingWeights] = None
        self._load_weights_lazy()

    def _load_weights_lazy(self) -> None:
        """Load weights on first use (lazy initialization)."""
        try:
            self.weights = self.config_persistence.load_current_weights()
            logger.info(
                f"L5AgentSelectorLearned loaded weights v{self.weights.version} "
                f"(feedback_count={self.weights.feedback_count})"
            )
        except Exception as e:
            logger.warning(f"Failed to load weights: {e}, using defaults")
            self.weights = RoutingWeights()

    def select_model(
        self,
        task_id: str,
        task_complexity: str,
        classifier_confidence: float,
        model_pin: Optional[str] = None,
        available_models: Optional[List[str]] = None,
    ) -> L5RoutingDecision:
        """Select model using learned weights (Phase 2).

        **Algorithm:**
        1. Load/ensure weights loaded
        2. If model_pin present, honor it (no learning override)
        3. Compute P(correct | complexity, model) for each available model
        4. Select model with highest probability
        5. Return decision with confidence + reasoning

        Args:
            task_id: Task identifier (for audit)
            task_complexity: "simple", "medium", or "complex"
            classifier_confidence: Confidence from upstream classifier (0.0-1.0)
            model_pin: Operator override (if provided, use this model)
            available_models: List of available model names (default: all 3)

        Returns:
            L5RoutingDecision with model selection + confidence
        """
        if self.weights is None:
            self._load_weights_lazy()

        available_models = available_models or [
            ModelTier.HAIKU_4_5.value,
            ModelTier.SONNET_5.value,
            ModelTier.OPUS_5.value,
        ]

        # Normalize task complexity (fail-safe)
        complexity = (task_complexity or "medium").lower()
        if complexity not in ("simple", "medium", "complex"):
            logger.warning(f"Unknown complexity {complexity!r}, defaulting to 'medium'")
            complexity = "medium"

        # Honor operator pin (manual override beats learning)
        if model_pin:
            logger.info(
                f"L5 routing: operator pin overrides learned weights "
                f"(pin={model_pin}, complexity={complexity})"
            )
            return L5RoutingDecision(
                task_id=task_id,
                model=self._normalize_model(model_pin),
                complexity=complexity,
                model_confidence=0.5,  # Pin = not learned
                classifier_confidence=classifier_confidence,
                reasoning=f"Operator pinned model to {model_pin}",
                is_learned_routing=False,
            )

        # Compute learned model probabilities
        model_scores: Dict[str, float] = {}
        for model_name in available_models:
            model_short = model_name.split("-")[0].lower()  # "sonnet-5" -> "sonnet"
            confidence = self.weights.get_confidence(complexity, model_short)
            model_scores[model_name] = confidence
            logger.debug(
                f"L5 routing: {complexity}_{model_short} → "
                f"P(correct)={confidence:.3f}"
            )

        # Select model with highest confidence
        best_model = max(model_scores.items(), key=lambda x: x[1])
        model_name, model_confidence = best_model

        reasoning = (
            f"Learned routing: complexity={complexity}, "
            f"P(correct)={model_confidence:.3f} (v{self.weights.version})"
        )

        return L5RoutingDecision(
            task_id=task_id,
            model=self._normalize_model(model_name),
            complexity=complexity,
            model_confidence=model_confidence,
            classifier_confidence=classifier_confidence,
            reasoning=reasoning,
            is_learned_routing=True,
        )

    def update_weights(self) -> None:
        """Reload weights from disk (called after ConfidenceCalculator updates).

        Useful for hot-reloading learned weights without restart.
        """
        try:
            self.weights = self.config_persistence.load_current_weights()
            logger.info(f"L5 weights reloaded: v{self.weights.version}")
        except Exception as e:
            logger.warning(f"Failed to reload weights: {e}")

    def get_weight_info(self) -> Dict[str, str]:
        """Get info about current weights (for console diagnostics).

        Returns:
            Dict with version, timestamp, feedback_count
        """
        if self.weights is None:
            self._load_weights_lazy()

        return {
            "version": self.weights.version,
            "updated_at": self.weights.updated_at,
            "feedback_count": str(self.weights.feedback_count),
        }

    @staticmethod
    def _normalize_model(model_name: str) -> ModelTier:
        """Normalize model name to ModelTier enum.

        Args:
            model_name: e.g., "sonnet-5", "sonnet", "claude-sonnet-5"

        Returns:
            ModelTier.SONNET_5 or similar

        Raises:
            ValueError: Model name not recognized
        """
        normalized = model_name.lower().replace("claude-", "")

        if "haiku" in normalized:
            return ModelTier.HAIKU_4_5
        elif "sonnet" in normalized:
            return ModelTier.SONNET_5
        elif "opus" in normalized:
            return ModelTier.OPUS_5
        else:
            raise ValueError(f"Unknown model name: {model_name!r}")
