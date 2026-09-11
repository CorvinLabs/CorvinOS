"""
Phase 2: E2E Model Selection Routing (ADR-0641–0644)

Wires task classification → model selection → provider routing → fallback.
Integrates with audit trail for every selection.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Any

from ..skills.os_skills.model_selector import ModelSelector, ClassificationResult
from .router import ModelRouter
from .provider_interface import ModelResponse
from ..skills.skill_audit import emit_skill_audit

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ATOPlanHint:
    """Immutable hint from ATO classification for routing injection (ADR-0165 Tier 2).

    This structure carries the recommendation from the ATO plan into _resolve_os_model(),
    where it is applied as a low-priority tier (after explicit override and context-length
    heuristic, but before workload hint and skill hook).
    """
    recommended_model: Optional[str]  # "haiku" | "sonnet" | "opus" | None
    task_type: Optional[str]
    confidence: float = 0.0  # 0.0 to 1.0

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict for audit trail."""
        return {
            "recommended_model": self.recommended_model,
            "task_type": self.task_type,
            "confidence": self.confidence,
        }


class ModelSelectionRouter:
    """
    End-to-end routing: task → classification → provider → fallback.

    Flow:
    1. Task input arrives
    2. ModelSelector.classify() → complexity, provider, model
    3. Route to provider via ModelRouter
    4. Audit every step
    5. On failure, fallback silently + audit event
    """

    def __init__(self):
        self.selector = ModelSelector()
        self.model_router = ModelRouter()
        self.selection_audit_trail: List[Dict] = []

    async def route_task(
        self,
        task_input: str,
        tenant_id: str,
        messages: Optional[List[Dict[str, str]]] = None,
        **kwargs,
    ) -> ModelResponse:
        """
        Route task to optimal model.

        Returns:
            ModelResponse from the invoked model

        Raises:
            Exception if all providers exhausted (after audit + fallback attempt)
        """
        # Step 1: Classify
        classification = self.selector.classify(task_input, tenant_id)

        # Step 2: Audit the classification (ADR-0644)
        self._audit_classification(tenant_id, classification)

        # Step 3: Build message list (or use provided)
        if messages is None:
            messages = [{"role": "user", "content": task_input}]

        # Step 4: Route to provider with fallback
        try:
            response = await self.model_router.invoke_with_fallback(
                skill_id="os.model_selector",
                model_preference=classification.recommended_provider,
                fallback_chain=self._parse_fallback_chain(),
                messages=messages,
                model=classification.recommended_model,
                **kwargs,
            )

            # Audit success
            self._audit_route_success(tenant_id, classification, response)
            return response

        except Exception as e:
            logger.error(f"Model selection routing failed: {e}")

            # Audit failure
            self._audit_route_failure(tenant_id, classification, str(e))

            # Fallback to Anthropic (default)
            logger.info("Fallback: Using default Anthropic provider")
            try:
                response = await self.model_router.invoke_with_fallback(
                    skill_id="os.model_selector",
                    model_preference="anthropic",
                    fallback_chain=[],
                    messages=messages,
                    model="claude-sonnet-5",
                    **kwargs,
                )
                self._audit_fallback_used(tenant_id, "anthropic", "claude-sonnet-5")
                return response
            except Exception as fallback_e:
                logger.error(f"Fallback to Anthropic also failed: {fallback_e}")
                self._audit_all_providers_failed(tenant_id, str(fallback_e))
                raise

    def _parse_fallback_chain(self) -> List[str]:
        """Parse fallback chain from config."""
        # Default fallback order: openrouter → openai → anthropic
        return ["openrouter", "openai", "anthropic"]

    def _audit_classification(self, tenant_id: str, classification: ClassificationResult) -> None:
        """Emit audit event for task classification."""
        emit_skill_audit(
            tenant_id=tenant_id,
            event_type="skill.model_selector.classified",
            tool="os.model_selector",
            details={
                "complexity": classification.complexity,
                "confidence": classification.confidence,
                "recommended_provider": classification.recommended_provider,
                "recommended_model": classification.recommended_model,
                "token_estimate": classification.features.token_estimate,
                "code_blocks": classification.features.code_blocks,
                "dependency_count": classification.features.dependency_count,
            },
        )

    def _audit_route_success(
        self,
        tenant_id: str,
        classification: ClassificationResult,
        response: ModelResponse,
    ) -> None:
        """Emit audit event for successful model invocation."""
        cost = response.cost_usd or 0.0
        emit_skill_audit(
            tenant_id=tenant_id,
            event_type="skill.model_selector.invoked",
            tool="os.model_selector",
            details={
                "provider": classification.recommended_provider,
                "model": response.model,
                "tokens_used": response.usage_tokens,
                "cost_usd": cost,
                "status": "success",
            },
        )

    def _audit_route_failure(
        self,
        tenant_id: str,
        classification: ClassificationResult,
        error: str,
    ) -> None:
        """Emit audit event for routing failure."""
        emit_skill_audit(
            tenant_id=tenant_id,
            event_type="skill.model_selector.route_failed",
            tool="os.model_selector",
            details={
                "intended_provider": classification.recommended_provider,
                "intended_model": classification.recommended_model,
                "error": error[:200],  # Truncate error message
                "status": "failed",
            },
            severity="warning",
        )

    def _audit_fallback_used(self, tenant_id: str, provider: str, model: str) -> None:
        """Emit audit event when fallback is used."""
        emit_skill_audit(
            tenant_id=tenant_id,
            event_type="skill.model_selector.fallback_used",
            tool="os.model_selector",
            details={
                "fallback_provider": provider,
                "fallback_model": model,
                "status": "fallback_success",
            },
            severity="info",
        )

    def _audit_all_providers_failed(self, tenant_id: str, error: str) -> None:
        """Emit audit event when all providers exhausted."""
        emit_skill_audit(
            tenant_id=tenant_id,
            event_type="skill.model_selector.all_providers_failed",
            tool="os.model_selector",
            details={
                "error": error[:200],
                "status": "all_failed",
            },
            severity="error",
        )

    def get_stats(self) -> Dict:
        """Get routing statistics."""
        selector_stats = self.selector.get_stats()
        cost_per_skill = self.model_router.cost_tracker.copy()

        return {
            "classifications": selector_stats,
            "costs": cost_per_skill,
            "audit_events": len(self.selection_audit_trail),
        }


async def route_task_e2e(
    task_input: str,
    tenant_id: str,
    messages: Optional[List[Dict[str, str]]] = None,
) -> ModelResponse:
    """
    Top-level E2E routing function.

    Usage:
        response = await route_task_e2e(
            task_input="Explain quantum computing",
            tenant_id="_default"
        )
    """
    router = ModelSelectionRouter()
    return await router.route_task(task_input, tenant_id, messages)
