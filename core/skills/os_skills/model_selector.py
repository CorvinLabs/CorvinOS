"""
Phase 2–3: Model Selector Skill (ADR-0641, ADR-0642, ADR-0377-Phase-2)

Determines which model (Anthropic/Ollama/OpenRouter/OpenAI) to use for a task.
Deterministic classification: token count + keywords + reasoning depth.

Phase 2 (ADR-0377): Cost-Variance Feedback Loop
- Learns from cost variance to adapt complexity thresholds
- Uses CostVarianceOptimizer for Bayesian online learning
- Provides record_cost_feedback() method for TaskManager integration
- Adapts thresholds based on observed cost variance patterns
"""

import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

from .feature_extractor import FeatureExtractor, ExtractedFeatures

logger = logging.getLogger(__name__)

# Optional import for learning store integration
try:
    from core.learning.learned_threshold_store import get_store
except ImportError:
    get_store = None


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

    k=2 (ADR-0845): Added classify_with_decomposition_hint() for OS-layer
    task routing with optional prompt-level decomposition support.
    """

    def __init__(
        self,
        config: Optional[ModelSelectorConfig] = None,
        overrides: Optional[Dict[str, Dict[str, Optional[str]]]] = None,
        cost_variance_optimizer: Optional[Any] = None,
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

        # Phase 2: Cost-Variance Learning (ADR-0377)
        # Optional cost variance optimizer for adaptive threshold learning
        self.cost_variance_optimizer = cost_variance_optimizer
        self._dynamic_complexity_threshold = 0.5  # Base threshold, may be adapted

        # k=2 (ADR-0845): Historical Haiku success rates per task_type
        # Format: {task_type: success_rate (0.0-1.0)}
        # Populated from learning loop feedback (ADR-0314)
        # After task-specific decomposition, Haiku can achieve high quality:
        self.haiku_success_rates: Dict[str, float] = {
            "code_review": 0.96,    # Haiku can handle structured reviews at 96% quality
            "code_gen": 0.90,       # Code generation harder but still viable
            "analysis": 0.90,       # Analysis with decomposition at 90% quality
            "summarization": 0.96,  # Haiku excels at structured summarization
            "refactoring": 0.92,    # Structured refactoring at 92% quality
            "testing": 0.95,        # Test generation at 95% quality
            "documentation": 0.97,  # Documentation is Haiku-optimized
            "orchestration": 0.50,  # Orchestration needs Sonnet reasoning
            "system_design": 0.55,  # System design needs Sonnet reasoning
            "default": 0.88,        # Default task type
        }

    def classify_with_decomposition_hint(
        self,
        task_input: str,
        tenant_id: Optional[str] = None,
        task_type: Optional[str] = None,
    ) -> Tuple[ClassificationResult, Optional[str]]:
        """
        Classify task and recommend model with optional decomposition hint.

        k=2 (ADR-0845): OS-layer routing with decomposition support.

        Returns:
            (ClassificationResult, decomposition_hint)
            where decomposition_hint is one of:
            - None: use full Sonnet reasoning (no decomposition)
            - "prompt_structured": decompose at prompt level (Tier 2)
            - "graph_structured": decompose at graph level (Tier 3, via Skill DAGs)

        Decision logic:
        1. Extract features
        2. Heuristic 1: Is this an orchestration/composition task?
           → If YES: recommend Sonnet (reasoning needed), hint=None
        3. Heuristic 2: Is this decomposable to prompt-level structure?
           → Check historical Haiku success(task_type)
           → If success_rate > 85%: recommend Haiku + hint="prompt_structured"
           → Else: recommend Sonnet, hint=None
        4. Return (ClassificationResult, hint)
        """
        tenant_id = tenant_id or "_default"

        # Step 1: Extract features
        features = self.feature_extractor.extract(task_input, tenant_id)

        # Step 2: Classify base complexity
        complexity, confidence = self._classify_complexity(
            features,
            task_type=task_type,
            tenant_id=tenant_id,
        )

        # Step 3: Heuristic 1 — Detect orchestration/composition tasks
        is_orchestration = self._is_orchestration_task(task_input, features, task_type)

        # Step 4: Heuristic 2 — Check decomposability & historical Haiku success
        is_decomposable = self._is_decomposable_task(task_input, features, task_type)
        decomposition_hint = None

        # Determine model and hint
        if is_orchestration:
            # Orchestration tasks need Sonnet for reasoning (no decomposition)
            provider = "anthropic"
            model = "claude-sonnet-5"
            decomposition_hint = None
        elif is_decomposable:
            # Check historical Haiku success rate (from learning store or defaults)
            haiku_success_rate = self._get_haiku_success_rate(
                task_type=task_type,
                tenant_id=tenant_id
            )

            if haiku_success_rate > 0.85:
                # Haiku can handle this task type successfully
                provider = "anthropic"
                model = "claude-haiku-4-5"
                decomposition_hint = "prompt_structured"  # Tier 2
                confidence = haiku_success_rate  # Confidence based on historical data
            else:
                # Haiku hasn't proven itself for this task type
                provider = "anthropic"
                model = "claude-sonnet-5"
                decomposition_hint = None
        else:
            # Standard routing (not decomposable)
            provider = self._select_provider(complexity)
            model = self._select_model_for_provider(provider, complexity)
            decomposition_hint = None

        # Build reasoning with decomposition context
        reasoning = self._build_reasoning_with_decomposition(
            features,
            complexity,
            provider,
            model,
            decomposition_hint,
            is_orchestration,
            is_decomposable,
            task_type=task_type,
            tenant_id=tenant_id,
        )

        result = ClassificationResult(
            complexity=complexity,
            confidence=confidence,
            recommended_provider=provider,
            recommended_model=model,
            reasoning=reasoning,
            features=features,
        )

        # Track for learning
        self.classification_history.append(result)

        return result, decomposition_hint

    def _is_orchestration_task(
        self,
        task_input: str,
        features: ExtractedFeatures,
        task_type: Optional[str] = None,
    ) -> bool:
        """
        Detect if task requires orchestration/composition reasoning.

        Heuristic 1 (ADR-0845):
        - Keywords: "coordinate", "orchestrate", "compose", "delegate", "manage"
        - Involves multiple sub-tasks or skills
        - Requires high-level planning (reasoning_depth > 3)
        """
        orchestration_keywords = {
            "coordinate", "orchestrate", "compose", "delegate", "manage",
            "plan", "design system", "workflow", "pipeline", "skill",
            "multi-step", "sequence", "aggregate", "combine"
        }

        task_lower = task_input.lower()

        # Check keywords
        keyword_match = any(kw in task_lower for kw in orchestration_keywords)

        # Check reasoning depth
        high_reasoning_depth = features.reasoning_depth > 3

        # Check task type
        is_orchestration_type = task_type in {"orchestration", "composition", "workflow", "system_design"}

        # Decision: any two of three signals indicate orchestration
        signals = [keyword_match, high_reasoning_depth, is_orchestration_type]
        return sum(signals) >= 2

    def _is_decomposable_task(
        self,
        task_input: str,
        features: ExtractedFeatures,
        task_type: Optional[str] = None,
    ) -> bool:
        """
        Detect if task can be decomposed to prompt-level steps.

        Heuristic 2 (ADR-0845):
        - Not an orchestration task
        - Has clear structure (bullet points, steps, numbered lists)
        - Reasonable complexity (token_estimate 200-8000)
        - Known task type with good Haiku track record
        """
        # Already orchestration? Don't decompose (higher reasoning level)
        if self._is_orchestration_task(task_input, features, task_type):
            return False

        # Check for structured format
        has_structured_format = (
            re.search(r'^\s*[-•*]\s', task_input, re.MULTILINE) or  # Bullets
            re.search(r'^\s*\d+\.\s', task_input, re.MULTILINE) or  # Numbered list
            ":" in task_input  # Structured headers (e.g., "Review code for:")
        )

        # Check token range (decomposition works best in manageable tasks)
        # Lower bound: at least 15 tokens (short but structured lists are OK)
        # Upper bound: 8000 tokens (beyond this needs full Sonnet reasoning)
        in_decomposable_range = 15 < features.token_estimate < 8000

        # Check if task type has reasonable Haiku track record
        known_task_type = task_type and task_type in self.haiku_success_rates

        # Decision: All three signals needed
        return has_structured_format and in_decomposable_range and known_task_type

    def _get_haiku_success_rate(
        self,
        task_type: Optional[str] = None,
        tenant_id: str = "_default",
    ) -> float:
        """Get historical Haiku success rate for task type.

        Tries to query learning store first (ADR-0314), then falls back to
        hardcoded defaults. Returns success rate [0.0, 1.0].

        Args:
            task_type: task classification (e.g., "code_review")
            tenant_id: tenant scope for learning store query (GDPR Art. 5)

        Returns:
            Success rate [0.0, 1.0], or default 0.88 if unknown
        """
        # Step 1: Try learning store first (real learned data)
        if get_store is not None and task_type:
            try:
                store = get_store(tenant_id)
                if store:
                    # Query for Haiku success rate per task type
                    thresholds = store.get_all()
                    for threshold in thresholds:
                        # Match on task_type (e.g., "code_review")
                        if (threshold.task_type == task_type and
                            threshold.success_rate > 0.0 and
                            threshold.converged and
                            threshold.sample_count >= 10):
                            # Use real learned success rate
                            logger.debug(
                                f"Using learned success rate for {task_type}: "
                                f"{threshold.success_rate:.2%} (n={threshold.sample_count})"
                            )
                            return threshold.success_rate
            except Exception as e:
                logger.debug(
                    f"Failed to query learning store for {task_type} "
                    f"(tenant {tenant_id}): {e}, falling back to defaults"
                )

        # Step 2: Fallback to hardcoded defaults
        if task_type and task_type in self.haiku_success_rates:
            return self.haiku_success_rates[task_type]

        # Step 3: Ultimate fallback
        return self.haiku_success_rates.get("default", 0.88)

    def get_haiku_stats_by_task_type(
        self,
        tenant_id: str = "_default",
    ) -> Dict[str, Dict[str, Any]]:
        """Get Haiku success statistics by task type from learning store.

        Returns real data from learning store with fallback to hardcoded defaults.
        Tenant-scoped query (GDPR Art. 5).

        Args:
            tenant_id: tenant scope for learning store query

        Returns:
            Dict mapping task_type → {success_rate, n_samples, confidence_interval}
            Example:
            {
                "code_review": {
                    "success_rate": 0.96,
                    "n_samples": 42,
                    "confidence_interval": [0.91, 1.0],
                    "model": "claude-haiku-4-5"
                },
                ...
            }
        """
        stats = {}

        # Step 1: Try learning store
        if get_store is not None:
            try:
                store = get_store(tenant_id)
                if store:
                    thresholds = store.get_all()
                    for threshold in thresholds:
                        if threshold.success_rate > 0.0 and threshold.sample_count >= 10:
                            # Compute 95% confidence interval using binomial proportion
                            n = threshold.sample_count
                            p = threshold.success_rate
                            se = (p * (1 - p) / n) ** 0.5
                            ci_margin = 1.96 * se  # 95% CI

                            stats[threshold.task_type] = {
                                "success_rate": p,
                                "n_samples": n,
                                "confidence_interval": [
                                    max(0.0, p - ci_margin),
                                    min(1.0, p + ci_margin),
                                ],
                                "converged": threshold.converged,
                                "model": "claude-haiku-4-5",
                            }
            except Exception as e:
                logger.debug(
                    f"Failed to query learning store for {tenant_id}: {e}, "
                    f"falling back to hardcoded defaults"
                )

        # Step 2: Fill in missing task types from hardcoded defaults
        for task_type, success_rate in self.haiku_success_rates.items():
            if task_type not in stats:
                stats[task_type] = {
                    "success_rate": success_rate,
                    "n_samples": 0,  # Hardcoded, no samples
                    "confidence_interval": None,  # N/A for hardcoded
                    "converged": False,
                    "model": "claude-haiku-4-5",
                }

        return stats

    def _build_reasoning_with_decomposition(
        self,
        features: ExtractedFeatures,
        complexity: str,
        provider: str,
        model: str,
        decomposition_hint: Optional[str],
        is_orchestration: bool,
        is_decomposable: bool,
        task_type: Optional[str] = None,
        tenant_id: str = "_default",
    ) -> str:
        """Build reasoning including decomposition context."""
        reasons = []

        if is_orchestration:
            reasons.append("Orchestration/composition task requires Sonnet reasoning")
        elif is_decomposable and decomposition_hint:
            # Get success rate from learning store (with fallback to defaults)
            success_rate = self._get_haiku_success_rate(task_type=task_type, tenant_id=tenant_id)
            reasons.append(f"Decomposable task: Haiku {success_rate*100:.0f}% success rate")
            reasons.append(f"Decomposition hint: {decomposition_hint}")

        if features.token_estimate < self.config.simple_max_tokens:
            reasons.append(f"Low token count ({features.token_estimate})")

        if features.code_blocks > self.config.medium_max_code_blocks:
            reasons.append(f"Multiple code blocks ({features.code_blocks})")

        if features.reasoning_depth > 3:
            reasons.append(f"Deep reasoning needed (depth {features.reasoning_depth})")

        reasoning = f"{complexity.upper()} complexity. {', '.join(reasons) or 'Balanced task'}. " \
                   f"Selected {provider}/{model} for cost/quality tradeoff."

        return reasoning

    def classify(
        self,
        task_input: str,
        tenant_id: Optional[str] = None,
        task_type: Optional[str] = None,
    ) -> ClassificationResult:
        """
        Classify task and recommend model.

        Args:
            task_input: task description or prompt
            tenant_id: tenant scope (default "_default")
            task_type: task classification for learning (e.g., "code_gen")

        Returns:
            ClassificationResult with complexity, confidence, provider, model
        """
        tenant_id = tenant_id or "_default"

        # Step 1: Extract features
        features = self.feature_extractor.extract(task_input, tenant_id)

        # Step 2: Decision tree for complexity classification (uses learned thresholds)
        complexity, confidence = self._classify_complexity(
            features,
            task_type=task_type,
            tenant_id=tenant_id,
        )

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

    def _classify_complexity(
        self,
        features: ExtractedFeatures,
        task_type: Optional[str] = None,
        tenant_id: str = "_default",
    ) -> Tuple[str, float]:
        """
        Classify complexity using decision tree.

        Decision Rules (ADR-0642):
        SIMPLE: token_count < 500 AND code_blocks <= 1 AND dependencies <= 2
        COMPLEX: token_count > 3000 OR code_blocks > 5 OR dependencies > 10
        MEDIUM: else

        Phase 2 (ADR-0377): Uses learned complexity thresholds if available.
        """
        # Get learned threshold from cost variance optimizer if available
        complexity_threshold = 0.5  # Default
        if self.cost_variance_optimizer and task_type:
            try:
                # Get learned threshold (or base 0.5 if not yet converged)
                learned_threshold = self.cost_variance_optimizer.get_threshold_recommendation(
                    task_type=task_type,
                    subsystem="model_selector",  # Logical subsystem
                    tenant_id=tenant_id,
                    base_threshold=0.5,
                )
                complexity_threshold = learned_threshold
                self._dynamic_complexity_threshold = learned_threshold
            except Exception as e:
                logger.debug(f"Failed to get learned threshold: {e}, using base 0.5")

        # Start with keyword-based hint
        base_complexity = features.keyword_complexity

        # Compute a feature-based complexity score (0.0-1.0)
        feature_complexity = self._compute_feature_complexity(features)

        # Apply learned threshold
        if feature_complexity < complexity_threshold:
            if features.code_blocks <= self.config.simple_max_code_blocks:
                return "SIMPLE", 0.85  # High confidence
        elif feature_complexity > (complexity_threshold + 0.3):
            if (features.code_blocks > self.config.medium_max_code_blocks
                    or features.dependency_count > self.config.medium_max_dependencies):
                return "COMPLEX", 0.90  # High confidence

        # Medium category (default, medium confidence)
        if base_complexity == "complex":
            return "COMPLEX", 0.70
        elif base_complexity == "simple":
            return "SIMPLE", 0.70
        else:
            return "MEDIUM", 0.60  # Uncertain

    def _compute_feature_complexity(self, features: ExtractedFeatures) -> float:
        """Compute normalized feature-based complexity (0.0-1.0).

        Combines token count, code blocks, and dependencies into a single score.
        """
        # Normalize token count (max 5000 tokens = complexity 1.0)
        token_complexity = min(1.0, features.token_estimate / 5000.0)

        # Normalize code blocks (max 20 = complexity 1.0)
        block_complexity = min(1.0, features.code_blocks / 20.0)

        # Normalize dependencies (max 50 = complexity 1.0)
        dep_complexity = min(1.0, features.dependency_count / 50.0)

        # Weighted average
        return (token_complexity * 0.5 + block_complexity * 0.25 + dep_complexity * 0.25)

    def _select_provider(self, complexity: str) -> str:
        """
        Select provider based on complexity.

        SIMPLE → Anthropic/Haiku (cost-optimized, high success)
        MEDIUM → Anthropic/Sonnet (balanced)
        COMPLEX → Anthropic/Opus (best quality)
        """
        # k=2 (ADR-0845): Always prefer Anthropic for consistency + Haiku for SIMPLE
        if complexity == "SIMPLE":
            return "anthropic"  # Use Haiku for cost-optimization
        elif complexity == "MEDIUM":
            return "anthropic"  # Use Sonnet for balanced quality
        else:  # COMPLEX
            return "anthropic"  # Use Opus for best quality

    def _select_model_for_provider(self, provider: str, complexity: str) -> str:
        """Select model within provider based on complexity."""
        if provider == "anthropic":
            if complexity == "COMPLEX":
                return "claude-opus-5"
            elif complexity == "MEDIUM":
                return "claude-sonnet-5"
            else:  # SIMPLE
                return "claude-haiku-4-5"

        elif provider == "ollama":
            if complexity == "COMPLEX":
                return "mistral:latest"
            else:
                return "mistral:7b"

        elif provider == "openrouter":
            if complexity == "COMPLEX":
                return "openai/gpt-4-turbo"
            elif complexity == "MEDIUM":
                return "anthropic/claude-opus"
            else:
                return "open-mistral-7b"

        elif provider == "openai":
            if complexity == "COMPLEX":
                return "gpt-4"
            elif complexity == "MEDIUM":
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

    def record_cost_feedback(
        self,
        task_type: str,
        quality_score: float,
        cost_variance: float,
        tenant_id: str = "_default",
    ) -> Optional[Tuple[float, bool]]:
        """Record cost variance feedback for learning.

        Phase 2 (ADR-0377): Integrates with CostVarianceOptimizer for adaptive learning.

        Args:
            task_type: task classification (e.g., "code_gen")
            quality_score: [0.0, 1.0] quality assessment
            cost_variance: actual_cost - estimated_cost
            tenant_id: tenant scope

        Returns:
            (recommended_threshold, is_converged) if optimizer available, else None
        """
        if not self.cost_variance_optimizer:
            logger.debug("Cost variance optimizer not available, skipping feedback")
            return None

        try:
            threshold, is_converged = self.cost_variance_optimizer.process_cost_variance(
                task_type=task_type,
                subsystem="model_selector",
                quality_score=quality_score,
                cost_variance=cost_variance,
                tenant_id=tenant_id,
                base_threshold=0.5,
            )
            logger.info(
                f"Cost feedback recorded: task_type={task_type}, "
                f"quality={quality_score:.2f}, variance={cost_variance:.4f}, "
                f"new_threshold={threshold:.3f}, converged={is_converged}"
            )
            return threshold, is_converged
        except Exception as e:
            logger.error(f"Failed to record cost feedback: {e}", exc_info=True)
            return None


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
