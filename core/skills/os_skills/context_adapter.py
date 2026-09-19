"""os.context_adapter Skill — L10 context engineering (ADR-0555 + ADR-0532 Phase 1).

Wires context selection into the security pipeline (ADR-0300 Dual Gate).
Implements Phase 2 Blocker 2 fix: making CEL context-building observable.
"""

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ContextSource:
    """A single context source (e.g., user model, session history)."""
    name: str
    priority: int  # 0=critical, 100=optional
    relevance: float  # 0.0-1.0
    tokens: int


class ContextAdapterSkill:
    """L10 context adapter (ADR-0555).

    Takes actor/action/input_data and adapts context for the security pipeline.
    Fail-open, non-blocking (ADR-0300).
    """

    async def execute(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """Adapt context based on actor, action, input.

        Input:
            actor: user/entity making request
            action: what they're trying to do
            input_data: the data they're working with
            tenant_id: which tenant (for multi-tenant isolation)

        Output:
            context_brief: {
                sources: [ContextSource, ...],
                confidence: "high" | "medium" | "low",
                tokens: int
            }
        """
        try:
            actor = input_data.get("actor", {})
            action = input_data.get("action", "unknown")
            data = input_data.get("input_data", {})
            tenant_id = input_data.get("tenant_id", "_default")

            # Phase 2: Basic context adaptation (minimal, pass-through)
            # Future: integrate user models, session history, attention budgets
            sources: List[ContextSource] = []
            total_tokens = 0

            # Context source 1: Actor role (if available)
            if actor and isinstance(actor, dict) and actor.get("role"):
                role_source = ContextSource(
                    name=f"actor_role:{actor.get('role')}",
                    priority=10,
                    relevance=0.8,
                    tokens=50,
                )
                sources.append(role_source)
                total_tokens += role_source.tokens

            # Context source 2: Action type (always present)
            action_source = ContextSource(
                name=f"action_type:{action}",
                priority=5,
                relevance=0.9,
                tokens=30,
            )
            sources.append(action_source)
            total_tokens += action_source.tokens

            # Confidence: based on source count + relevance
            confidence = "high" if len(sources) >= 2 else "medium"

            logger.debug(
                f"[ContextAdapterSkill] actor={actor.get('id', 'unknown')} "
                f"action={action} sources={len(sources)} tokens={total_tokens} tenant={tenant_id}"
            )

            return {
                "context_brief": {
                    "sources": [
                        {
                            "name": s.name,
                            "priority": s.priority,
                            "relevance": s.relevance,
                            "tokens": s.tokens,
                        }
                        for s in sources
                    ],
                    "confidence": confidence,
                    "tokens": total_tokens,
                },
                "status": "ok",
            }

        except Exception as e:
            logger.exception(f"[ContextAdapterSkill] Exception: {e}")
            # Fail-open: return empty context, don't block (ADR-0300)
            return {
                "context_brief": {
                    "sources": [],
                    "confidence": "low",
                    "tokens": 0,
                },
                "status": "error",
                "error": str(e),
            }


# Singleton instance for use in pipeline
_skill_instance: Optional[ContextAdapterSkill] = None


def get_context_adapter() -> ContextAdapterSkill:
    """Get or create the singleton ContextAdapterSkill."""
    global _skill_instance
    if _skill_instance is None:
        _skill_instance = ContextAdapterSkill()
    return _skill_instance
