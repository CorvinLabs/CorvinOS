"""Routing Decision Engine (Phase 2, Week 8).

Cost/capability-based engine selection algorithm.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from core.engines.engine_interface import EngineType
from core.orchestration.cost_capability_matrix import CostCapabilityMatrix


@dataclass
class RoutingContext:
    """Context for routing decision."""

    task_id: str
    task_type: str
    tokens_estimated: int
    operator_id: str
    quality_threshold: float = 0.8  # Minimum quality needed
    cost_budget_cents: int = 1000000  # Daily budget in cents
    deadline_ms: Optional[int] = None  # Urgency indicator
    prefer_fast: bool = False
    prefer_cheap: bool = False


@dataclass
class RoutingDecision:
    """Decision to use specific engine."""

    task_id: str
    engine_type: EngineType
    confidence: float  # How confident in this decision (0-1)
    reason: str  # Why this engine was chosen
    alternative_engine: Optional[EngineType] = None
    estimated_cost_cents: int = 0
    estimated_quality: float = 0.0


class RoutingDecisionEngine:
    """Makes routing decisions based on cost/capability."""

    def __init__(self):
        self.matrix = CostCapabilityMatrix()

    def decide(self, context: RoutingContext) -> RoutingDecision:
        """Decide which engine to use for a task.

        Algorithm:
        1. Get all engines that can handle task_type
        2. Filter by quality threshold
        3. Score each engine: (quality / threshold) × (budget / cost) × urgency_factor
        4. Select highest score
        5. Return decision with confidence
        """
        # Step 1: Get candidate engines
        candidates = self.matrix.get_engines_for_task(context.task_type)

        if not candidates:
            # Fallback to cheapest engine
            candidates = [EngineType.HAIKU]

        # Step 2: Filter by quality threshold
        viable_engines = []
        for engine in candidates:
            quality = self.matrix.estimate_quality(engine, context.task_type)
            if quality >= context.quality_threshold:
                viable_engines.append(engine)

        # If none meet threshold, use best quality available
        if not viable_engines:
            viable_engines = sorted(
                candidates,
                key=lambda e: self.matrix.estimate_quality(e, context.task_type),
                reverse=True,
            )[:1]

        # Step 3: Score engines
        best_engine = None
        best_score = -1
        scores = {}

        for engine in viable_engines:
            quality = self.matrix.estimate_quality(engine, context.task_type)
            cost = self.matrix.estimate_cost(engine, context.task_type, context.tokens_estimated)
            latency = self.matrix.estimate_latency(engine, context.task_type)

            # Quality factor (normalized against threshold)
            quality_factor = quality / max(context.quality_threshold, 0.1)

            # Cost factor (lower cost = higher factor)
            cost_factor = max(context.cost_budget_cents - cost, 0) / max(context.cost_budget_cents, 1)

            # Urgency factor (if deadline, prefer faster engines)
            if context.deadline_ms:
                # Urgent tasks prefer fast engines
                urgency_factor = max(context.deadline_ms - latency, 0) / max(context.deadline_ms, 1)
            else:
                urgency_factor = 1.0

            # Weighted score
            # Prefer: high quality (40%), low cost (35%), speed if urgent (25%)
            score = (
                0.40 * quality_factor +
                0.35 * cost_factor +
                0.25 * urgency_factor
            )

            scores[engine] = score

            # Adjust for operator preferences
            if context.prefer_cheap and cost_factor > 0.5:
                score *= 1.2
            if context.prefer_fast and urgency_factor > 0.5:
                score *= 1.2

            if score > best_score:
                best_score = score
                best_engine = engine

        # Step 5: Build decision
        if best_engine:
            quality = self.matrix.estimate_quality(best_engine, context.task_type)
            cost = self.matrix.estimate_cost(best_engine, context.task_type, context.tokens_estimated)

            # Determine reason
            if context.prefer_fast:
                reason = f"Fast execution (p99 {self.matrix.estimate_latency(best_engine, context.task_type)}ms)"
            elif context.prefer_cheap:
                reason = f"Cost-effective (${cost/100:.2f})"
            else:
                reason = f"Optimal balance (quality {quality:.2f}, cost ${cost/100:.2f})"

            # Get alternative (next best option)
            alternative = None
            for engine in viable_engines:
                if engine != best_engine:
                    alternative = engine
                    break

            return RoutingDecision(
                task_id=context.task_id,
                engine_type=best_engine,
                confidence=min(1.0, best_score / 2.0),  # Normalize to 0-1
                reason=reason,
                alternative_engine=alternative,
                estimated_cost_cents=cost,
                estimated_quality=quality,
            )
        else:
            # Fallback to Haiku
            return RoutingDecision(
                task_id=context.task_id,
                engine_type=EngineType.HAIKU,
                confidence=0.5,
                reason="Default fallback (no qualified engines)",
                estimated_cost_cents=self.matrix.estimate_cost(EngineType.HAIKU, context.task_type, context.tokens_estimated),
                estimated_quality=self.matrix.estimate_quality(EngineType.HAIKU, context.task_type),
            )

    def explain_decision(self, context: RoutingContext) -> dict:
        """Explain routing decision in detail."""
        decision = self.decide(context)

        # Score all engines
        candidates = self.matrix.get_engines_for_task(context.task_type)
        all_scores = {}

        for engine in candidates:
            quality = self.matrix.estimate_quality(engine, context.task_type)
            cost = self.matrix.estimate_cost(engine, context.task_type, context.tokens_estimated)
            latency = self.matrix.estimate_latency(engine, context.task_type)

            all_scores[engine.value] = {
                "quality": quality,
                "cost_cents": cost,
                "latency_ms": latency,
            }

        return {
            "task_id": context.task_id,
            "selected_engine": decision.engine_type.value,
            "confidence": decision.confidence,
            "reason": decision.reason,
            "alternative": decision.alternative_engine.value if decision.alternative_engine else None,
            "all_scores": all_scores,
        }
