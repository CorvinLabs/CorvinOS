"""
ADR-0377: Cost Optimizer — estimates complexity and routes to optimal models.
"""

from typing import Dict, Optional, Any, Callable
from dataclasses import dataclass
from datetime import datetime
import logging

from core.orchestration.model_routing import (
    ModelRoutingPlan,
    SubsystemCostEvent,
    TaskTemplate,
    ExecutionContext,
    OperatorStyle,
    ModelChoice,
    hash_routing_plan,
)


_log = logging.getLogger(__name__)


@dataclass
class CostEstimate:
    """Cost estimate for a subsystem with a specific model."""
    subsystem: str
    model: str
    estimated_cost: float
    confidence: float


class CostOptimizer:
    """Computes per-subsystem model routing based on task complexity + operator style."""

    def __init__(
        self,
        template_provider: Callable[[str], Optional[TaskTemplate]],
        operator_style_provider: Callable[[str], Optional[OperatorStyle]],
    ):
        """
        Args:
            template_provider: Function to fetch TaskTemplate by task_type
            operator_style_provider: Function to fetch OperatorStyle by operator_id
        """
        self.template_provider = template_provider
        self.operator_style_provider = operator_style_provider

    def compute_routing_plan(
        self,
        task_id: str,
        task_type: str,
        context: ExecutionContext,
    ) -> ModelRoutingPlan:
        """
        Compute per-subsystem model routing.

        Returns a ModelRoutingPlan with subsystem → model assignments.
        """
        template = self.template_provider(task_type)
        operator_style = self.operator_style_provider(context.operator_id)

        # Estimate task complexity (0.0 to 1.0)
        complexity = self._estimate_complexity(context, template)

        # Determine complexity threshold (operator style affects this)
        complexity_threshold = 0.5  # Default
        if operator_style:
            complexity_threshold = operator_style.get_complexity_threshold(default=0.5)
            # Clamp to [0.1, 0.9]
            complexity_threshold = max(0.1, min(0.9, complexity_threshold))

        # Assign models per subsystem
        routes = {}
        reasoning = {}

        # Define subsystems (expandable)
        subsystems = ["code_analyzer", "refactoring_engine", "test_generator"]

        for subsystem in subsystems:
            sub_complexity = self._estimate_subsystem_complexity(
                subsystem, context, template
            )

            # Route based on complexity vs. threshold
            if sub_complexity < complexity_threshold:
                model = ModelChoice.HAIKU
            elif sub_complexity < (complexity_threshold + 0.25):
                model = ModelChoice.SONNET  # Middle ground
            else:
                model = ModelChoice.OPUS

            routes[subsystem] = model

            # Store reasoning
            reasoning[subsystem] = {
                "complexity": sub_complexity,
                "threshold": complexity_threshold,
                "model": model,
            }

        # Compute total estimated cost
        total_cost = 0.0
        for subsystem, model in routes.items():
            estimate = self._estimate_subsystem_cost(subsystem, model, template)
            total_cost += estimate.estimated_cost

        # Estimate overall confidence
        confidence = 0.7  # Base confidence
        if template and template.sample_count >= 20:
            confidence = 0.85  # Higher confidence with good historical data
        elif not template:
            confidence = 0.5  # Lower confidence without template

        return ModelRoutingPlan(
            task_id=task_id,
            task_type=task_type,
            subsystem_routes=routes,
            estimated_total_cost=total_cost,
            confidence=confidence,
            reasoning=reasoning,
        )

    def _estimate_complexity(
        self,
        context: ExecutionContext,
        template: Optional[TaskTemplate],
    ) -> float:
        """
        Estimate overall task complexity (0.0 to 1.0).

        Factors:
        - Context size (tokens)
        - Template historical duration
        """
        # Context size heuristic
        context_dict = context.to_dict()
        context_size = sum(len(str(v)) for v in context_dict.values())
        complexity_from_size = min(1.0, context_size / 5000)  # 5000 chars = 1.0

        # Template heuristic
        complexity_from_template = 0.5  # Default
        if template:
            # Longer tasks are typically more complex
            max_duration = 120  # 2 hours max
            complexity_from_template = min(
                1.0, template.duration_mean_minutes / max_duration
            )

        # Blend: 60% context, 40% template
        return (complexity_from_size * 0.6) + (complexity_from_template * 0.4)

    def _estimate_subsystem_complexity(
        self,
        subsystem: str,
        context: ExecutionContext,
        template: Optional[TaskTemplate],
    ) -> float:
        """Estimate complexity for a specific subsystem."""

        if subsystem == "code_analyzer":
            # Complexity based on code size
            code_lines = len(context.code.split('\n')) if context.code else 0
            return min(1.0, code_lines / 500)

        elif subsystem == "refactoring_engine":
            # Complexity based on scope
            return context.refactoring_scope or 0.5

        elif subsystem == "test_generator":
            # Complexity based on coverage target
            if context.coverage_target:
                return min(1.0, context.coverage_target / 100.0)
            return 0.5

        else:
            # Unknown subsystem, default to 0.5
            return 0.5

    def _estimate_subsystem_cost(
        self,
        subsystem: str,
        model: str,
        template: Optional[TaskTemplate] = None,
    ) -> CostEstimate:
        """Estimate cost for a subsystem with a specific model."""

        # Default costs (in USD)
        model_costs = {
            ModelChoice.HAIKU: 0.20,
            ModelChoice.SONNET: 0.40,
            ModelChoice.OPUS: 0.70,
        }

        base_cost = model_costs.get(model, 0.50)

        # Template overrides
        if template:
            subsystem_config = template.get_subsystem_config(subsystem)
            if model == ModelChoice.HAIKU:
                base_cost = subsystem_config.get("model_haiku_cost", base_cost)
            elif model == ModelChoice.OPUS:
                base_cost = subsystem_config.get("model_opus_cost", base_cost)

        # Subsystem multipliers (some are more expensive)
        subsystem_multipliers = {
            "code_analyzer": 1.0,
            "refactoring_engine": 1.2,
            "test_generator": 1.5,
        }

        multiplier = subsystem_multipliers.get(subsystem, 1.0)
        final_cost = base_cost * multiplier

        return CostEstimate(
            subsystem=subsystem,
            model=model,
            estimated_cost=final_cost,
            confidence=0.75,
        )


class CostTracker:
    """Tracks actual vs. estimated costs and emits audit events."""

    def __init__(self, event_store):
        """
        Args:
            event_store: Must have a write_event(event) method for audit trail
        """
        self.event_store = event_store

    def track_subsystem_cost(
        self,
        task_id: str,
        subsystem: str,
        model_used: str,
        actual_cost: float,
        routing_plan: ModelRoutingPlan,
        success: bool = True,
        error_msg: Optional[str] = None,
    ) -> SubsystemCostEvent:
        """
        Track actual cost for a subsystem execution.

        Returns a SubsystemCostEvent (immutable audit record).
        """
        estimated_cost = self._get_subsystem_estimate(subsystem, model_used, routing_plan)

        event = SubsystemCostEvent(
            task_id=task_id,
            subsystem=subsystem,
            model_used=model_used,
            estimated_cost=estimated_cost,
            actual_cost=actual_cost,
            variance=actual_cost - estimated_cost,
            success=success,
            error_msg=error_msg,
            routing_plan_hash=hash_routing_plan(routing_plan),
        )

        # Write to audit trail
        try:
            self.event_store.write_event(event.to_dict())
        except Exception as e:
            _log.error(f"Failed to write cost event to audit trail: {e}")

        # Alert on high variance
        if not success or (abs(event.variance) > estimated_cost * 0.3):
            self._alert_cost_estimate_error(event)

        return event

    def _get_subsystem_estimate(
        self,
        subsystem: str,
        model: str,
        routing_plan: ModelRoutingPlan,
    ) -> float:
        """Extract estimated cost for a subsystem from routing plan."""
        # This is a simplified lookup; in practice, you'd recompute or cache
        if subsystem in routing_plan.subsystem_routes:
            # Rough estimate based on model
            model_costs = {
                ModelChoice.HAIKU: 0.20,
                ModelChoice.SONNET: 0.40,
                ModelChoice.OPUS: 0.70,
            }
            return model_costs.get(model, 0.50)
        return 0.0

    def _alert_cost_estimate_error(self, event: SubsystemCostEvent):
        """Alert when actual cost deviates from estimate."""
        _log.warning(
            f"Cost estimate error for task {event.task_id}/{event.subsystem}: "
            f"estimated ${event.estimated_cost:.2f}, actual ${event.actual_cost:.2f} "
            f"({event.variance_pct():.1f}% variance)"
        )
