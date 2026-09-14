"""Weight Convergence Detection — Detect when learning weights stabilize"""

import statistics


class WeightConvergence:
    """Detect convergence of skill confidence scores"""

    def __init__(self, event_store):
        self.event_store = event_store

    async def detect_convergence(
        self, skill_id: str, tenant_id: str, confidence_threshold: float = 0.80
    ) -> bool:
        """
        Detect if skill has converged (mean confidence >= threshold).

        Returns True if last 50+ events show confidence >= threshold.
        """
        events = await self.event_store.query_events(
            tenant_id=tenant_id, skill_id=skill_id, limit=50
        )

        if len(events) < 10:
            return False  # Not enough data

        # Extract confidence scores from outcome events
        confidences = [
            e.signal
            for e in events
            if e.event_type == "outcome" and e.signal is not None
        ]

        if not confidences:
            return False

        mean_confidence = statistics.mean(confidences)
        return mean_confidence >= confidence_threshold

    async def get_convergence_stats(
        self, skill_id: str, tenant_id: str
    ) -> dict:
        """Get detailed convergence stats for dashboard"""
        events = await self.event_store.query_events(
            tenant_id=tenant_id, skill_id=skill_id, limit=50
        )

        confidences = [
            e.signal
            for e in events
            if e.event_type == "outcome" and e.signal is not None
        ]

        if not confidences:
            return {
                "skill_id": skill_id,
                "n_events": 0,
                "mean_confidence": 0.0,
                "is_converged": False,
            }

        return {
            "skill_id": skill_id,
            "n_events": len(events),
            "mean_confidence": statistics.mean(confidences),
            "std_dev": statistics.stdev(confidences) if len(confidences) > 1 else 0.0,
            "is_converged": statistics.mean(confidences) >= 0.80,
        }
