"""
Phase 2: Model Selector Skill (ADR-0641, ADR-0642)

Determines which model (Anthropic/Ollama/OpenRouter/OpenAI) to use for a task.
Deterministic classification: token count + keywords + reasoning depth.
"""

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .feature_extractor import FeatureExtractor, ExtractedFeatures

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelSelectorConfig:
    """Configuration for model selection (immutable, audit-safe)."""
    # Thresholds for SIMPLE/MEDIUM/COMPLEX classification
    simple_max_tokens: int = 500
    simple_max_code_blocks: int = 1
    simple_max_dependencies: int = 2

    medium_max_tokens: int = 3000
    medium_max_code_blocks: int = 5
    medium_max_dependencies: int = 10

    # Cost preferences (SIMPLE → cheap, COMPLEX → best quality)
    prefer_local_for_simple: bool = True
    fallback_chain: str = "openrouter,anthropic,openai"  # Order of fallback

    # Timeout config (ADR-0643)
    provider_timeout_seconds: int = 30
    cost_limit_per_task: float = 5.0  # Max USD per task


class ClassificationResult:
    """Result of task complexity classification."""

    def __init__(
        self,
        complexity: str,  # "simple" | "medium" | "complex"
        confidence: float,  # 0.0-1.0
        recommended_provider: str,  # "anthropic" | "ollama" | "openrouter" | "openai"
        recommended_model: str,
        reasoning: str,
        features: ExtractedFeatures,
    ):
        self.complexity = complexity
        self.confidence = confidence
        self.recommended_provider = recommended_provider
        self.recommended_model = recommended_model
        self.reasoning = reasoning
        self.features = features

    def to_dict(self) -> Dict:
        """Audit-safe serialization (no PII, no prompts)."""
        return {
            "complexity": self.complexity,
            "confidence": self.confidence,
            "recommended_provider": self.recommended_provider,
            "recommended_model": self.recommended_model,
            "reasoning": self.reasoning,
            "features": self.features.to_dict(),
        }


class ModelSelector:
    """
    Select optimal model for a task.

    Decision Tree (ADR-0642):
    1. Extract features (deterministic)
    2. Classify complexity (SIMPLE/MEDIUM/COMPLEX)
    3. Map to provider (cost-optimized)
    4. Select model within provider
    5. Return audit event data
    """

    def __init__(
        self,
        config: Optional[ModelSelectorConfig] = None,
        overrides: Optional[Dict[str, Dict[str, Optional[str]]]] = None,
    ):
        self.config = config or ModelSelectorConfig()
        # Operator-set persisted choice per complexity tier — {"simple": {"provider":
        # "ollama_local"|None, "model": "..."}, "medium": {...}, "complex": {...}}.
        # Consulted BEFORE the hardcoded _select_provider/_select_model_for_provider
        # rules below, so a saved console preference (core/console/corvin_console/
        # routes/engine_api.py) has a real, observable effect on future
        # classifications instead of being cosmetic-only.
        self.overrides = overrides or {}
        self.feature_extractor = FeatureExtractor()
        self.classification_history: List[ClassificationResult] = []

    def classify(
        self,
        task_input: str,
        tenant_id: Optional[str] = None,
    ) -> ClassificationResult:
        """
        Classify task and recommend model.

        Returns:
            ClassificationResult with complexity, confidence, provider, model
        """
        # Step 1: Extract features
        features = self.feature_extractor.extract(task_input, tenant_id)

        # Step 2: Decision tree for complexity classification
        complexity, confidence = self._classify_complexity(features)

        # Step 3+4: operator override wins outright; else the cost-optimized
        # provider/model rule. `complexity in self.overrides` (not truthiness
        # of the override dict) is the presence check: provider=None is a
        # valid, deliberate override meaning "native Anthropic" and must NOT
        # fall through to _select_provider's own default (e.g. "ollama" for
        # simple) just because it's falsy.
        if complexity in self.overrides:
            override = self.overrides[complexity]
            provider = override.get("provider")
            model = override.get("model") or self._select_model_for_provider(provider, complexity)
        else:
            provider = self._select_provider(complexity)
            model = self._select_model_for_provider(provider, complexity)

        # Step 5: Build reasoning
        reasoning = self._build_reasoning(features, complexity, provider, model)

        result = ClassificationResult(
            complexity=complexity,
            confidence=confidence,
            recommended_provider=provider,
            recommended_model=model,
            reasoning=reasoning,
            features=features,
        )

        # Track for learning (Phase 2+)
        self.classification_history.append(result)

        return result

    def _classify_complexity(self, features: ExtractedFeatures) -> Tuple[str, float]:
        """
        Classify complexity using decision tree.

        Decision Rules (ADR-0642):
        SIMPLE: token_count < 500 AND code_blocks <= 1 AND dependencies <= 2
        COMPLEX: token_count > 3000 OR code_blocks > 5 OR dependencies > 10
        MEDIUM: else
        """
        # Start with keyword-based hint
        base_complexity = features.keyword_complexity

        # Apply token-based rules
        if features.token_estimate < self.config.simple_max_tokens:
            if (features.code_blocks <= self.config.simple_max_code_blocks
                    and features.dependency_count <= self.config.simple_max_dependencies):
                return "simple", 0.85  # High confidence
        elif features.token_estimate > self.config.medium_max_tokens:
            if (features.code_blocks > self.config.medium_max_code_blocks
                    or features.dependency_count > self.config.medium_max_dependencies):
                return "complex", 0.90  # High confidence

        # Medium category (default, medium confidence)
        if base_complexity == "complex":
            return "complex", 0.70
        elif base_complexity == "simple":
            return "simple", 0.70
        else:
            return "medium", 0.60  # Uncertain

    def _select_provider(self, complexity: str) -> str:
        """
        Select provider based on complexity.

        SIMPLE → Ollama (local, free) if available, else OpenRouter
        MEDIUM → OpenRouter (cost/quality balance)
        COMPLEX → Anthropic/OpenAI (best quality)
        """
        if complexity == "simple":
            # Prefer local Ollama for simple tasks
            if self.config.prefer_local_for_simple:
                return "ollama"
            return "openrouter"
        elif complexity == "medium":
            return "openrouter"
        else:  # complex
            return "anthropic"

    def _select_model_for_provider(self, provider: str, complexity: str) -> str:
        """Select model within provider based on complexity."""
        if provider == "anthropic":
            if complexity == "complex":
                return "claude-opus-5"
            elif complexity == "medium":
                return "claude-sonnet-5"
            else:
                return "claude-haiku-4-5"

        elif provider == "ollama":
            if complexity == "complex":
                return "mistral:latest"  # Or dolphin, neural, etc.
            else:
                return "mistral:7b"  # Lightweight

        elif provider == "openrouter":
            if complexity == "complex":
                return "openai/gpt-4-turbo"
            elif complexity == "medium":
                return "anthropic/claude-opus"
            else:
                return "open-mistral-7b"

        elif provider == "openai":
            if complexity == "complex":
                return "gpt-4"
            elif complexity == "medium":
                return "gpt-4-turbo"
            else:
                return "gpt-3.5-turbo"

        return "claude-sonnet-5"  # Fallback

    def _build_reasoning(
        self,
        features: ExtractedFeatures,
        complexity: str,
        provider: str,
        model: str,
    ) -> str:
        """Build human-readable reasoning for the selection."""
        reasons = []

        if features.token_estimate < self.config.simple_max_tokens:
            reasons.append(f"Low token count ({features.token_estimate})")

        if features.code_blocks > self.config.medium_max_code_blocks:
            reasons.append(f"Multiple code blocks ({features.code_blocks})")

        if features.dependency_count > self.config.medium_max_dependencies:
            reasons.append(f"Many dependencies ({features.dependency_count})")

        if features.reasoning_depth > 3:
            reasons.append(f"Deep reasoning needed (depth {features.reasoning_depth})")

        reasoning = f"{complexity.upper()} complexity. {', '.join(reasons) or 'Balanced task'}. " \
                   f"Selected {provider}/{model} for cost/quality tradeoff."

        return reasoning

    def get_stats(self) -> Dict:
        """Get classification statistics."""
        if not self.classification_history:
            return {"classifications": 0}

        simple_count = sum(1 for r in self.classification_history if r.complexity == "simple")
        medium_count = sum(1 for r in self.classification_history if r.complexity == "medium")
        complex_count = sum(1 for r in self.classification_history if r.complexity == "complex")

        avg_confidence = sum(r.confidence for r in self.classification_history) / len(
            self.classification_history
        )

        return {
            "classifications": len(self.classification_history),
            "simple": simple_count,
            "medium": medium_count,
            "complex": complex_count,
            "avg_confidence": avg_confidence,
        }


def create_selector(config_path: Optional[Path] = None) -> ModelSelector:
    """Factory function to create a ModelSelector with optional config file."""
    config = ModelSelectorConfig()

    if config_path and config_path.exists():
        try:
            with open(config_path) as f:
                config_dict = json.load(f)
            # Merge with defaults
            for key, value in config_dict.items():
                if hasattr(config, key):
                    setattr(config, key, value)
        except Exception as e:
            logger.warning(f"Failed to load config from {config_path}: {e}")

    return ModelSelector(config)
