"""
Multi-Model Routing for ADR-0377 Phase 3
Ranks models by cost, latency, accuracy per task type.
Supports Gemini, Llama (Ollama), Haiku, Sonnet, Opus.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class ModelTier(Enum):
    """Model performance tier."""
    FAST_CHEAP = "fast_cheap"      # Haiku, Gemini Flash
    BALANCED = "balanced"            # Sonnet, Gemini Pro
    BEST_QUALITY = "best_quality"    # Opus
    LOCAL_FREE = "local_free"        # Ollama/Llama local


@dataclass(frozen=True)
class ModelProfile:
    """Cost, latency, accuracy profile for a model."""
    model_id: str                # "haiku", "sonnet", "opus", "gemini-1.5-pro", "llama-7b"
    provider: str                # "anthropic", "google", "ollama"
    tier: ModelTier

    # Pricing (in USD)
    cost_per_1k_tokens: float    # Average cost per 1000 tokens

    # Performance
    latency_ms: float            # Typical response time in milliseconds
    accuracy: float              # Relative accuracy (0.0-1.0, normalized vs Opus=1.0)

    # Constraints
    max_context_tokens: int      # Max input context window
    max_output_tokens: int       # Max output tokens

    # Task suitability (0.0-1.0)
    code_review_aptitude: float
    research_aptitude: float
    summary_aptitude: float
    refactor_aptitude: float


# Pre-defined model profiles (based on empirical data)
MODEL_PROFILES: Dict[str, ModelProfile] = {
    # Claude/Anthropic
    "claude-3-5-haiku": ModelProfile(
        model_id="claude-3-5-haiku-20241022",
        provider="anthropic",
        tier=ModelTier.FAST_CHEAP,
        cost_per_1k_tokens=0.0042,  # ~$0.80/M input, $4/M output avg
        latency_ms=200,
        accuracy=0.88,
        max_context_tokens=200_000,
        max_output_tokens=4_096,
        code_review_aptitude=0.85,
        research_aptitude=0.70,
        summary_aptitude=0.90,
        refactor_aptitude=0.80,
    ),
    "claude-3-5-sonnet": ModelProfile(
        model_id="claude-3-5-sonnet-20241022",
        provider="anthropic",
        tier=ModelTier.BALANCED,
        cost_per_1k_tokens=0.0090,  # ~$3/M input, $15/M output avg
        latency_ms=500,
        accuracy=0.95,
        max_context_tokens=200_000,
        max_output_tokens=4_096,
        code_review_aptitude=0.95,
        research_aptitude=0.92,
        summary_aptitude=0.94,
        refactor_aptitude=0.94,
    ),
    "claude-3-opus": ModelProfile(
        model_id="claude-3-opus-20240229",
        provider="anthropic",
        tier=ModelTier.BEST_QUALITY,
        cost_per_1k_tokens=0.0450,  # ~$15/M input, $75/M output avg
        latency_ms=1500,
        accuracy=1.0,  # Reference baseline
        max_context_tokens=200_000,
        max_output_tokens=4_096,
        code_review_aptitude=1.0,
        research_aptitude=1.0,
        summary_aptitude=1.0,
        refactor_aptitude=1.0,
    ),

    # Google Gemini
    "gemini-1.5-flash": ModelProfile(
        model_id="gemini-1.5-flash",
        provider="google",
        tier=ModelTier.FAST_CHEAP,
        cost_per_1k_tokens=0.001125,  # ~$0.075/M input, $0.30/M output avg
        latency_ms=300,
        accuracy=0.82,
        max_context_tokens=1_000_000,
        max_output_tokens=8_000,
        code_review_aptitude=0.75,
        research_aptitude=0.60,
        summary_aptitude=0.85,
        refactor_aptitude=0.70,
    ),
    "gemini-1.5-pro": ModelProfile(
        model_id="gemini-1.5-pro",
        provider="google",
        tier=ModelTier.BALANCED,
        cost_per_1k_tokens=0.003750,  # ~$1.50/M input, $6/M output avg
        latency_ms=600,
        accuracy=0.93,
        max_context_tokens=1_000_000,
        max_output_tokens=8_000,
        code_review_aptitude=0.90,
        research_aptitude=0.88,
        summary_aptitude=0.92,
        refactor_aptitude=0.89,
    ),

    # Ollama/Llama (local, free)
    "llama-2-7b": ModelProfile(
        model_id="llama2:7b",
        provider="ollama",
        tier=ModelTier.LOCAL_FREE,
        cost_per_1k_tokens=0.0,  # Free (local compute)
        latency_ms=2000,  # Slower (local CPU)
        accuracy=0.65,  # Lower accuracy than commercial models
        max_context_tokens=4_096,
        max_output_tokens=2_048,
        code_review_aptitude=0.50,
        research_aptitude=0.40,
        summary_aptitude=0.70,
        refactor_aptitude=0.45,
    ),
}


@dataclass(frozen=True)
class ModelRanking:
    """Ranked models for a task with rationale."""
    task_type: str
    quality_threshold: float  # Minimum accuracy required (0.0-1.0)

    # Ranked candidates (best to worst)
    ranked_models: List[Tuple[str, float, str]] = field(default_factory=list)  # (model_id, score, reason)

    # Recommended model
    recommended_model: str = ""
    recommended_reason: str = ""

    # Fallback chain
    fallback_chain: List[str] = field(default_factory=list)


class MultiModelRouter:
    """Ranks models by cost, quality, and task suitability."""

    def __init__(self, profiles: Optional[Dict[str, ModelProfile]] = None):
        """Initialize router with model profiles."""
        self.profiles = profiles or MODEL_PROFILES

    def rank_models(
        self,
        task_type: str,
        quality_threshold: float = 0.85,
        max_cost_per_1k: Optional[float] = None,
    ) -> ModelRanking:
        """
        Rank models for a task.

        Args:
            task_type: "code_review", "research", "summary", "refactor"
            quality_threshold: Minimum accuracy (0.0-1.0)
            max_cost_per_1k: Max cost per 1000 tokens (optional)

        Returns:
            ModelRanking with ranked candidates
        """
        # Get aptitude for this task type
        aptitude_key = f"{task_type}_aptitude"

        # Filter models by quality threshold
        candidates = []
        for model_id, profile in self.profiles.items():
            if profile.accuracy < quality_threshold:
                continue  # Below threshold

            # Get aptitude (default to 0.5 if task type unknown)
            aptitude = getattr(profile, aptitude_key, 0.5)

            # Respect cost limit
            if max_cost_per_1k and profile.cost_per_1k_tokens > max_cost_per_1k:
                continue

            candidates.append((model_id, profile, aptitude))

        # If no candidates meet quality threshold, relax it
        if not candidates:
            logger.warning(f"No models meet quality threshold {quality_threshold} for {task_type}; relaxing")
            for model_id, profile in self.profiles.items():
                aptitude = getattr(profile, aptitude_key, 0.5)
                if max_cost_per_1k and profile.cost_per_1k_tokens > max_cost_per_1k:
                    continue
                candidates.append((model_id, profile, aptitude))

        # Score candidates: accuracy + aptitude - cost penalty
        scored = []
        for model_id, profile, aptitude in candidates:
            # Normalize cost as penalty (0.0 = free, 1.0 = most expensive)
            max_cost = max(p.cost_per_1k_tokens for p in self.profiles.values())
            cost_penalty = (profile.cost_per_1k_tokens / max_cost) if max_cost > 0 else 0.0

            # Score = accuracy + aptitude - 0.3 * cost_penalty
            # Higher is better
            score = (profile.accuracy + aptitude) / 2.0 - (0.3 * cost_penalty)
            scored.append((model_id, score, profile))

        # Sort by score (descending)
        scored.sort(key=lambda x: x[1], reverse=True)

        # Build ranked list
        ranked = []
        for model_id, score, profile in scored:
            reason = self._score_reason(profile, task_type)
            ranked.append((model_id, score, reason))

        # Recommended model (top ranked)
        if ranked:
            recommended_model, rec_score, rec_reason = ranked[0]
        else:
            recommended_model = "claude-3-5-sonnet"
            rec_reason = "Fallback: no candidates met quality threshold"

        # Fallback chain (top 3, excluding recommended)
        fallback = [m for m, _, _ in ranked[1:4]]

        return ModelRanking(
            task_type=task_type,
            quality_threshold=quality_threshold,
            ranked_models=ranked,
            recommended_model=recommended_model,
            recommended_reason=rec_reason,
            fallback_chain=fallback,
        )

    def _score_reason(self, profile: ModelProfile, task_type: str) -> str:
        """Generate human-readable reason for model recommendation."""
        aptitude_key = f"{task_type}_aptitude"
        aptitude = getattr(profile, aptitude_key, 0.5)

        if profile.tier == ModelTier.FAST_CHEAP:
            return f"Fast & cheap ({profile.cost_per_1k_tokens:.5f}/1K tokens, {aptitude*100:.0f}% apt)"
        elif profile.tier == ModelTier.BALANCED:
            return f"Balanced cost/quality ({profile.cost_per_1k_tokens:.5f}/1K tokens, {aptitude*100:.0f}% apt)"
        elif profile.tier == ModelTier.BEST_QUALITY:
            return f"Best quality ({profile.accuracy*100:.0f}% acc, {aptitude*100:.0f}% apt)"
        elif profile.tier == ModelTier.LOCAL_FREE:
            return f"Free local compute (0/1K tokens, {aptitude*100:.0f}% apt)"
        else:
            return "Unknown tier"

    def estimate_task_cost(
        self,
        model_id: str,
        estimated_input_tokens: int,
        estimated_output_tokens: int,
    ) -> float:
        """Estimate cost for a model."""
        if model_id not in self.profiles:
            return 0.0

        profile = self.profiles[model_id]
        total_tokens = estimated_input_tokens + estimated_output_tokens

        return profile.cost_per_1k_tokens * (total_tokens / 1000.0)

    def get_model_profile(self, model_id: str) -> Optional[ModelProfile]:
        """Get profile for a specific model."""
        return self.profiles.get(model_id)


def default_router() -> MultiModelRouter:
    """Get the default multi-model router."""
    return MultiModelRouter()
