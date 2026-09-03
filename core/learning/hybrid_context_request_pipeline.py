"""Request Pipeline Integration: Hybrid Context Model (Phase 4).

Wires HybridContextModel + ContextSelectorSkill into the LLM request path.

Flow:
1. RequestPipeline.enrich_request() called with raw request
2. ContextSelectorSkill selects quality mode + context items
3. HybridContextModel builds Tier 1 + Tier 2 + merge
4. Merged context injected into system prompt
5. Request sent to LLM (Claude)
6. Response + metrics logged to audit trail
"""

from __future__ import annotations

import logging
from typing import Optional, Dict, Any
from dataclasses import dataclass

from core.learning.hybrid_context import HybridContextModel
from core.skills.os_skills.context_selector import get_context_selector, ContextSelectorSkill

logger = logging.getLogger(__name__)


@dataclass
class EnrichedRequest:
    """Request enriched with hybrid context."""

    original_request: Dict[str, Any]
    system_prompt: str
    context_metadata: Dict[str, Any]  # Selection decision, metrics, etc.
    quality_mode: str
    layers_injected: int


class HybridContextRequestPipeline:
    """Pipeline: enrich requests with hybrid context before LLM call."""

    def __init__(
        self,
        tenant_id: str = "_default",
        context_model: Optional[HybridContextModel] = None,
        context_selector: Optional[ContextSelectorSkill] = None,
    ):
        """Initialize pipeline.

        Args:
            tenant_id: Tenant identifier
            context_model: HybridContextModel instance (creates if None)
            context_selector: ContextSelectorSkill instance (gets global if None)
        """
        self.tenant_id = tenant_id
        self.context_model = context_model or HybridContextModel(tenant_id)
        self.context_selector = context_selector or get_context_selector(tenant_id)

    async def enrich_request(
        self,
        request: Dict[str, Any],
        user_id: str,
        session_id: str,
        system_load_p99_ms: Optional[int] = None,
    ) -> EnrichedRequest:
        """Enrich request with hybrid context.

        Args:
            request: Raw request dict with keys:
                - messages: list[{role, content}]
                - metadata: {task_type, ...}
                - system_prompt: (optional) existing system prompt
            user_id: User identifier
            session_id: Session identifier
            system_load_p99_ms: Current system P99 latency (for load adjustment)

        Returns:
            EnrichedRequest with injected context

        Process:
            1. Get task type from request metadata
            2. Call ContextSelectorSkill to select quality mode + items
            3. Build Tier 1 base from Phase 3 data (if available)
            4. Build Tier 2 layers based on selected items
            5. Merge with fallback (fail-closed)
            6. Inject into system prompt
            7. Log audit event
        """
        task_type = request.get("metadata", {}).get("task_type", "general")

        # Step 1: Call ContextSelectorSkill
        selection = self.context_selector.execute(
            task_type=task_type,
            user_id=user_id,
            time_budget_ms=1000,
            system_load_p99_ms=system_load_p99_ms,
        )

        logger.info(
            f"Context selection: mode={selection.quality_mode.value}, "
            f"confidence={selection.confidence:.2f}"
        )

        # Step 2: Build Tier 1 base
        try:
            tier1_base = self._build_tier1_base(user_id, session_id)
        except Exception as e:
            logger.warning(f"Failed to build Tier 1 base: {e}")
            tier1_base = None

        # Step 3: Build Tier 2 layers
        tier2_layers = self._build_tier2_layers(
            user_id, selection.selected_adr_ids, selection.selected_memory_ids
        )

        # Step 4: Merge with fallback
        if tier1_base:
            try:
                layers_dicts = [
                    {
                        "layer_name": layer.layer_name,
                        "data": layer.data,
                        "status": layer.status,
                    }
                    for layer in tier2_layers
                ]
                merged_context = self.context_model.merge_with_fallback(
                    tier1_base, layers_dicts
                )
            except Exception as e:
                logger.error(f"Merge failed: {e}")
                merged_context = None
        else:
            merged_context = None

        # Step 5: Inject into system prompt
        system_prompt = request.get("system_prompt", "")
        if merged_context:
            context_str = self._serialize_context(merged_context, selection.quality_mode.value)
            system_prompt = f"{system_prompt}\n\n{context_str}".strip()

        # Step 6: Build response
        enriched = EnrichedRequest(
            original_request=request,
            system_prompt=system_prompt,
            context_metadata={
                "selection_decision": {
                    "quality_mode": selection.quality_mode.value,
                    "confidence": selection.confidence,
                    "reasoning": selection.reasoning,
                    "execution_time_ms": selection.execution_time_ms,
                },
                "tier1_present": tier1_base is not None,
                "tier2_layers": len(tier2_layers),
                "selected_adrs": selection.selected_adr_ids,
                "selected_memory": selection.selected_memory_ids,
            },
            quality_mode=selection.quality_mode.value,
            layers_injected=len(tier2_layers),
        )

        # Step 7: Log audit event
        self._emit_audit_event(enriched, user_id, session_id)

        return enriched

    def _build_tier1_base(self, user_id: str, session_id: str):
        """Build Tier 1 immutable base from Phase 3 data.

        TODO: Integrate with Phase 3 adapters (DecisionAdapter, OutcomeAdapter, etc.)
        For now, returns dummy base.
        """
        # Placeholder: would query Phase 3 stores for real data
        decisions = [{"decision_id": "d1", "choice": "a"}]
        profile = {"style": "default"}
        success_rate = 0.7
        attention_budget = 5000

        return self.context_model.snapshot_base_context(
            user_id=user_id,
            session_id=session_id,
            decisions=decisions,
            profile=profile,
            success_rate=success_rate,
            attention_budget=attention_budget,
        )

    def _build_tier2_layers(
        self, user_id: str, selected_adr_ids: list[str], selected_memory_ids: list[str]
    ):
        """Build Tier 2 injected layers from selected items.

        Args:
            user_id: User identifier
            selected_adr_ids: ADR IDs to include
            selected_memory_ids: Memory item IDs to include

        Returns:
            list[InjectedLayer]
        """
        layers = []

        # Build ADR layer
        if selected_adr_ids:
            adr_layer_data = {"adr_ids": selected_adr_ids, "count": len(selected_adr_ids)}
            # Inject as layer
            try:
                self.context_model.inject_layer(
                    user_id=user_id,
                    layer_name="adr_references",
                    data=adr_layer_data,
                    lom="hybrid_context_request_pipeline.py:_build_tier2_layers",
                )
            except Exception as e:
                logger.warning(f"Failed to inject ADR layer: {e}")

        # Build memory layer
        if selected_memory_ids:
            memory_layer_data = {
                "memory_ids": selected_memory_ids,
                "count": len(selected_memory_ids),
            }
            try:
                self.context_model.inject_layer(
                    user_id=user_id,
                    layer_name="user_memory",
                    data=memory_layer_data,
                    lom="hybrid_context_request_pipeline.py:_build_tier2_layers",
                )
            except Exception as e:
                logger.warning(f"Failed to inject memory layer: {e}")

        # Return layers for this user
        return self.context_model.injected_layers.get(user_id, [])

    @staticmethod
    def _serialize_context(merged_context: Dict[str, Any], quality_mode: str) -> str:
        """Convert merged context to prompt string."""
        lines = [
            f"## Context (Hybrid Model, Phase 4, mode={quality_mode})",
        ]

        if "recent_decisions" in merged_context:
            decisions = merged_context["recent_decisions"]
            if decisions:
                lines.append(f"Recent decisions: {len(decisions)} decision(s)")

        if "user_profile" in merged_context:
            profile = merged_context["user_profile"]
            if profile:
                lines.append(f"User profile: {profile}")

        if "success_rate" in merged_context:
            sr = merged_context["success_rate"]
            lines.append(f"Success rate: {sr:.1%}")

        if "attention_budget_remaining" in merged_context:
            budget = merged_context["attention_budget_remaining"]
            lines.append(f"Attention budget: {budget} tokens")

        # Add other layers
        for key, value in merged_context.items():
            if key not in [
                "recent_decisions",
                "user_profile",
                "success_rate",
                "attention_budget_remaining",
            ]:
                lines.append(f"{key}: {value}")

        return "\n".join(lines)

    @staticmethod
    def _emit_audit_event(
        enriched: EnrichedRequest, user_id: str, session_id: str
    ) -> None:
        """Emit audit event for context enrichment (will integrate with ADR-0232)."""
        logger.info(
            f"Context enrichment: user={user_id}, session={session_id}, "
            f"mode={enriched.quality_mode}, layers={enriched.layers_injected}"
        )


# Global instance (singleton)
_global_pipeline: Optional[HybridContextRequestPipeline] = None


def get_request_pipeline(tenant_id: str = "_default") -> HybridContextRequestPipeline:
    """Get or create global request pipeline."""
    global _global_pipeline
    if _global_pipeline is None:
        _global_pipeline = HybridContextRequestPipeline(tenant_id)
    return _global_pipeline
