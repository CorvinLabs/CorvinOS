"""GuidanceClassifier — L6 decision classification engine (Phase 3, ADR-0423).

Classifies task decisions into strategic categories (refactor, parallelize, optimize, etc.)
based on ExecutionContext + heuristics. Outputs confidence scores and fallback strategy.

Classification categories:
  - refactor: reorganize task structure (reduce complexity, improve clarity)
  - parallelize: split sequential work into parallel branches
  - optimize_latency: reduce execution time (increase concurrency/speed)
  - optimize_cost: reduce resource consumption (fewer API calls, smaller models)
  - error_recovery: handle failure gracefully (retry, fallback, degradation)
  - context_split: split task to manage context budget (reduce token usage)
  - feature_gating: new feature behind experiment flag (safe rollout)

Confidence threshold: 0.7 (below = use heuristic fallback)
Integration: Wired to ContextBus, publishes vibe.guidance_requested events.
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from datetime import datetime
from enum import Enum
import logging
import json

logger = logging.getLogger(__name__)


class GuidanceCategory(str, Enum):
    """Strategic guidance categories for task decisions."""

    REFACTOR = "refactor"
    PARALLELIZE = "parallelize"
    OPTIMIZE_LATENCY = "optimize_latency"
    OPTIMIZE_COST = "optimize_cost"
    ERROR_RECOVERY = "error_recovery"
    CONTEXT_SPLIT = "context_split"
    FEATURE_GATING = "feature_gating"


@dataclass
class DecisionContext:
    """Input context for guidance classification.

    Captures task state, constraints, and execution metrics to enable classification.
    """

    task_id: str
    tenant_id: str
    task_type: str  # e.g., "data_analysis", "code_generation", "research"
    description: str  # short task description
    current_iteration: int = 0
    max_iterations: int = 10
    budget_remaining: float = 1.0  # fraction (0.0–1.0)
    time_remaining: int = 0  # seconds
    tokens_used: int = 0
    tokens_available: int = 4096
    complexity_score: float = 0.5  # 0.0–1.0 estimate of task complexity
    parallel_branches: int = 0  # number of parallel work streams identified
    error_rate: float = 0.0  # fraction of attempts that failed (0.0–1.0)
    model: str = "claude-3-5-sonnet"
    execution_history: List[Dict[str, Any]] = field(default_factory=list)
    previous_decisions: List[Dict[str, Any]] = field(default_factory=list)
    guidance_overrides: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize context for audit/logging."""
        return {
            "task_id": self.task_id,
            "tenant_id": self.tenant_id,
            "task_type": self.task_type,
            "description": self.description,
            "current_iteration": self.current_iteration,
            "max_iterations": self.max_iterations,
            "budget_remaining": self.budget_remaining,
            "time_remaining": self.time_remaining,
            "tokens_used": self.tokens_used,
            "tokens_available": self.tokens_available,
            "complexity_score": self.complexity_score,
            "parallel_branches": self.parallel_branches,
            "error_rate": self.error_rate,
            "model": self.model,
        }


@dataclass
class GuidanceDecision:
    """Output from guidance classifier.

    Represents a strategic recommendation for task execution.
    """

    category: GuidanceCategory
    confidence: float  # 0.0–1.0
    rationale: str  # human-readable explanation
    fallback_used: bool = False  # whether heuristic fallback was used
    recommended_action: str = ""  # specific action to take
    severity: str = "info"  # "info", "warning", "critical"
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    supporting_metrics: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for audit/logging."""
        return {
            "category": self.category.value,
            "confidence": self.confidence,
            "rationale": self.rationale,
            "fallback_used": self.fallback_used,
            "recommended_action": self.recommended_action,
            "severity": self.severity,
            "timestamp": self.timestamp,
            "supporting_metrics": self.supporting_metrics,
        }

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict(), indent=2)


class GuidanceClassifier:
    """Strategic decision classifier for task execution.

    Two-stage classification:
      1. LLM-based: Use language model for nuanced decision (if available)
      2. Heuristic fallback: Use rule-based logic if LLM confidence < 0.7

    Thread-safe; can be used across multiple ExecutionContexts.
    """

    # Confidence threshold for accepting LLM classification (below = fallback)
    CONFIDENCE_THRESHOLD = 0.7

    # Heuristic rules: (predicate, category, confidence, action)
    HEURISTIC_RULES = [
        # Error recovery: high error rate
        (
            lambda ctx: ctx.error_rate > 0.3,
            GuidanceCategory.ERROR_RECOVERY,
            0.8,
            "Implement retry logic with exponential backoff",
        ),
        # Context split: low budget or high token usage
        (
            lambda ctx: ctx.budget_remaining < 0.2 or (
                ctx.tokens_used > ctx.tokens_available * 0.8
            ),
            GuidanceCategory.CONTEXT_SPLIT,
            0.75,
            "Split task into smaller checkpoints to preserve context",
        ),
        # Parallelize: identified parallel branches
        (
            lambda ctx: ctx.parallel_branches >= 2,
            GuidanceCategory.PARALLELIZE,
            0.7,
            f"Execute {ctx.parallel_branches} branches in parallel",
        ),
        # Optimize latency: many iterations remaining with time budget
        (
            lambda ctx: (
                ctx.current_iteration < ctx.max_iterations / 2
                and ctx.time_remaining > 300
            ),
            GuidanceCategory.OPTIMIZE_LATENCY,
            0.65,
            "Increase concurrency or use faster model for remaining iterations",
        ),
        # Optimize cost: high token budget but low time pressure
        (
            lambda ctx: (
                ctx.budget_remaining > 0.5 and ctx.time_remaining < 60
            ),
            GuidanceCategory.OPTIMIZE_COST,
            0.6,
            "Switch to more efficient model or reduce output length",
        ),
        # Refactor: high complexity score
        (
            lambda ctx: ctx.complexity_score > 0.7,
            GuidanceCategory.REFACTOR,
            0.65,
            "Break task into simpler, sequential subtasks",
        ),
    ]

    def __init__(self, enable_llm: bool = False, llm_fn=None):
        """Initialize classifier.

        Args:
            enable_llm: Whether to attempt LLM-based classification
            llm_fn: Optional async function (context: DecisionContext) -> GuidanceDecision
                   for LLM-based classification. If None and enable_llm=True, will
                   use a default placeholder.
        """
        self.enable_llm = enable_llm
        self.llm_fn = llm_fn
        self._call_count = 0
        self._fallback_count = 0

    async def classify(
        self, context: DecisionContext
    ) -> GuidanceDecision:
        """Classify a decision context into strategic category.

        Two-stage process:
          1. Try LLM classification if enabled (async)
          2. Fall back to heuristic if LLM confidence < 0.7 or LLM fails

        Args:
            context: DecisionContext with task state and metrics

        Returns:
            GuidanceDecision with category, confidence, and rationale
        """
        self._call_count += 1

        # Stage 1: Try LLM classification if enabled
        if self.enable_llm:
            try:
                decision = await self._classify_llm(context)
                if decision.confidence >= self.CONFIDENCE_THRESHOLD:
                    logger.info(
                        f"LLM classification (high confidence): "
                        f"{decision.category.value} ({decision.confidence:.2f})"
                    )
                    return decision
                logger.debug(
                    f"LLM confidence below threshold ({decision.confidence:.2f}), "
                    f"falling back to heuristic"
                )
            except Exception as e:
                logger.warning(f"LLM classification failed: {e}, using heuristic")

        # Stage 2: Heuristic fallback
        self._fallback_count += 1
        decision = self._classify_heuristic(context)
        decision.fallback_used = True
        logger.info(
            f"Heuristic classification: {decision.category.value} "
            f"({decision.confidence:.2f})"
        )
        return decision

    async def _classify_llm(
        self, context: DecisionContext
    ) -> GuidanceDecision:
        """Classify using LLM (if llm_fn is provided).

        Args:
            context: DecisionContext with task state

        Returns:
            GuidanceDecision from LLM

        Raises:
            RuntimeError: If no LLM function configured
        """
        if not self.llm_fn:
            raise RuntimeError(
                "LLM classification enabled but no llm_fn provided"
            )

        # Call LLM function (expected to be async)
        decision = await self.llm_fn(context)
        if not isinstance(decision, GuidanceDecision):
            raise TypeError(f"LLM returned {type(decision)}, expected GuidanceDecision")
        return decision

    def _classify_heuristic(self, context: DecisionContext) -> GuidanceDecision:
        """Classify using heuristic rules (rule-based fallback).

        Evaluates rules in order; returns first match with highest confidence.

        Args:
            context: DecisionContext with task state

        Returns:
            GuidanceDecision from heuristic rules
        """
        best_decision = None
        best_confidence = 0.0

        for predicate, category, confidence, action in self.HEURISTIC_RULES:
            try:
                if predicate(context):
                    if confidence > best_confidence:
                        best_decision = GuidanceDecision(
                            category=category,
                            confidence=confidence,
                            rationale=self._build_rationale(category, context),
                            recommended_action=action,
                            severity="info",
                            supporting_metrics=self._extract_metrics(context),
                        )
                        best_confidence = confidence
            except Exception as e:
                logger.warning(f"Rule evaluation failed: {e}, skipping")

        # If no rule matched, default to feature_gating (safest, least intrusive)
        if best_decision is None:
            best_decision = GuidanceDecision(
                category=GuidanceCategory.FEATURE_GATING,
                confidence=0.5,
                rationale="No specific guidance needed; using feature flag for safe rollout",
                recommended_action="Ship feature behind experiment flag",
                severity="info",
                supporting_metrics=self._extract_metrics(context),
            )

        return best_decision

    def _build_rationale(
        self, category: GuidanceCategory, context: DecisionContext
    ) -> str:
        """Build human-readable rationale for classification."""
        if category == GuidanceCategory.ERROR_RECOVERY:
            return (
                f"High error rate ({context.error_rate:.0%}) detected. "
                f"Recommend implementing retry logic with exponential backoff."
            )
        elif category == GuidanceCategory.CONTEXT_SPLIT:
            return (
                f"Budget low ({context.budget_remaining:.0%} remaining, "
                f"{context.tokens_used} / {context.tokens_available} tokens). "
                f"Split task to preserve context."
            )
        elif category == GuidanceCategory.PARALLELIZE:
            return (
                f"Identified {context.parallel_branches} independent branches. "
                f"Parallelization can accelerate execution."
            )
        elif category == GuidanceCategory.OPTIMIZE_LATENCY:
            return (
                f"Early in execution ({context.current_iteration} / "
                f"{context.max_iterations} iterations) with time budget available. "
                f"Increase concurrency or use faster model."
            )
        elif category == GuidanceCategory.OPTIMIZE_COST:
            return (
                f"Sufficient budget ({context.budget_remaining:.0%}) but low time "
                f"remaining ({context.time_remaining}s). Optimize for speed."
            )
        elif category == GuidanceCategory.REFACTOR:
            return (
                f"High task complexity ({context.complexity_score:.1f} / 1.0). "
                f"Break into simpler, sequential subtasks."
            )
        else:  # FEATURE_GATING
            return "Safe rollout with experiment flag (default behavior)."

    def _extract_metrics(self, context: DecisionContext) -> Dict[str, Any]:
        """Extract supporting metrics from context."""
        return {
            "error_rate": context.error_rate,
            "budget_remaining": context.budget_remaining,
            "tokens_used_ratio": (
                context.tokens_used / context.tokens_available
                if context.tokens_available > 0
                else 0.0
            ),
            "iteration_progress": (
                context.current_iteration / context.max_iterations
                if context.max_iterations > 0
                else 0.0
            ),
            "complexity_score": context.complexity_score,
            "parallel_branches": context.parallel_branches,
            "time_remaining": context.time_remaining,
        }

    def get_stats(self) -> Dict[str, Any]:
        """Get classifier statistics (for monitoring)."""
        return {
            "total_classifications": self._call_count,
            "heuristic_fallbacks": self._fallback_count,
            "llm_success_rate": (
                (self._call_count - self._fallback_count) / self._call_count
                if self._call_count > 0
                else 0.0
            ),
        }
