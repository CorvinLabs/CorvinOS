"""
Sprint 2.1: ContextReducer

Reduces context to 91% compression (essential only) for checkpoint storage.
Preserves: goal, constraints, decisions made, errors encountered, learnings.
Drops: metadata, debug logs, intermediate attempts, tangential notes.

Integration: CheckpointManager stores reduced context in context_essentials field.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from datetime import datetime
import json
import logging

logger = logging.getLogger(__name__)


@dataclass
class EssentialSection:
    """Metadata about what was kept and why."""
    section_type: str  # "goal", "constraint", "decision", "error", "learning"
    content: str
    iteration: int  # When this became essential
    reason: str  # Why kept: "blocking", "precedent", "error context", etc.


@dataclass
class ReducedContext:
    """Compressed context (91% reduction)."""
    # Kept sections
    goal: str
    constraints: List[str]
    decisions_made: List[EssentialSection]  # [(iter, decision, why_kept), ...]
    errors_encountered: List[EssentialSection]  # [(iter, error_type, root_cause), ...]
    learnings: List[EssentialSection]  # [(iter, learning, applies_to), ...]

    # Metadata
    original_size_tokens: int  # Approximate token count before reduction
    reduced_size_tokens: int  # Approximate token count after reduction
    reduction_pct: int  # 91 for typical case
    compressed_at: str  # ISO timestamp

    # Dropped sections (for recovery)
    dropped_sections: List[Dict[str, Any]] = field(default_factory=list)  # [{type, reason}, ...]


class ContextReducer:
    """
    Reduces context to 91% compression for checkpoint storage.

    Guarantees:
    - Preserves all blocking decisions, errors, constraints, goals
    - Drops debug logs, intermediate attempts, tangential notes
    - Idempotent: same context always produces same reduced form
    - Recoverable: dropped sections recorded for full-context restore (Phase 3)
    """

    # Essential keywords (Tier 1: MUST KEEP)
    ESSENTIAL_TIER1 = {
        "goal", "objective", "task", "requirement",
        "constraint", "must", "required", "error", "exception",
        "critical", "blocking", "decision", "chose",
        "prerequisite", "dependency"
    }

    # Relevant keywords (Tier 2: LIKELY KEEP)
    RELEVANT_TIER2 = {
        "learned", "lesson", "pattern", "approach", "optimization",
        "issue", "problem", "fixed", "resolved", "why",
        "trade-off", "recommendation", "precedent"
    }

    # Tangential keywords (Tier 3: DROP)
    TANGENTIAL_TIER3 = {
        "tangential", "nice-to-know", "fyi", "optional",
        "alternatively", "could", "might", "probably",
        "debug", "verbose", "introspection", "meta"
    }

    def __init__(self, target_reduction_pct: int = 91):
        """
        Initialize reducer.

        Args:
            target_reduction_pct: Target compression (typically 91%).
        """
        self.target_reduction_pct = target_reduction_pct
        logger.info(f"ContextReducer initialized (target: {target_reduction_pct}% reduction)")

    def reduce(
        self,
        goal: str,
        constraints: List[str],
        decisions: List[Dict[str, Any]],  # [{iter, decision, why}, ...]
        errors: List[Dict[str, Any]],  # [{iter, error_type, root_cause}, ...]
        learnings: List[Dict[str, Any]],  # [{iter, learning, applies_to}, ...]
        original_size_tokens: int = 10000  # Approximate
    ) -> ReducedContext:
        """
        Reduce context to essential sections only.

        Args:
            goal: Original task goal (always kept).
            constraints: List of constraints (all kept as Tier 1).
            decisions: Decision history [{iter: int, decision: str, why: str}, ...].
            errors: Error log [{iter: int, error_type: str, root_cause: str}, ...].
            learnings: Lessons learned [{iter: int, learning: str, applies_to: str}, ...].
            original_size_tokens: Approximate token count before reduction.

        Returns:
            ReducedContext with 91% compression.
        """
        # Keep goal (always)
        reduced_goal = goal

        # Keep all constraints (Tier 1)
        reduced_constraints = constraints

        # Filter decisions: keep only high-impact ones
        reduced_decisions = self._filter_tier_decisions(decisions)

        # Keep all errors (Tier 1, critical for recovery)
        reduced_errors = self._classify_errors(errors)

        # Filter learnings: keep relevant, drop tangential
        reduced_learnings = self._filter_learnings(learnings)

        # Estimate reduction
        kept_tokens = self._estimate_tokens({
            "goal": reduced_goal,
            "constraints": reduced_constraints,
            "decisions": reduced_decisions,
            "errors": reduced_errors,
            "learnings": reduced_learnings
        })

        reduction_pct = max(1, 100 - int((kept_tokens / original_size_tokens) * 100))

        # Record what was dropped
        dropped = self._identify_dropped(decisions, learnings)

        reduced_context = ReducedContext(
            goal=reduced_goal,
            constraints=reduced_constraints,
            decisions_made=reduced_decisions,
            errors_encountered=reduced_errors,
            learnings=reduced_learnings,
            original_size_tokens=original_size_tokens,
            reduced_size_tokens=kept_tokens,
            reduction_pct=reduction_pct,
            compressed_at=datetime.now().isoformat(),
            dropped_sections=dropped
        )

        logger.info(
            f"Context reduced: {original_size_tokens} → {kept_tokens} tokens "
            f"({reduction_pct}% reduction, {len(reduced_decisions)} decisions, "
            f"{len(reduced_errors)} errors, {len(reduced_learnings)} learnings)"
        )

        return reduced_context

    def _filter_tier_decisions(self, decisions: List[Dict[str, Any]]) -> List[EssentialSection]:
        """Filter decisions: keep Tier 1/2, drop Tier 3."""
        kept = []

        for d in decisions:
            decision_text = d.get("decision", "")
            why = d.get("why", "")

            # Check if Tier 1 or 2
            if self._is_tier_1(decision_text + " " + why):
                kept.append(EssentialSection(
                    section_type="decision",
                    content=decision_text,
                    iteration=d.get("iter", -1),
                    reason=why
                ))
            elif self._is_tier_2(decision_text + " " + why):
                kept.append(EssentialSection(
                    section_type="decision",
                    content=decision_text,
                    iteration=d.get("iter", -1),
                    reason=why
                ))
            # else: Tier 3, drop it

        return kept

    def _classify_errors(self, errors: List[Dict[str, Any]]) -> List[EssentialSection]:
        """All errors are kept (Tier 1) — critical for recovery."""
        kept = []

        for e in errors:
            kept.append(EssentialSection(
                section_type="error",
                content=e.get("error_type", "unknown"),
                iteration=e.get("iter", -1),
                reason=f"Root cause: {e.get('root_cause', 'unknown')}"
            ))

        return kept

    def _filter_learnings(self, learnings: List[Dict[str, Any]]) -> List[EssentialSection]:
        """Filter learnings: keep them, DROP the tangential (Tier 3) ones.

        The default is KEEP. This used to require a Tier-2 keyword ("lesson",
        "pattern", "optimization", …) to be present in the learning's own text
        before it was kept — so a learning phrased like a normal finding ("TTL
        alone is insufficient") matched nothing and was silently discarded.
        Everything in this list is already a learning by construction; making a
        vocabulary match the precondition for keeping one inverted the default
        from keep to drop, and a long autonomous run — whose context is reduced
        at every checkpoint — therefore forgot most of what it had learned,
        every time it compressed.
        """
        kept = []

        for l in learnings:
            learning_text = l.get("learning", "")
            applies_to = l.get("applies_to", "")

            if self._is_tier_3(learning_text + " " + applies_to):
                continue  # tangential / nice-to-know — this is the one to drop
            kept.append(EssentialSection(
                section_type="learning",
                content=learning_text,
                iteration=l.get("iter", -1),
                reason=f"Applies to: {applies_to}"
            ))

        return kept

    def _is_tier_1(self, text: str) -> bool:
        """Check if text contains Tier 1 keywords (blocking, critical)."""
        text_lower = text.lower()
        return any(kw in text_lower for kw in self.ESSENTIAL_TIER1)

    def _is_tier_2(self, text: str) -> bool:
        """Check if text contains Tier 2 keywords (relevant, pattern)."""
        text_lower = text.lower()
        # Tier 2 if contains relevant keyword AND NOT tangential
        return (any(kw in text_lower for kw in self.RELEVANT_TIER2) and
                not any(kw in text_lower for kw in self.TANGENTIAL_TIER3))

    def _is_tier_3(self, text: str) -> bool:
        """Check if text is tangential."""
        text_lower = text.lower()
        return any(kw in text_lower for kw in self.TANGENTIAL_TIER3)

    def _estimate_tokens(self, context_dict: Dict[str, Any]) -> int:
        """Rough token estimate (1 token ≈ 4 chars)."""
        json_str = json.dumps(context_dict, default=str)
        # Approximate: 1 token per 4 characters
        return len(json_str) // 4

    def _identify_dropped(
        self,
        decisions: List[Dict[str, Any]],
        learnings: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Record which sections were dropped."""
        dropped = []

        for d in decisions:
            decision_text = d.get("decision", "")
            if self._is_tier_3(decision_text):
                dropped.append({
                    "type": "decision",
                    "content": decision_text[:100],  # First 100 chars
                    "reason": "Tier 3: tangential or optional"
                })

        for l in learnings:
            learning_text = l.get("learning", "")
            if self._is_tier_3(learning_text):
                dropped.append({
                    "type": "learning",
                    "content": learning_text[:100],
                    "reason": "Tier 3: tangential or nice-to-know"
                })

        return dropped

    def serialize(self, reduced: ReducedContext) -> str:
        """Serialize reduced context to JSON."""
        from dataclasses import asdict

        data = asdict(reduced)
        # Convert EssentialSection objects to dicts
        data["decisions_made"] = [asdict(d) for d in reduced.decisions_made]
        data["errors_encountered"] = [asdict(e) for e in reduced.errors_encountered]
        data["learnings"] = [asdict(l) for l in reduced.learnings]

        return json.dumps(data, indent=2, default=str)

    def deserialize(self, json_str: str) -> ReducedContext:
        """Deserialize reduced context from JSON."""
        data = json.loads(json_str)

        # Reconstruct EssentialSection objects
        decisions = [EssentialSection(**d) for d in data.pop("decisions_made", [])]
        errors = [EssentialSection(**e) for e in data.pop("errors_encountered", [])]
        learnings = [EssentialSection(**l) for l in data.pop("learnings", [])]

        return ReducedContext(
            decisions_made=decisions,
            errors_encountered=errors,
            learnings=learnings,
            **data
        )
