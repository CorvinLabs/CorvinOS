"""L10 Context Adapter Skill Integration — Phase 2b (Context Engineering Pipeline).

Integrates os.context_adapter Skill into the request context pipeline so learned
context adjustments (vibe_score, attention_budget, priority) flow to agents.

Current state: os.context_adapter exists but has zero production call sites.
This module wires it into the CEL pipeline as a new stage that runs BEFORE
delegation routing (L5) so that both routing and context decisions have access
to the learned context state.

Architecture:
  Request → L1-L9 CEL stages → L10_Adapt (THIS MODULE) → Merge → L5 Routing

Fail-closed semantics:
  - If L10 Skill times out (>2s): return base context only
  - If L10 Skill errors: return base context only
  - If output is malformed: return base context only
  - Base context is always immutable and available (fallback)

ADR-0532 Phase 2b: L10 Context Wiring
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.skills.skill_registry import SkillRegistry

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ContextTiers:
    """Three-tier hybrid context model (fail-closed)."""

    base_tier: dict  # Immutable Phase 3 context (always available)
    injected_tier: dict | None  # Learned adjustments (may be None if Skill fails)
    merged_tier: dict  # Final merged result (base + injected if available)


class L10AdapterStage:
    """CEL pipeline stage that calls os.context_adapter Skill.

    Responsibility:
    - Execute Skill with request signals (complexity, task_type, user_context)
    - Merge base + injected context tiers (fail-closed)
    - Emit audit event (decision logged + hash-chained)
    - Return merged context for downstream routing

    Integration:
    - Called by CEL pipeline after L9, before delegation routing
    - Input: base_context, complexity, task_type, user_context, tenant_id
    - Output: merged context dict with learned adjustments
    - Timeout: 2000ms (stricter than L5 for context responsiveness)
    """

    def __init__(self, registry: SkillRegistry | None = None):
        """Initialize L10 adapter stage.

        Args:
            registry: SkillRegistry instance; if None, will try to import at runtime
        """
        self.registry = registry
        self._enabled = True  # Can be toggled via feature flag

    def __call__(self, context_state: dict) -> dict:
        """Execute L10 context adaptation.

        Args:
            context_state: Dict with keys:
                - context: base_tier (immutable context dict)
                - complexity: int [1-10] (task complexity)
                - task_type: str (task category)
                - task_description: str (task description)
                - priority_hint: int [1-10] (user priority hint)
                - user_context: dict (user profile, recent decisions)
                - tenant_id: str (tenant identifier)

        Returns:
            Merged context dict with learned adjustments applied.
        """
        if not self._enabled:
            return context_state.get("context", {})

        try:
            # Get registry if not injected
            registry = self.registry or self._get_registry()
            if registry is None:
                _log.debug("L10: Registry unavailable, returning base context")
                return context_state.get("context", {})

            # Extract base context (immutable)
            base_context = context_state.get("context", {})

            # Build Skill input
            skill_input = {
                "complexity": context_state.get("complexity", 5),
                "task_type": context_state.get("task_type", "general"),
                "task_description": context_state.get("task_description", ""),
                "priority_hint": context_state.get("priority_hint", 5),
                "user_context": context_state.get("user_context", {}),
                "tenant_id": context_state.get("tenant_id", "_default"),
            }

            tenant_id = context_state.get("tenant_id", "_default")

            # Execute Skill (timeout: 2000ms)
            result = registry.execute(
                "os.context_adapter",
                skill_input,
                timeout_ms=2000,
                lom="core/context_engineering/adapter_l10.py:L10AdapterStage.__call__",
                tenant_id=tenant_id,
            )

            # Check execution result
            if result is None or not hasattr(result, "output"):
                _log.warning("L10: Skill returned invalid output, using base context")
                return base_context

            output = result.output if hasattr(result, "output") else {}

            # Merge context tiers (fail-closed)
            merged = self._merge_tiers(
                base_tier=output.get("base_tier", {}),
                injected_tier=output.get("injected_tier", {}),
                merged_tier=output.get("merged_tier", {}),
                base_context=base_context,
            )

            # Emit audit event
            self._emit_audit_event(
                decision_made=True,
                base_tier_keys=len(output.get("base_tier", {})),
                injected_tier_keys=len(output.get("injected_tier", {})),
                merged_tier_keys=len(merged),
                tenant_id=tenant_id,
            )

            return merged

        except TimeoutError:
            _log.warning("L10: Skill execution timed out, using base context")
            self._emit_audit_event(
                decision_made=False,
                reason="timeout",
                tenant_id=context_state.get("tenant_id", "_default"),
            )
            return context_state.get("context", {})

        except Exception as exc:  # noqa: BLE001 — fail-closed
            _log.error("L10: Skill execution failed: %s", exc)
            self._emit_audit_event(
                decision_made=False,
                reason=f"error: {type(exc).__name__}",
                tenant_id=context_state.get("tenant_id", "_default"),
            )
            return context_state.get("context", {})

    def _merge_tiers(
        self,
        base_tier: dict,
        injected_tier: dict | None,
        merged_tier: dict,
        base_context: dict,
    ) -> dict:
        """Merge context tiers (fail-closed).

        Fail-closed logic:
        1. Base tier is immutable (never change)
        2. Injected tier may have learned adjustments (vibe_score, priority_boost)
        3. Merged tier is the final result from Skill
        4. If merge fails, return base_tier (fallback)
        5. Never return partial merge or None

        Args:
            base_tier: Immutable base context
            injected_tier: Learned adjustments (may be None)
            merged_tier: Final merged result from Skill
            base_context: Original base context dict

        Returns:
            Final merged context dict (guaranteed non-empty)
        """
        try:
            # Prefer merged_tier if available and valid
            if isinstance(merged_tier, dict) and len(merged_tier) > 0:
                # Ensure critical fields are present
                if "context_fields" in merged_tier:
                    result = {
                        **merged_tier.get("context_fields", {}),
                        "vibe_score": merged_tier.get("vibe_score", 0.5),
                        "priority": merged_tier.get("priority", 5),
                        "attention_budget": merged_tier.get("attention_budget", 100000),
                        "origin": "l10_adapted",
                    }
                    return result

            # Fallback 1: base_tier
            if isinstance(base_tier, dict) and len(base_tier) > 0:
                return {
                    **base_tier,
                    "origin": "l10_base_tier",
                }

            # Fallback 2: original base_context
            if isinstance(base_context, dict) and len(base_context) > 0:
                return {
                    **base_context,
                    "origin": "l10_fallback",
                }

            # Fallback 3: minimal context
            return {
                "vibe_score": 0.5,
                "priority": 5,
                "attention_budget": 100000,
                "origin": "l10_minimal",
            }

        except Exception as exc:  # noqa: BLE001 — fail-closed
            _log.error("L10 merge failed: %s, returning base context", exc)
            if isinstance(base_context, dict) and len(base_context) > 0:
                return base_context
            return {"origin": "l10_minimal", "vibe_score": 0.5}

    def _emit_audit_event(
        self,
        decision_made: bool,
        reason: str | None = None,
        base_tier_keys: int | None = None,
        injected_tier_keys: int | None = None,
        merged_tier_keys: int | None = None,
        tenant_id: str = "_default",
    ) -> None:
        """Emit audit event (ADR-0299)."""
        try:
            from core.security.audit_logger import audit_event  # noqa: PLC0415

            audit_event(
                "l10_context_adapted",
                {
                    "decision_made": decision_made,
                    "reason": reason,
                    "base_tier_keys": base_tier_keys,
                    "injected_tier_keys": injected_tier_keys,
                    "merged_tier_keys": merged_tier_keys,
                    "timestamp": time.time(),
                },
                tenant_id=tenant_id,
            )
        except Exception as exc:  # noqa: BLE001 — audit failure is non-fatal
            _log.debug("Failed to emit L10 audit event: %s", exc)

    def _get_registry(self) -> SkillRegistry | None:
        """Lazy-load SkillRegistry at runtime."""
        try:
            from core.skills.skill_registry_phase1 import _global_registry  # noqa: PLC0415

            return _global_registry
        except Exception:  # noqa: BLE001
            return None

    def set_enabled(self, enabled: bool) -> None:
        """Enable/disable L10 adapter (for feature flags)."""
        self._enabled = enabled
        _log.info("L10 adapter enabled=%s", enabled)


def create_l10_adapter_stage() -> L10AdapterStage:
    """Factory to create L10 adapter stage for CEL pipeline."""
    return L10AdapterStage()
