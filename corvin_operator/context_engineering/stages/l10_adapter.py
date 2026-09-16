"""L10 Context Adapter stage (ADR-0532 Phase 2b) — Skill-driven context adjustment.

Integrates os.context_adapter Skill into the CEL pipeline. Learns from user
feedback and prior task outcomes to adjust context (vibe_score, priority,
attention_budget) before routing (L5).

Architecture:
  Request → L1-L9 CEL stages → L10_Adapt (THIS STAGE) → Merge → L5 Routing

Stage attributes:
  - id: "l10_adapter"
  - requires: ("graph",) — consumes decisions from graph stage
  - effect: "pure" — no external I/O, deterministic (Skill call is wrapped)
  - trust: "builtin" — first-party implementation

Fail-closed:
  - Skill timeout (>2s): return base context
  - Skill error: return base context
  - Malformed output: return base context
"""
from __future__ import annotations

import logging
from .base import StageTelemetry
from .registry import register_stage
from ._util import confidence_tier

_log = logging.getLogger(__name__)


class L10AdapterStage:
    """CEL pipeline stage that consults os.context_adapter Skill.

    Executes the context_adapter Skill with task complexity, type, and user
    context, then merges learned adjustments into the brief.
    """
    id = "l10_adapter"
    requires: tuple = ("graph",)  # depends on graph decisions
    effect = "pure"  # no external I/O outside Skill call
    trust = "builtin"  # first-party implementation

    def run(self, bundle, ctx):
        """Execute L10 context adaptation.

        Args:
            bundle: ContextBundle (brief, scratch, etc.)
            ctx: StageCtx (tenant_id, task_obj, etc.)

        Returns:
            (bundle, telemetry) tuple
        """
        try:
            # Import at runtime to avoid circular dependencies
            from core.skills.os_skills_integration import adapt_context_l10  # noqa: PLC0415

            # Build input for the Skill
            brief = bundle.brief
            task_obj = ctx.task_obj

            # Extract task signals
            complexity = getattr(task_obj, "complexity", 5)
            task_type = getattr(task_obj, "task_type", "general")
            task_description = getattr(task_obj, "description", "")
            priority_hint = getattr(task_obj, "priority", 5)
            user_context = getattr(task_obj, "user_context", {})

            # Call the L10 Skill (this is the REAL PRODUCTION CALL SITE)
            adapted_result = adapt_context_l10(
                complexity=complexity,
                task_type=task_type,
                task_description=task_description,
                priority_hint=priority_hint,
                user_context=user_context,
                tenant_id=ctx.tenant_id,
            )

            # Update brief with adapted context
            bundle.brief.adapted_context = adapted_result
            bundle.scratch["adapted_context"] = adapted_result

            # Extract confidence and origin for telemetry
            origin = adapted_result.get("origin", "unknown") if adapted_result else "unknown"
            vibe_score = adapted_result.get("vibe_score", 0.5) if adapted_result else 0.5

            # Map origin to confidence tier
            tier_map = {
                "l10_adapted": "high",      # full Skill execution
                "l10_base_tier": "medium",  # base tier fallback
                "l10_fallback": "medium",   # original base context
                "l10_minimal": "low",       # minimal fallback
            }
            confidence = tier_map.get(origin, "medium")

            # Telemetry
            tel = StageTelemetry(
                stage="l10_adapter",
                status="ok",
                confidence_tier=confidence,
                sources=[
                    {"id": "vibe_score", "score": vibe_score},
                    {"id": "origin", "score": 1.0 if origin.startswith("l10_adapted") else 0.5},
                ]
            )

            return bundle, tel

        except TimeoutError:
            _log.warning("L10 adapter timed out, using base context")
            return bundle, StageTelemetry(
                stage="l10_adapter",
                status="failed",
                confidence_tier="low",
                reason="timeout",
            )
        except Exception as exc:  # noqa: BLE001 — fail-closed
            _log.error("L10 adapter failed: %s", exc)
            return bundle, StageTelemetry(
                stage="l10_adapter",
                status="failed",
                confidence_tier="low",
                reason=f"error: {type(exc).__name__}",
                error=str(exc),
            )


# Self-register at import
register_stage(L10AdapterStage())
