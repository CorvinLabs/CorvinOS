"""Example: Integrating ToolCostLearner with CostController (ADR-0326).

This example shows how to wire ToolCostLearner into the CostController
subsystem so that cost estimates improve over time through learning.

The integration pattern:
1. CostController estimates cost for a tool execution
2. Tool executes, TOOL_EXECUTED event records actual cost
3. ToolCostLearner observes (estimated, actual) pair via observe_execution()
4. EMA multiplier updates converge to true cost over time
5. Next estimate uses corrected multiplier via get_cost_estimate()

Feature flag: learning_gap_6_cost_learning (default: false)
"""

from core.learning.tool_cost_learning import ToolCostLearner
from core.console.corvin_core.feature_flags import is_enabled


class CostControllerWithLearning:
    """Example: CostController with ToolCostLearner integration.

    This is a reference implementation showing the integration pattern.
    """

    def __init__(self, tenant_id: str = "_default"):
        """Initialize with optional cost learning.

        Args:
            tenant_id: Tenant for isolation
        """
        self.tenant_id = tenant_id
        self.cost_learner: ToolCostLearner | None = None

        # Check if feature flag is enabled
        if is_enabled("learning_gap_6_cost_learning", tenant_id):
            self.cost_learner = ToolCostLearner(ema_alpha=0.1)
            print(f"Cost learning enabled for tenant {tenant_id}")
        else:
            print(f"Cost learning disabled for tenant {tenant_id}")

    async def estimate_tool_cost(
        self,
        tool_id: str,
        model_id: str,
        base_cost_cents: int,
    ) -> int:
        """Estimate tool cost, using learned multiplier if available.

        Args:
            tool_id: Tool identifier
            model_id: Model used
            base_cost_cents: Base cost from pricing model

        Returns:
            Estimated cost in cents, adjusted by learned multiplier if enabled
        """
        if self.cost_learner:
            # Use learned multiplier
            return self.cost_learner.get_cost_estimate(
                tool_id=tool_id,
                model_id=model_id,
                base_cost_cents=base_cost_cents,
                use_correction=True,
            )
        else:
            # No learning, return base cost
            return base_cost_cents

    async def observe_tool_execution(
        self,
        tool_id: str,
        model_id: str,
        estimated_cost_cents: int,
        actual_cost_cents: int,
    ) -> None:
        """Record actual cost from tool execution for learning.

        Called after TOOL_EXECUTED event is processed.

        Args:
            tool_id: Tool identifier
            model_id: Model used
            estimated_cost_cents: Cost we predicted
            actual_cost_cents: Cost from execution
        """
        if self.cost_learner:
            await self.cost_learner.observe_execution(
                tool_id=tool_id,
                model_id=model_id,
                estimated_cost_cents=estimated_cost_cents,
                actual_cost_cents=actual_cost_cents,
                tenant_id=self.tenant_id,
            )

    async def get_cost_metrics(self):
        """Get aggregated cost learning metrics.

        Returns:
            Dict of (tool_id, model_id) -> CostLearnerMetrics
        """
        if self.cost_learner:
            return await self.cost_learner.aggregate_metrics(
                tenant_id=self.tenant_id
            )
        else:
            return {}


# ─────────────────────────────────────────────────────────────────────────────
# Example usage:

async def example_cost_learning_workflow():
    """Example workflow showing cost learning in action."""

    # Initialize with cost learning
    controller = CostControllerWithLearning(tenant_id="_default")

    # Estimate cost for a tool (first time, no history)
    estimate_1 = await controller.estimate_tool_cost(
        tool_id="tool_1",
        model_id="claude-opus-5",
        base_cost_cents=100,
    )
    print(f"Initial estimate: {estimate_1} cents (no history)")

    # Simulate tool executions with overhead
    # True multiplier is 1.5 (tool always costs 1.5x estimated)
    true_multiplier = 1.5

    for i in range(30):
        # Estimate cost
        estimate = await controller.estimate_tool_cost(
            tool_id="tool_1",
            model_id="claude-opus-5",
            base_cost_cents=100,
        )

        # Execute tool, get actual cost
        actual_cost = int(100 * true_multiplier)

        # Record observation for learning
        await controller.observe_tool_execution(
            tool_id="tool_1",
            model_id="claude-opus-5",
            estimated_cost_cents=estimate,
            actual_cost_cents=actual_cost,
        )

        if i % 10 == 0:
            print(f"Iteration {i}: estimate={estimate}, actual={actual_cost}")

    # Get learned metrics
    metrics = await controller.get_cost_metrics()
    for key, m in metrics.items():
        tool_id, model_id = key
        print(
            f"\n{tool_id}/{model_id}:"
            f"\n  Samples: {m.samples}"
            f"\n  Learned multiplier: {m.subsystem_overhead_multiplier:.3f}"
            f"\n  Confidence: {m.confidence:.2%}"
            f"\n  Trend: {m.trend:+.1f}"
        )

    # Final estimate should now be close to true value
    final_estimate = await controller.estimate_tool_cost(
        tool_id="tool_1",
        model_id="claude-opus-5",
        base_cost_cents=100,
    )
    expected_estimate = int(100 * true_multiplier)
    print(f"\nFinal estimate: {final_estimate} cents (expected ~{expected_estimate})")
    print(f"Accuracy: {abs(final_estimate - expected_estimate)} cents off")


# ─────────────────────────────────────────────────────────────────────────────
# Integration points with existing systems:

"""
1. TOOL_EXECUTED Event Observation:
   When a TOOL_EXECUTED event is recorded by the learning event emitter,
   extract the (estimated_cost_cents, actual_cost_cents) pair and call:

   await cost_learner.observe_execution(
       tool_id=event.payload['tool_id'],
       model_id=event.payload['model_id'],
       estimated_cost_cents=event.payload['estimated_cost_cents'],
       actual_cost_cents=actual_cost_from_event,
   )

2. Cost Estimation in Tool Selection (Gap 2):
   When ranking tools for reuse (Gap 2, ADR-0322), use the corrected
   cost estimate instead of base pricing:

   corrected_cost = cost_learner.get_cost_estimate(
       tool_id=tool.tool_id,
       model_id=model_id,
       base_cost_cents=base_cost,
   )

3. Budget Forecasting:
   When forecasting budget consumption over time, aggregate learned
   multipliers per tool/model and project future costs:

   metrics = await cost_learner.aggregate_metrics()
   for key, m in metrics.items():
       tool_id, model_id = key
       # Use m.subsystem_overhead_multiplier for projection
       # Use m.confidence to weight older vs newer predictions

4. Model Pricing Updates:
   When model pricing changes, reset multipliers so learning restarts:

   cost_learner.reset_multiplier(tool_id, old_model_id)
   # or for all tools of a model:
   for tool_id in get_all_tools():
       cost_learner.reset_multiplier(tool_id, old_model_id)

5. Per-Tenant Isolation:
   Each tenant gets its own ToolCostLearner instance; pass tenant_id
   to all methods for audit trail and GDPR Art. 5, 32 compliance:

   learner = ToolCostLearner()
   await learner.observe_execution(..., tenant_id=tenant_id)
"""
