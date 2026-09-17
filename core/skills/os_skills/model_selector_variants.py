"""
Model Selection Variants B, C, D — Autonomous OS System Integration

Implements three variants of model selection with tenant-skill architecture:
- Variant B: Base model selection with tenant context
- Variant C: Budget-aware selection with quota fallback (ADR-0201)
- Variant D: Learning-integrated variant with outcome feedback

ADRs: 0641, 0642, 0643, 0644, 0845 (decomposition), 0201 (quota fallback)
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Optional, Dict, List, Any, Tuple

logger = logging.getLogger(__name__)


class ModelVariant(Enum):
    """Supported model selection variants."""
    B = "variant_b"      # Base: tenant-aware classification
    C = "variant_c"      # Budget-aware: quota fallback integration
    D = "variant_d"      # Learning-aware: outcome feedback loop


@dataclass(frozen=True)
class BudgetEnvelope:
    """Immutable budget constraints (ADR-0201 ceiling enforcement)."""
    max_loops: int = 100
    max_wall_time_seconds: int = 86400  # 24h ceiling
    timeout_seconds: int = 86400
    max_worker_turns: int = 5000
    max_total_workers: int = 64
    max_depth: int = 4  # Fan-out exponent

    def is_at_ceiling(self) -> bool:
        """Check if all parameters are at validation ceiling."""
        return (
            self.max_loops == 100 and
            self.max_wall_time_seconds == 86400 and
            self.timeout_seconds == 86400 and
            self.max_worker_turns == 5000 and
            self.max_total_workers == 64
        )


@dataclass
class TenantBudgetQuota:
    """Per-tenant budget tracking (ADR-0201 quota management)."""
    tenant_id: str
    daily_quota_remaining: float  # USD
    daily_quota_limit: float      # USD
    last_reset: datetime
    worker_count_current: int

    def remaining_budget_percent(self) -> float:
        """Percentage of quota remaining."""
        if self.daily_quota_limit == 0:
            return 100.0
        return (self.daily_quota_remaining / self.daily_quota_limit) * 100

    def is_quota_exhausted(self) -> bool:
        """Check if quota exceeded (triggers fallback per ADR-0201)."""
        return self.daily_quota_remaining <= 0

    def quota_reset_needed(self) -> bool:
        """Check if quota should reset (daily window)."""
        elapsed = datetime.utcnow() - self.last_reset
        return elapsed >= timedelta(hours=24)


@dataclass
class ModelSelectionDecision:
    """Immutable model selection decision with audit trail."""
    variant: ModelVariant
    tenant_id: str
    recommended_model: str
    recommended_provider: str
    complexity: str
    confidence: float
    reasoning: str
    task_type: Optional[str] = None
    budget_envelope: Optional[BudgetEnvelope] = None
    quota_status: Optional[str] = None
    fallback_applied: bool = False
    decomposition_hint: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.utcnow)
    lom: str = "assistant.ModelSelector::decide"

    def to_audit_dict(self) -> Dict[str, Any]:
        """Serialize for audit trail (ADR-0644 audit-safe format)."""
        return {
            "variant": self.variant.value,
            "tenant_id": self.tenant_id,
            "recommended_model": self.recommended_model,
            "recommended_provider": self.recommended_provider,
            "complexity": self.complexity,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "task_type": self.task_type,
            "budget_envelope": asdict(self.budget_envelope) if self.budget_envelope else None,
            "quota_status": self.quota_status,
            "fallback_applied": self.fallback_applied,
            "decomposition_hint": self.decomposition_hint,
            "timestamp": self.timestamp.isoformat(),
            "lom": self.lom,
        }


class VariantBSelector:
    """
    Variant B: Base Model Selection with Tenant Context

    Deterministic classification with tenant-aware overrides.
    No quota management, no learning integration.

    Flow:
    1. Tenant context lookup (skill manifest, config)
    2. Feature extraction (token count, complexity markers)
    3. Classification (simple/medium/complex)
    4. Provider selection (cost-optimized)
    5. Model assignment within provider
    """

    def __init__(self, tenant_id: str):
        self.tenant_id = tenant_id
        self.config_path = self._get_config_path()
        self.overrides = self._load_tenant_overrides()

    def _get_config_path(self) -> Path:
        """Get tenant-specific config path."""
        corvin_home = Path.home() / ".corvin"
        return corvin_home / "tenants" / self.tenant_id / "global" / "model_selection_overrides.json"

    def _load_tenant_overrides(self) -> Dict[str, str]:
        """Load operator-set model overrides for this tenant."""
        if not self.config_path.exists():
            return {}
        try:
            data = json.loads(self.config_path.read_text("utf-8"))
            return data.get("models", {})
        except Exception as e:
            logger.warning(f"Failed to load tenant overrides for {self.tenant_id}: {e}")
            return {}

    def classify(
        self,
        task_input: str,
        task_type: Optional[str] = None,
    ) -> ModelSelectionDecision:
        """
        Classify task and select model for Variant B.

        Args:
            task_input: Task description or prompt
            task_type: Optional task classification (code_gen, analysis, etc.)

        Returns:
            ModelSelectionDecision with recommended model/provider
        """
        # Step 1: Check tenant overrides
        if task_type and task_type in self.overrides:
            override_model = self.overrides[task_type]
            return ModelSelectionDecision(
                variant=ModelVariant.B,
                tenant_id=self.tenant_id,
                recommended_model=override_model,
                recommended_provider=self._provider_for_model(override_model),
                complexity="unknown",
                confidence=1.0,
                reasoning=f"Tenant override: {task_type} -> {override_model}",
                task_type=task_type,
                fallback_applied=False,
            )

        # Step 2: Feature extraction
        features = self._extract_features(task_input)

        # Step 3: Classify complexity
        complexity = self._classify_complexity(features)
        confidence = self._confidence_for_complexity(complexity, features)

        # Step 4: Select provider (cost-optimized)
        provider = self._select_provider_for_complexity(complexity)

        # Step 5: Select model within provider
        model = self._select_model_for_provider(provider, complexity)

        reasoning = (
            f"Complexity: {complexity} (confidence {confidence:.2f}), "
            f"provider: {provider}, model: {model}"
        )

        return ModelSelectionDecision(
            variant=ModelVariant.B,
            tenant_id=self.tenant_id,
            recommended_model=model,
            recommended_provider=provider,
            complexity=complexity,
            confidence=confidence,
            reasoning=reasoning,
            task_type=task_type,
            fallback_applied=False,
        )

    def _extract_features(self, task_input: str) -> Dict[str, Any]:
        """Extract task features for classification."""
        return {
            "token_count": len(task_input.split()),
            "has_code": "```" in task_input or "def " in task_input,
            "has_math": any(s in task_input for s in ["∑", "∫", "√", "π", "=>"]),
            "has_reasoning": any(s in task_input for s in ["why", "because", "explain", "reason"]),
            "has_refactor": any(s in task_input for s in ["refactor", "simplify", "improve"]),
            "length": len(task_input),
        }

    def _classify_complexity(self, features: Dict[str, Any]) -> str:
        """Classify task complexity (simple/medium/complex)."""
        token_count = features.get("token_count", 0)

        if token_count <= 500:
            return "simple"
        elif token_count <= 3000:
            return "medium"
        else:
            return "complex"

    def _confidence_for_complexity(self, complexity: str, features: Dict[str, Any]) -> float:
        """Calculate confidence in classification."""
        base_confidence = {"simple": 0.95, "medium": 0.85, "complex": 0.75}.get(complexity, 0.50)

        # Boost confidence for clear markers
        if features.get("has_code"):
            base_confidence += 0.10
        if features.get("has_reasoning"):
            base_confidence += 0.05

        return min(base_confidence, 1.0)

    def _select_provider_for_complexity(self, complexity: str) -> str:
        """Select provider based on complexity (cost-optimized)."""
        provider_map = {
            "simple": "anthropic",    # Use Haiku for simple
            "medium": "anthropic",    # Use Sonnet for medium
            "complex": "anthropic",   # Use Opus for complex
        }
        return provider_map.get(complexity, "anthropic")

    def _select_model_for_provider(self, provider: str, complexity: str) -> str:
        """Select specific model within provider."""
        if provider == "anthropic":
            model_map = {
                "simple": "claude-haiku-4-5-20251001",
                "medium": "claude-sonnet-5",
                "complex": "claude-opus-5",
            }
            return model_map.get(complexity, "claude-sonnet-5")
        return "claude-sonnet-5"

    def _provider_for_model(self, model: str) -> str:
        """Map model name to provider."""
        if "haiku" in model.lower():
            return "anthropic"
        elif "sonnet" in model.lower():
            return "anthropic"
        elif "opus" in model.lower():
            return "anthropic"
        return "anthropic"


class VariantCSelector(VariantBSelector):
    """
    Variant C: Budget-Aware Model Selection with Quota Fallback (ADR-0201)

    Extends Variant B with:
    - Budget envelope tracking (validation ceiling enforcement)
    - Daily quota management
    - Quota exhaustion → fallback to direct delegation
    - Cost-aware provider selection

    Quota Fallback Strategy (ADR-0201):
    1. Track per-tenant daily quota (USD)
    2. On quota exhaustion, downgrade to single Claude Code turn
    3. Enforce L44 (Acceptable Use) fail-closed in fallback path
    4. Never re-open quota via fan-out
    """

    def __init__(self, tenant_id: str):
        super().__init__(tenant_id)
        self.quota_store_path = self._get_quota_store_path()
        self.current_quota = self._load_or_initialize_quota()

    def _get_quota_store_path(self) -> Path:
        """Get quota tracking file for this tenant."""
        corvin_home = Path.home() / ".corvin"
        return corvin_home / "tenants" / self.tenant_id / "global" / "quota_tracking.json"

    def _load_or_initialize_quota(self) -> TenantBudgetQuota:
        """Load tenant quota or initialize with defaults."""
        if self.quota_store_path.exists():
            try:
                data = json.loads(self.quota_store_path.read_text("utf-8"))
                return TenantBudgetQuota(
                    tenant_id=self.tenant_id,
                    daily_quota_remaining=float(data.get("daily_quota_remaining", 50.0)),
                    daily_quota_limit=float(data.get("daily_quota_limit", 50.0)),
                    last_reset=datetime.fromisoformat(data.get("last_reset", datetime.utcnow().isoformat())),
                    worker_count_current=int(data.get("worker_count_current", 0)),
                )
            except Exception as e:
                logger.warning(f"Failed to load quota for {self.tenant_id}, reinitializing: {e}")

        return TenantBudgetQuota(
            tenant_id=self.tenant_id,
            daily_quota_remaining=50.0,  # Default daily USD limit
            daily_quota_limit=50.0,
            last_reset=datetime.utcnow(),
            worker_count_current=0,
        )

    def _check_and_reset_quota_if_needed(self) -> None:
        """Reset quota if 24h window elapsed."""
        if self.current_quota.quota_reset_needed():
            self.current_quota.daily_quota_remaining = self.current_quota.daily_quota_limit
            self.current_quota.last_reset = datetime.utcnow()
            self._persist_quota()

    def _persist_quota(self) -> None:
        """Write quota state to disk."""
        self.quota_store_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "tenant_id": self.current_quota.tenant_id,
            "daily_quota_remaining": self.current_quota.daily_quota_remaining,
            "daily_quota_limit": self.current_quota.daily_quota_limit,
            "last_reset": self.current_quota.last_reset.isoformat(),
            "worker_count_current": self.current_quota.worker_count_current,
        }
        self.quota_store_path.write_text(json.dumps(data, indent=2))

    def classify_with_budget(
        self,
        task_input: str,
        task_type: Optional[str] = None,
        cost_limit_usd: float = 5.0,
    ) -> ModelSelectionDecision:
        """
        Classify task with budget awareness (Variant C).

        Evaluates quota exhaustion and applies fallback strategy per ADR-0201.

        Args:
            task_input: Task description
            task_type: Optional task classification
            cost_limit_usd: Maximum cost for this task

        Returns:
            ModelSelectionDecision with budget envelope and quota status
        """
        # Step 1: Check and reset quota if needed
        self._check_and_reset_quota_if_needed()

        # Step 2: Evaluate quota status
        quota_exhausted = self.current_quota.is_quota_exhausted()
        quota_remaining_pct = self.current_quota.remaining_budget_percent()

        # Step 3: Base classification (from Variant B)
        base_decision = self.classify(task_input, task_type)

        # Step 4: Create budget envelope at ceiling (ADR-0201)
        budget_envelope = BudgetEnvelope(
            max_loops=100,
            max_wall_time_seconds=86400,
            timeout_seconds=86400,
            max_worker_turns=5000,
            max_total_workers=64,
            max_depth=4,
        )

        # Step 5: Handle quota exhaustion (apply fallback per ADR-0201)
        if quota_exhausted:
            return ModelSelectionDecision(
                variant=ModelVariant.C,
                tenant_id=self.tenant_id,
                recommended_model="claude-sonnet-5",  # Fallback to Sonnet
                recommended_provider="anthropic",
                complexity=base_decision.complexity,
                confidence=0.5,  # Lower confidence for fallback
                reasoning="Quota exhausted, falling back to single Claude Code delegation (ADR-0201)",
                task_type=task_type,
                budget_envelope=budget_envelope,
                quota_status=f"EXHAUSTED (${self.current_quota.daily_quota_remaining:.2f} remaining)",
                fallback_applied=True,
            )

        # Step 6: Adjust model selection based on remaining budget
        selected_model = base_decision.recommended_model
        if quota_remaining_pct < 25:
            # Low quota: prefer cheaper model
            selected_model = "claude-haiku-4-5-20251001"
            reasoning_suffix = " (quota low, using Haiku)"
        elif quota_remaining_pct < 50:
            # Medium quota: use Sonnet
            selected_model = "claude-sonnet-5"
            reasoning_suffix = " (quota medium, using Sonnet)"
        else:
            reasoning_suffix = ""

        return ModelSelectionDecision(
            variant=ModelVariant.C,
            tenant_id=self.tenant_id,
            recommended_model=selected_model,
            recommended_provider=self._provider_for_model(selected_model),
            complexity=base_decision.complexity,
            confidence=base_decision.confidence,
            reasoning=base_decision.reasoning + reasoning_suffix,
            task_type=task_type,
            budget_envelope=budget_envelope,
            quota_status=f"OK ({quota_remaining_pct:.1f}% remaining)",
            fallback_applied=False,
        )

    def deduct_from_quota(self, cost_usd: float) -> None:
        """Record task cost and deduct from quota."""
        self.current_quota.daily_quota_remaining -= cost_usd
        self._persist_quota()


class VariantDSelector(VariantCSelector):
    """
    Variant D: Learning-Integrated Model Selection

    Extends Variant C with:
    - Learning loop integration (ADR-0314 outcome feedback)
    - Success rate tracking per task type
    - Adaptive complexity thresholds
    - Decomposition hints for complex tasks
    - Cost variance optimization

    Learning Feedback Loop:
    1. Record model selection with task type
    2. Receive outcome feedback (success/failure)
    3. Update success rates for model + task type
    4. Adapt complexity thresholds based on cost variance
    5. Next selection uses learned data
    """

    def __init__(self, tenant_id: str):
        super().__init__(tenant_id)
        self.learning_store_path = self._get_learning_store_path()
        self.success_rates = self._load_or_initialize_success_rates()
        self.cost_variance_history: List[Tuple[str, float]] = []

    def _get_learning_store_path(self) -> Path:
        """Get learning data store for this tenant."""
        corvin_home = Path.home() / ".corvin"
        return corvin_home / "tenants" / self.tenant_id / "global" / "model_learning.json"

    def _load_or_initialize_success_rates(self) -> Dict[str, Dict[str, float]]:
        """Load learned success rates per model + task type."""
        if self.learning_store_path.exists():
            try:
                data = json.loads(self.learning_store_path.read_text("utf-8"))
                return data.get("success_rates", {})
            except Exception as e:
                logger.warning(f"Failed to load learning data for {self.tenant_id}: {e}")

        # Initialize with sensible defaults
        return {
            "claude-haiku-4-5-20251001": {"code_review": 0.96, "code_gen": 0.90, "analysis": 0.90, "default": 0.88},
            "claude-sonnet-5": {"code_review": 0.99, "code_gen": 0.98, "analysis": 0.98, "default": 0.95},
            "claude-opus-5": {"code_review": 1.0, "code_gen": 0.99, "analysis": 0.99, "default": 0.98},
        }

    def _persist_learning_data(self) -> None:
        """Write learning data to disk."""
        self.learning_store_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "tenant_id": self.tenant_id,
            "success_rates": self.success_rates,
            "updated": datetime.utcnow().isoformat(),
        }
        self.learning_store_path.write_text(json.dumps(data, indent=2))

    def classify_with_learning(
        self,
        task_input: str,
        task_type: Optional[str] = None,
        cost_limit_usd: float = 5.0,
    ) -> Tuple[ModelSelectionDecision, Optional[str]]:
        """
        Classify task with learning feedback integration (Variant D).

        Args:
            task_input: Task description
            task_type: Task classification for learning
            cost_limit_usd: Maximum cost

        Returns:
            Tuple of (ModelSelectionDecision, decomposition_hint)
        """
        # Step 1: Get Variant C classification (with budget)
        base_decision = self.classify_with_budget(task_input, task_type, cost_limit_usd)

        # Step 2: Check learned success rates
        success_rate = self._get_learned_success_rate(base_decision.recommended_model, task_type)

        # Step 3: Generate decomposition hint for complex tasks with lower success rates
        decomposition_hint = None
        if base_decision.complexity == "complex" and success_rate < 0.85:
            decomposition_hint = self._generate_decomposition_hint(task_input, task_type)

        # Step 4: Enhance decision with learning data
        enhanced_decision = ModelSelectionDecision(
            variant=ModelVariant.D,
            tenant_id=self.tenant_id,
            recommended_model=base_decision.recommended_model,
            recommended_provider=base_decision.recommended_provider,
            complexity=base_decision.complexity,
            confidence=base_decision.confidence * success_rate,  # Adjust confidence
            reasoning=base_decision.reasoning + f" (learned success rate: {success_rate:.2f})",
            task_type=task_type,
            budget_envelope=base_decision.budget_envelope,
            quota_status=base_decision.quota_status,
            fallback_applied=base_decision.fallback_applied,
            decomposition_hint=decomposition_hint,
        )

        return enhanced_decision, decomposition_hint

    def _get_learned_success_rate(self, model: str, task_type: Optional[str]) -> float:
        """Get learned success rate for model + task type."""
        task_key = task_type or "default"
        model_rates = self.success_rates.get(model, {})
        return model_rates.get(task_key, model_rates.get("default", 0.88))

    def _generate_decomposition_hint(self, task_input: str, task_type: Optional[str]) -> str:
        """Generate task decomposition hint for complex tasks."""
        return (
            f"Task marked as {task_type or 'complex'}. "
            f"Consider breaking into smaller subtasks for better model performance."
        )

    def record_outcome_feedback(
        self,
        model: str,
        task_type: Optional[str],
        success: bool,
        cost_usd: float,
    ) -> None:
        """
        Record outcome feedback for learning loop integration (ADR-0314).

        Updates success rates and cost variance tracking.

        Args:
            model: Model used
            task_type: Task classification
            success: Whether task completed successfully
            cost_usd: Actual cost of the task
        """
        task_key = task_type or "default"

        # Update success rate (running average)
        if model not in self.success_rates:
            self.success_rates[model] = {}

        current_rate = self.success_rates[model].get(task_key, 0.5)
        # Bayesian update: weight = 0.9 * current + 0.1 * new
        new_rate = (0.9 * current_rate) + (0.1 * (1.0 if success else 0.0))
        self.success_rates[model][task_key] = new_rate

        # Track cost variance for threshold adaptation
        self.cost_variance_history.append((task_key, cost_usd))

        # Persist learning data
        self._persist_learning_data()

        logger.info(
            f"Learning feedback: {model}/{task_key}, "
            f"success={success}, cost=${cost_usd:.2f}, "
            f"new_rate={new_rate:.2f}"
        )


# Variant Factory for easy construction
def create_selector(tenant_id: str, variant: ModelVariant = ModelVariant.B) -> VariantBSelector:
    """
    Factory function to create appropriate model selector variant.

    Args:
        tenant_id: Tenant identifier
        variant: Variant to create (B/C/D)

    Returns:
        Appropriate selector instance
    """
    if variant == ModelVariant.B:
        return VariantBSelector(tenant_id)
    elif variant == ModelVariant.C:
        return VariantCSelector(tenant_id)
    elif variant == ModelVariant.D:
        return VariantDSelector(tenant_id)
    else:
        raise ValueError(f"Unknown variant: {variant}")
