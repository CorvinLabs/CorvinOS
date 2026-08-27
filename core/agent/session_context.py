"""SessionContext: Frozen user intent for session lifetime (ADR-0403)."""

from dataclasses import dataclass
from typing import Dict, Optional
from datetime import datetime


@dataclass(frozen=True)
class SessionContext:
    """
    User's current request — FROZEN for session duration.
    NEVER OVERWRITTEN by memory or other context sources.
    """
    user_request: str              # "mach Week 3-4", "do adversarial review"
    explicit_constraints: Dict     # {"rollout": "100%", "strategy": "direct"}
    task_scope: str                # "production readiness", "autonomy"
    session_timestamp: datetime

    def is_authoritative_on(self, topic: str) -> bool:
        """
        Does session context own this topic?

        Session is authoritative if user explicitly mentioned it.
        Example: "mach Week 3-4" → session owns "Week"
        """
        keywords = self.user_request.lower().split()
        topic_lower = topic.lower()

        # Direct keyword match
        if topic_lower in keywords:
            return True

        # Substring match (e.g. "orchestration" in "task_orchestration")
        for kw in keywords:
            if topic_lower in kw or kw in topic_lower:
                return True

        # Constraint-based match
        if any(c.lower() in topic_lower for c in self.explicit_constraints.keys()):
            return True

        return False

    def get_authority_reason(self, topic: str) -> Optional[str]:
        """Why is session authoritative on this topic?"""
        if topic.lower() in self.user_request.lower():
            return f"User explicitly mentioned: '{topic}'"

        for constraint in self.explicit_constraints:
            if constraint.lower() in topic.lower():
                return f"User constraint applies: {constraint}={self.explicit_constraints[constraint]}"

        return None


@dataclass
class MemoryContext:
    """
    Prior learnings — ADDITIVE ONLY, NEVER REPLACES SESSION.
    Used to argue/support session intent, not override it.
    """
    related_adrs: list              # ["ADR-0401", "ADR-0402"] (context)
    prior_findings: list            # ["was in audit", "learned X"] (arguments)
    architectural_patterns: list    # Reusable insights

    def can_augment(self, session: SessionContext, topic: str) -> bool:
        """
        Can memory augment session context on this topic?

        YES if:
        - Session doesn't own topic AND
        - Memory has relevant info

        NO if:
        - Session explicitly mentioned topic
        """
        return not session.is_authoritative_on(topic) and (
            self.related_adrs or self.prior_findings
        )


class ContextConflictResolver:
    """Resolves conflicts between SessionContext and MemoryContext."""

    @staticmethod
    def resolve(session: SessionContext, memory: MemoryContext,
               topic: str) -> dict:
        """
        Resolve context conflict for a topic.

        Returns: {
            "source": "SESSION" | "MEMORY" | "NONE",
            "value": str,
            "reason": str,
            "declared": bool  # True if conflict was detected
        }
        """
        session_owns = session.is_authoritative_on(topic)
        memory_can_augment = memory.can_augment(session, topic)

        if session_owns:
            # Session wins (default case)
            return {
                "source": "SESSION",
                "value": session.user_request,
                "reason": session.get_authority_reason(topic),
                "declared": False,
            }
        elif memory_can_augment:
            # Memory can argue (session doesn't own topic)
            return {
                "source": "MEMORY",
                "value": str(memory.prior_findings),
                "reason": f"Memory augments (Session doesn't override)",
                "declared": False,
            }
        else:
            # No opinion
            return {
                "source": "NONE",
                "value": None,
                "reason": "No context available",
                "declared": False,
            }

    @staticmethod
    def declare_conflict(session: SessionContext, memory: MemoryContext,
                        topic: str) -> str:
        """Explicitly declare context conflict for user visibility."""
        resolved = ContextConflictResolver.resolve(session, memory, topic)

        if resolved["source"] == "SESSION":
            return f"✓ SESSION (authoritative): {resolved['reason']}"
        elif resolved["source"] == "MEMORY":
            return f"ⓘ MEMORY (argues): {resolved['reason']}"
        else:
            return f"⊘ NO CONTEXT: {resolved['reason']}"
