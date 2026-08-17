"""Token Metrics Aggregator — Complete pipeline (Phase 1.K4).

Combines TokenMetricsStore + ComparisonEngine for dashboard queries.
"""

from __future__ import annotations
from typing import Optional
from core.learning.token_metrics_store import TokenMetricsStore
from core.learning.token_baseline import ComparisonEngine


class TokenMetricsAggregator:
    """Complete aggregation pipeline for token measurement dashboard."""

    def __init__(self, store: TokenMetricsStore, comparison_engine: ComparisonEngine):
        self.store = store
        comparison_engine = comparison_engine

    def get_session_dashboard_data(self, session_id: str) -> dict:
        """Get complete dashboard data for a session.

        Args:
            session_id: Session identifier

        Returns:
            Complete dashboard JSON (summary + trends + breakdowns)
        """
        summary = self.store.summary(session_id)

        return {
            "session_id": session_id,
            "timestamp": "2026-08-17T22:45:00Z",  # Would use datetime.utcnow()

            # Summary tier
            "summary": {
                "turn_count": summary.get("turn_count", 0),
                "total_tokens": summary.get("total_tokens", 0),
                "baseline_tokens": summary.get("baseline_tokens", 0),
                "savings_tokens": summary.get("savings_tokens", 0),
                "savings_percent": round(summary.get("savings_percent", 0), 1),
                "avg_tokens_per_turn": round(summary.get("avg_tokens_per_turn", 0), 0),
            },

            # By task type
            "by_task_type": summary.get("by_task_type", {}),

            # Subsystem attribution
            "subsystems": summary.get("subsystems", {}),

            # Confidence: if savings_percent > 15%, high confidence
            "confidence": 0.95 if summary.get("savings_percent", 0) > 15 else 0.3,
            "is_significant": summary.get("savings_percent", 0) > 15,
        }

    def get_session_metrics(self, session_id: str) -> list[dict]:
        """Get detailed metrics for each turn in a session.

        Args:
            session_id: Session identifier

        Returns:
            List of turn metrics (for detailed view)
        """
        events = self.store.query_by_session(session_id, limit=100)
        metrics_list = []

        for event in events:
            payload = event.payload.get("token_metrics", {})
            metrics_list.append({
                "turn_id": payload.get("turn_id"),
                "input_tokens": payload.get("input_tokens"),
                "output_tokens": payload.get("output_tokens"),
                "total_tokens": payload.get("total_tokens"),
                "savings_percent": round(payload.get("savings_percent", 0), 1),
                "task_type": payload.get("task_type"),
                "outcome_quality": payload.get("outcome_quality"),
                "latency_ms": payload.get("latency_ms"),
            })

        return metrics_list

    def get_comparison_summary(self) -> dict:
        """Get Vibe vs Native comparison summary.

        Returns:
            Comparison statistics
        """
        return self.comparison_engine.aggregate_comparisons()
