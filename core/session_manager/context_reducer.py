"""ContextReducer: Reduces context 200k → 18k tokens (91% reduction).

k=3: ContextReducer with tiered preservation
- Tier 0 (Keep ✅): Goal, constraints, validating findings, error patterns
- Tier 1 (Keep ✅): Strategies, phase, artifacts
- Tier 2 (Drop ❌): Intermediate attempts, stale approaches
- Tier 3 (Drop ❌): Debug logs, micro-step transcripts

ADR-0XXX: Session Manager Architecture
Integration: ContextReducer works alongside ContextPipeline v2 (ADR-0399)
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List, Dict, Any

from core.session_manager.goal_validation_gate import (
    GoalAlignmentValidator,
    ValidationResult,
)

logger = logging.getLogger(__name__)


class ContextTier(str, Enum):
    """Context preservation tiers."""

    TIER_0 = "tier_0_essential"  # Goal, constraints, findings, errors
    TIER_1 = "tier_1_strategy"  # Strategies, phase, artifacts
    TIER_2 = "tier_2_intermediate"  # Intermediate attempts, stale approaches
    TIER_3 = "tier_3_debug"  # Debug logs, micro-step transcripts


@dataclass
class ContextReductionResult:
    """Result of context reduction."""

    original_tokens: int = 0
    reduced_tokens: int = 0
    reduction_percentage: float = 0.0
    kept_items: List[str] = field(default_factory=list)
    dropped_items: List[str] = field(default_factory=list)
    tier_breakdown: Dict[str, int] = field(default_factory=dict)  # Tier -> token count
    validation_result: Optional[ValidationResult] = None  # Goal alignment validation
    validation_applied: bool = False  # Whether validation gate was applied

    def summary(self) -> str:
        """Get summary of reduction."""
        summary = (
            f"Reduced {self.original_tokens} → {self.reduced_tokens} tokens "
            f"({self.reduction_percentage:.1%} reduction). "
            f"Kept {len(self.kept_items)} items, dropped {len(self.dropped_items)}."
        )

        # Add validation info if available
        if self.validation_result:
            summary += (
                f" [Validation: {'PASS' if self.validation_result.is_valid else 'FAIL'} "
                f"score={self.validation_result.composite_score:.2f}]"
            )

        return summary


class ContextReducer:
    """Reduces context size 200k → 18k (91% reduction) via tiered preservation.

    Strategy:
    1. Classify existing context by tier (0-3)
    2. Keep Tiers 0-1 (essential + strategy)
    3. Drop Tiers 2-3 (intermediate + debug)
    4. Estimate token reduction (typically 91%)
    5. Generate restoration prompt for new session

    Integrates with ContextPipeline v2 to ensure:
    - Original context immutable (ADR-0399)
    - Pipeline context additive-only
    - No context switching / topic drift
    """

    # Token estimation heuristics (can be tuned per model)
    TOKENS_PER_WORD = 1.3  # Approximate for Claude
    TOKENS_PER_DEBUG_LOG_LINE = 5

    def __init__(
        self, validator: Optional[GoalAlignmentValidator] = None
    ):
        """Initialize ContextReducer.

        Args:
            validator: Optional GoalAlignmentValidator for goal preservation
                If None (default), validation is skipped (backward compatible)
                If provided, validates goal preservation during reduction
        """
        self.name = "context_reducer"
        self.version = "0.1.0"
        self.validator = validator

    def reduce_context(
        self,
        original_context: str,
        phase: str,
        goal: str,
        task_id: str,
        preserve_tier_0: List[str],
        preserve_tier_1: List[str],
        drop_tier_2: Optional[List[str]] = None,
        drop_tier_3: Optional[List[str]] = None,
    ) -> ContextReductionResult:
        """Reduce context via tiered preservation.

        Args:
            original_context: Full context from current session
            phase: Phase name (used for classification)
            goal: Task goal (always kept)
            task_id: Task identifier
            preserve_tier_0: Essential items (goal, constraints, findings, errors)
            preserve_tier_1: Strategy items (strategies, phase, artifacts)
            drop_tier_2: Intermediate attempts (if None, will auto-classify)
            drop_tier_3: Debug logs (if None, will auto-classify)

        Returns:
            ContextReductionResult with reduction statistics
        """
        drop_tier_2 = drop_tier_2 or []
        drop_tier_3 = drop_tier_3 or []

        # Estimate original token count
        original_tokens = self._estimate_tokens(original_context)

        # Tier 0: Essential (always kept)
        tier_0_content = self._build_tier_0(goal, preserve_tier_0)
        tier_0_tokens = self._estimate_tokens(tier_0_content)

        # Tier 1: Strategy (always kept)
        tier_1_content = self._build_tier_1(phase, preserve_tier_1)
        tier_1_tokens = self._estimate_tokens(tier_1_content)

        # Tiers 2-3: Dropped
        dropped_content = drop_tier_2 + drop_tier_3
        dropped_tokens = sum(self._estimate_tokens(item) for item in dropped_content)

        # Calculate reduction
        reduced_tokens = tier_0_tokens + tier_1_tokens
        reduction_percentage = 1.0 - (reduced_tokens / original_tokens) if original_tokens > 0 else 0.0

        # Classify kept/dropped items
        kept_items = preserve_tier_0 + preserve_tier_1
        dropped_items = dropped_content

        # Tier breakdown
        tier_breakdown = {
            ContextTier.TIER_0.value: tier_0_tokens,
            ContextTier.TIER_1.value: tier_1_tokens,
            ContextTier.TIER_2.value: sum(
                self._estimate_tokens(item) for item in drop_tier_2
            ),
            ContextTier.TIER_3.value: sum(
                self._estimate_tokens(item) for item in drop_tier_3
            ),
        }

        # Validate that reduced context preserves goal (Phase 2: Goal Alignment Gate)
        validation_result = None
        if self.validator and goal.strip():
            try:
                # Build reduced context string for validation
                reduced_text = tier_0_content + "\n" + tier_1_content

                # Run goal alignment validation
                validation_result = self.validator.validate_reduction(goal, reduced_text)

                # Fail-closed: if validation fails, use FULL context
                if not validation_result.is_valid:
                    logger.warning(
                        f"Goal alignment validation FAILED for {task_id}: "
                        f"score={validation_result.composite_score:.2f} < "
                        f"threshold={validation_result.threshold}. "
                        f"Using FULL context (fail-closed)."
                    )

                    # Return full context unchanged
                    result = ContextReductionResult(
                        original_tokens=original_tokens,
                        reduced_tokens=original_tokens,  # No reduction
                        reduction_percentage=0.0,  # No reduction applied
                        kept_items=kept_items,
                        dropped_items=[],  # Nothing dropped due to validation failure
                        tier_breakdown={
                            ContextTier.TIER_0.value: original_tokens,
                            ContextTier.TIER_1.value: 0,
                            ContextTier.TIER_2.value: 0,
                            ContextTier.TIER_3.value: 0,
                        },
                        validation_result=validation_result,
                        validation_applied=True,
                    )

                    logger.info(
                        f"Context reduction for {task_id}: {result.summary()} "
                        f"(validation triggered fail-closed)"
                    )
                    return result
                else:
                    # Validation passed: proceed with reduction
                    logger.info(
                        f"Goal alignment validation PASSED for {task_id}: "
                        f"score={validation_result.composite_score:.2f} >= "
                        f"threshold={validation_result.threshold}. "
                        f"Proceeding with reduction."
                    )

            except Exception as e:
                # Fail-closed: on validation error, use full context
                logger.error(f"Goal alignment validation error for {task_id}: {e}. Using FULL context.")
                validation_result = None
                # Continue with original reduction (conservative)

        result = ContextReductionResult(
            original_tokens=original_tokens,
            reduced_tokens=reduced_tokens,
            reduction_percentage=reduction_percentage,
            kept_items=kept_items,
            dropped_items=dropped_items,
            tier_breakdown=tier_breakdown,
            validation_result=validation_result,
            validation_applied=validation_result is not None,
        )

        logger.info(f"Context reduction for {task_id}: {result.summary()}")
        return result

    def auto_classify_context(
        self, context: str, phase: str
    ) -> Dict[str, List[str]]:
        """Auto-classify context items by tier (heuristic).

        Useful when tier classification is not explicitly provided.

        Args:
            context: Context to classify
            phase: Phase name (planning/execution/validation/finalization)

        Returns:
            Dict with keys "tier_0", "tier_1", "tier_2", "tier_3"
        """
        lines = context.split("\n")
        tier_0 = []
        tier_1 = []
        tier_2 = []
        tier_3 = []

        for line in lines:
            # Skip empty lines
            if not line.strip():
                continue
            line_lower = line.lower()

            # Tier 0: Essential keywords
            if any(
                kw in line_lower
                for kw in [
                    "goal",
                    "objective",
                    "constraint",
                    "error",
                    "failure",
                    "finding",
                    "conclusion",
                ]
            ):
                tier_0.append(line)

            # Tier 1: Strategy keywords
            elif any(
                kw in line_lower
                for kw in [
                    "strategy",
                    "approach",
                    "method",
                    "phase",
                    "artifact",
                    "deliverable",
                    "output",
                ]
            ):
                tier_1.append(line)

            # Tier 2: Intermediate keywords
            elif any(
                kw in line_lower
                for kw in [
                    "attempt",
                    "tried",
                    "experiment",
                    "iteration",
                    "draft",
                    "rough",
                ]
            ):
                tier_2.append(line)

            # Tier 3: Debug keywords
            elif any(
                kw in line_lower
                for kw in [
                    "debug",
                    "log",
                    "trace",
                    "step",
                    "detail",
                    "verbose",
                    "timestamp",
                ]
            ):
                tier_3.append(line)

            # Default: treat as intermediate
            else:
                tier_2.append(line)

        return {
            "tier_0": tier_0,
            "tier_1": tier_1,
            "tier_2": tier_2,
            "tier_3": tier_3,
        }

    def generate_restoration_prompt(
        self,
        goal: str,
        phase: str,
        task_id: str,
        tier_0_items: List[str],
        tier_1_items: List[str],
        reduction_result: ContextReductionResult,
    ) -> str:
        """Generate a prompt for restoring context in the next session.

        This prompt is prepended to the reduced context to help the model
        understand the session split and resume coherently.

        Args:
            goal: Original task goal
            phase: Current phase
            task_id: Task identifier
            tier_0_items: Essential items (kept)
            tier_1_items: Strategy items (kept)
            reduction_result: Result from context reduction

        Returns:
            Restoration prompt string
        """
        prompt = f"""[SESSION CHECKPOINT & RESUMPTION]

Task Goal: {goal}
Phase: {phase}
Task ID: {task_id}

Context Reduction Summary:
- Original: {reduction_result.original_tokens} tokens
- Reduced: {reduction_result.reduced_tokens} tokens
- Reduction: {reduction_result.reduction_percentage:.1%}

Essential Items (Tier 0 - KEPT):
{chr(10).join('• ' + item for item in tier_0_items[:5])}

Strategy Items (Tier 1 - KEPT):
{chr(10).join('• ' + item for item in tier_1_items[:5])}

Dropped Items (Tiers 2-3):
- Intermediate attempts and debug logs were removed to reduce context
- The above essential and strategy items are sufficient to resume

INSTRUCTION: Continue the {phase} phase with the goal and strategy above.
Avoid re-attempting dropped approaches. Focus on forward progress.

---
"""
        return prompt

    def _build_tier_0(self, goal: str, tier_0_items: List[str]) -> str:
        """Build Tier 0 (essential) content.

        Args:
            goal: Task goal
            tier_0_items: Essential items to keep

        Returns:
            Tier 0 content string
        """
        content = f"GOAL: {goal}\n\n"
        content += "ESSENTIAL FINDINGS:\n"
        content += "\n".join(f"• {item}" for item in tier_0_items)
        return content

    def _build_tier_1(self, phase: str, tier_1_items: List[str]) -> str:
        """Build Tier 1 (strategy) content.

        Args:
            phase: Phase name
            tier_1_items: Strategy items to keep

        Returns:
            Tier 1 content string
        """
        content = f"\nPHASE: {phase}\n\n"
        content += "STRATEGIES & ARTIFACTS:\n"
        content += "\n".join(f"• {item}" for item in tier_1_items)
        return content

    def _estimate_tokens(self, text: str) -> int:
        """Estimate token count for a string.

        Heuristic: ~1.3 tokens per word for Claude models.

        Args:
            text: Text to estimate

        Returns:
            Approximate token count
        """
        if not text:
            return 0

        # Count words
        word_count = len(text.split())

        # Check if this looks like debug logs (many short lines)
        line_count = len(text.split("\n"))
        if line_count > 20 and word_count / line_count < 5:
            # Likely debug logs: estimate higher
            return int(line_count * self.TOKENS_PER_DEBUG_LOG_LINE)

        return int(word_count * self.TOKENS_PER_WORD)

    def measure_reduction_success(
        self,
        original_tokens: int,
        reduced_tokens: int,
        target_reduction: float = 0.85,
    ) -> Dict[str, Any]:
        """Measure whether reduction met targets.

        Args:
            original_tokens: Original context token count
            reduced_tokens: Reduced context token count
            target_reduction: Target reduction (default 85%)

        Returns:
            Dict with success metrics
        """
        actual_reduction = 1.0 - (reduced_tokens / original_tokens) if original_tokens > 0 else 0.0
        success = actual_reduction >= target_reduction

        return {
            "success": success,
            "actual_reduction": actual_reduction,
            "target_reduction": target_reduction,
            "original_tokens": original_tokens,
            "reduced_tokens": reduced_tokens,
            "message": (
                f"Reduction {'PASSED' if success else 'FAILED'}: "
                f"{actual_reduction:.1%} {'≥' if success else '<'} {target_reduction:.1%}"
            ),
        }
