"""
Model Selector Skill Integration — Wires Variants B, C, D into Autonomous OS L5 Routing

This module provides:
1. SkillInterface wrapper for ModelSelector variants
2. Integration with L5 (auto-routing) + L10 (context engineering)
3. Audit trail emission (ADR-0644)
4. Learning loop feedback integration (ADR-0314)
5. Quota-aware delegation (ADR-0201)

Skill Manifest Requirements:
```json
{
  "skill_id": "os.model_selector",
  "version": "2.0.0",
  "variant": "b|c|d",  // Set at tenant/operator level
  "config": {
    "tenant_id": "<tenant>",
    "variant": "variant_c",  // Default to C (budget-aware)
    "fallback_to_delegate": true,  // Fallback to direct delegation on quota exhaustion
    "cost_limit_per_task": 5.0
  }
}
```

Usage:
```python
from core.skills.os_skills.model_selector_skill_integration import ModelSelectorSkill

skill = ModelSelectorSkill(tenant_id="my_tenant", variant="variant_c")
decision = skill.execute(
    task_input="Classify this code review",
    task_type="code_review"
)
# Returns: (recommended_model, recommended_provider, audit_event)

# Record learning feedback
skill.record_outcome(
    model=decision.recommended_model,
    task_type="code_review",
    success=True,
    cost_usd=2.50
)
```
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from enum import Enum
from typing import Optional, Dict, Any, Tuple

from core.skills.os_skills.model_selector_variants import (
    ModelVariant,
    ModelSelectionDecision,
    create_selector,
    VariantBSelector,
    VariantCSelector,
    VariantDSelector,
)

# Audit event emission (optional, fail-soft on import)
try:
    from core.gateway.corvin_gateway.plugin_cmd import emit_audit_event
except ImportError:
    def emit_audit_event(event_type: str, plugin_id: str = None, details: dict = None) -> None:
        """Fallback audit event emission (no-op if audit backend unavailable)."""
        logger.debug(f"Audit event: {event_type} (backend unavailable)")

logger = logging.getLogger(__name__)


class SkillExecutionMode(Enum):
    """Skill execution modes for different contexts."""
    NORMAL = "normal"          # Standard task routing
    SHADOW = "shadow"          # Audit-only, bundled decision stands (ADR-0613)
    LEARNING_FEEDBACK = "learning_feedback"  # Outcome recording only


class ModelSelectorSkill:
    """
    Unified Skill interface for model selection variants B, C, D.

    Provides:
    - Skill API (execute, validate, get_info)
    - Audit trail integration (ADR-0644)
    - Learning feedback loop (ADR-0314)
    - Quota fallback handling (ADR-0201)

    Skill Registration (in skill manifest):
    ```yaml
    id: "os.model_selector"
    version: "2.0.0"
    tier: "core"
    boot_layer: "core"  # Immutable, always on
    variant: "variant_c"  # Configurable: b, c, or d
    dependencies: []
    required_checks: ["l44_acceptable_use", "l16_consent"]
    ```
    """

    def __init__(
        self,
        tenant_id: str,
        variant: str = "variant_c",
        cost_limit_per_task: float = 5.0,
        enable_audit: bool = True,
        enable_learning: bool = True,
    ):
        """
        Initialize ModelSelectorSkill.

        Args:
            tenant_id: Tenant identifier
            variant: "variant_b", "variant_c", or "variant_d"
            cost_limit_per_task: Max USD per task
            enable_audit: Emit audit events
            enable_learning: Record learning feedback (Variant D only)
        """
        self.tenant_id = tenant_id
        self.variant_name = variant
        self.cost_limit_per_task = cost_limit_per_task
        self.enable_audit = enable_audit
        self.enable_learning = enable_learning

        # Map variant name to ModelVariant enum
        self.variant_enum = self._parse_variant(variant)

        # Create appropriate selector instance
        self.selector = create_selector(tenant_id, self.variant_enum)

        # Audit trail
        self.audit_trail: list[Dict[str, Any]] = []

    def _parse_variant(self, variant_str: str) -> ModelVariant:
        """Parse variant string to ModelVariant enum."""
        variant_map = {
            "variant_b": ModelVariant.B,
            "variant_c": ModelVariant.C,
            "variant_d": ModelVariant.D,
            "b": ModelVariant.B,
            "c": ModelVariant.C,
            "d": ModelVariant.D,
        }
        return variant_map.get(variant_str.lower(), ModelVariant.C)

    def execute(
        self,
        task_input: str,
        task_type: Optional[str] = None,
        mode: SkillExecutionMode = SkillExecutionMode.NORMAL,
    ) -> ModelSelectionDecision:
        """
        Execute model selection skill.

        This is the main entry point called by L5 (auto-routing).

        Args:
            task_input: Task description or prompt
            task_type: Optional task classification
            mode: Execution mode (normal, shadow, learning_feedback)

        Returns:
            ModelSelectionDecision with recommended model/provider
        """
        try:
            # Step 1: Execute appropriate variant
            if self.variant_enum == ModelVariant.B:
                decision = self._execute_variant_b(task_input, task_type)
            elif self.variant_enum == ModelVariant.C:
                decision = self._execute_variant_c(task_input, task_type)
            elif self.variant_enum == ModelVariant.D:
                decision = self._execute_variant_d(task_input, task_type)
            else:
                decision = self._execute_variant_c(task_input, task_type)  # Fallback to C

            # Step 2: Emit audit event (ADR-0644)
            if self.enable_audit:
                self._emit_audit_event(decision, mode)

            # Step 3: Handle execution modes
            if mode == SkillExecutionMode.SHADOW:
                logger.info(
                    f"Model Selector shadow execution: "
                    f"{decision.recommended_model} / {decision.recommended_provider}"
                )

            return decision

        except Exception as e:
            logger.error(f"Model selection failed for tenant {self.tenant_id}: {e}")
            # Fallback to safe default on error
            return self._create_error_fallback_decision(str(e))

    def _execute_variant_b(self, task_input: str, task_type: Optional[str]) -> ModelSelectionDecision:
        """Execute Variant B (base classification)."""
        return self.selector.classify(task_input, task_type)

    def _execute_variant_c(self, task_input: str, task_type: Optional[str]) -> ModelSelectionDecision:
        """Execute Variant C (budget-aware with quota fallback)."""
        assert isinstance(self.selector, VariantCSelector)
        return self.selector.classify_with_budget(
            task_input,
            task_type,
            self.cost_limit_per_task
        )

    def _execute_variant_d(self, task_input: str, task_type: Optional[str]) -> ModelSelectionDecision:
        """Execute Variant D (learning-integrated)."""
        assert isinstance(self.selector, VariantDSelector)
        decision, hint = self.selector.classify_with_learning(
            task_input,
            task_type,
            self.cost_limit_per_task
        )
        return decision

    def _emit_audit_event(
        self,
        decision: ModelSelectionDecision,
        mode: SkillExecutionMode,
    ) -> None:
        """Emit audit event for model selection decision (ADR-0644)."""
        try:
            audit_data = decision.to_audit_dict()
            audit_data["skill_id"] = "os.model_selector"
            audit_data["execution_mode"] = mode.value
            audit_data["cost_limit"] = self.cost_limit_per_task

            # Emit to audit backend (fail-closed per ADR-0232)
            emit_audit_event(
                event_type="skill_executed",
                tenant_id=self.tenant_id,
                skill_id="os.model_selector",
                data=audit_data,
                lom="assistant.ModelSelectorSkill::execute",
            )

            self.audit_trail.append(audit_data)
        except Exception as e:
            logger.error(f"Failed to emit audit event: {e}")
            # Do not raise — audit failure should not block skill execution
            # (audit-first principle applies at the backend, not in the skill)

    def record_outcome(
        self,
        model: str,
        task_type: Optional[str],
        success: bool,
        cost_usd: float,
    ) -> None:
        """
        Record outcome feedback for learning loop integration (ADR-0314).

        Only available for Variant D.

        Args:
            model: Model used
            task_type: Task classification
            success: Whether task succeeded
            cost_usd: Actual cost of task
        """
        if self.variant_enum != ModelVariant.D:
            logger.debug(
                f"Outcome recording skipped: variant {self.variant_name} "
                f"does not support learning feedback"
            )
            return

        if not self.enable_learning:
            return

        try:
            assert isinstance(self.selector, VariantDSelector)
            self.selector.record_outcome_feedback(model, task_type, success, cost_usd)

            # Emit learning feedback audit event
            if self.enable_audit:
                emit_audit_event(
                    event_type="skill_feedback",
                    tenant_id=self.tenant_id,
                    skill_id="os.model_selector",
                    data={
                        "model": model,
                        "task_type": task_type,
                        "success": success,
                        "cost_usd": cost_usd,
                    },
                    lom="assistant.ModelSelectorSkill::record_outcome",
                )
        except Exception as e:
            logger.error(f"Failed to record outcome feedback: {e}")

    def deduct_quota(self, cost_usd: float) -> bool:
        """
        Deduct cost from tenant quota (Variant C only).

        Returns:
            True if quota available, False if exhausted
        """
        if self.variant_enum != ModelVariant.C:
            return True  # Variants B and D don't track quota

        try:
            assert isinstance(self.selector, VariantCSelector)
            if self.selector.current_quota.is_quota_exhausted():
                return False

            self.selector.deduct_from_quota(cost_usd)
            return True
        except Exception as e:
            logger.error(f"Quota deduction failed: {e}")
            return False

    def get_current_quota(self) -> Optional[Dict[str, Any]]:
        """Get current quota status (Variant C only)."""
        if self.variant_enum != ModelVariant.C:
            return None

        assert isinstance(self.selector, VariantCSelector)
        return {
            "remaining": self.selector.current_quota.daily_quota_remaining,
            "limit": self.selector.current_quota.daily_quota_limit,
            "percent": self.selector.current_quota.remaining_budget_percent(),
            "exhausted": self.selector.current_quota.is_quota_exhausted(),
        }

    def get_skill_info(self) -> Dict[str, Any]:
        """Get skill metadata."""
        return {
            "skill_id": "os.model_selector",
            "version": "2.0.0",
            "tenant_id": self.tenant_id,
            "variant": self.variant_name,
            "variant_enum": self.variant_enum.value,
            "cost_limit_per_task": self.cost_limit_per_task,
            "features": {
                "tenant_aware": True,
                "budget_aware": self.variant_enum in [ModelVariant.C, ModelVariant.D],
                "learning_enabled": self.variant_enum == ModelVariant.D,
                "quota_fallback": self.variant_enum in [ModelVariant.C, ModelVariant.D],
                "audit_enabled": self.enable_audit,
            },
            "audit_trail_size": len(self.audit_trail),
        }

    def _create_error_fallback_decision(self, error_msg: str) -> ModelSelectionDecision:
        """Create safe fallback decision on error."""
        return ModelSelectionDecision(
            variant=self.variant_enum,
            tenant_id=self.tenant_id,
            recommended_model="claude-sonnet-5",  # Safe middle-ground default
            recommended_provider="anthropic",
            complexity="unknown",
            confidence=0.0,
            reasoning=f"Error in model selection: {error_msg}. Falling back to Sonnet.",
            fallback_applied=True,
        )


def skill_execute_wrapper(
    tenant_id: str,
    task_input: str,
    variant: str = "variant_c",
    task_type: Optional[str] = None,
) -> Tuple[str, str]:
    """
    Convenience wrapper for calling model selector skill.

    Used by L5 routing to get model/provider recommendation.

    Returns:
        Tuple of (recommended_model, recommended_provider)
    """
    skill = ModelSelectorSkill(tenant_id, variant)
    decision = skill.execute(task_input, task_type)
    return decision.recommended_model, decision.recommended_provider
