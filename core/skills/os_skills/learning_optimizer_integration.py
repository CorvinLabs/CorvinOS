"""Phase 8 Integration: Learning Optimizer uses real metrics (Phase 7 → Phase 8)

Wires Phase 7 (Learning Loop) with Phase 8 (Observability) so optimizer
reads live metrics to assess learned parameters.
"""

from core.telemetry.metrics_collector import get_collector, MetricsQuery


class LearningOptimizerMetricsIntegration:
    """Phase 7 + Phase 8 integration: learning uses real metrics."""

    @staticmethod
    def get_skill_metrics_for_optimizer(
        skill_id: str,
        tenant_id: str,
        range_hours: int = 1,
    ) -> dict:
        """Get real metrics for skill to inform learning optimizer.

        Phase 7 Optimizer uses these to assess: "Did my learned parameters
        actually reduce latency / error rate / improve convergence?"

        Returns:
            {
                'latency_ms': float (p50),
                'error_rate': float [0.0, 1.0],
                'convergence': float [0.0, 1.0],
                'throughput': int (exec/min),
                'cost': float ($),
            }
        """
        collector = get_collector()
        query = MetricsQuery(
            tenant_id=tenant_id,
            range_hours=range_hours,
            skill_ids=[skill_id],
        )

        metrics = collector.query_metrics(query)

        if not metrics:
            # No metrics yet - return neutral baseline
            return {
                'latency_ms': 100.0,
                'error_rate': 0.0,
                'convergence': 0.5,
                'throughput': 0,
                'cost': 0.0,
            }

        m = metrics[0]
        return {
            'latency_ms': m.latency_ms,
            'error_rate': m.error_rate,
            'convergence': m.convergence_score,
            'throughput': m.throughput_per_min,
            'cost': m.model_cost,
        }

    @staticmethod
    def optimizer_should_adjust(
        current_metrics: dict,
        previous_metrics: dict,
    ) -> bool:
        """Decide if learning optimizer should adjust parameters.

        Phase 8 signals Phase 7: "Metrics are degrading" or "Metrics improved!"
        """
        # If latency increased >20% or error rate increased >10%, adjust
        if current_metrics['latency_ms'] > previous_metrics['latency_ms'] * 1.2:
            return True

        if current_metrics['error_rate'] > previous_metrics['error_rate'] * 1.1:
            return True

        # If convergence dropped significantly, adjust
        if current_metrics['convergence'] < previous_metrics['convergence'] * 0.9:
            return True

        return False


# Integration point: Phase 7 calls this before next optimization round
def integrate_phase8_metrics_into_optimizer():
    """Inject Phase 8 (Observability) into Phase 7 (Learning)."""
    return LearningOptimizerMetricsIntegration
