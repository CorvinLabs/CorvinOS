"""What-If Replay Engine (Phase 3, Week 14).

Deterministic replay of past decisions with counterfactual analysis.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional, Dict, Any
from datetime import datetime


@dataclass(frozen=True)
class ExecutionSnapshot:
    """Snapshot of a task execution for replay."""

    task_id: str
    task_type: str
    input_prompt: str
    engine_chosen: str
    outcome_quality: float
    outcome_cost_cents: int
    outcome_latency_ms: int
    timestamp: str

    def to_json(self) -> str:
        """Serialize to JSON."""
        return json.dumps({
            "task_id": self.task_id,
            "task_type": self.task_type,
            "input_prompt": self.input_prompt,
            "engine_chosen": self.engine_chosen,
            "outcome_quality": self.outcome_quality,
            "outcome_cost_cents": self.outcome_cost_cents,
            "outcome_latency_ms": self.outcome_latency_ms,
            "timestamp": self.timestamp,
        })


class ReplayEngine:
    """Replays past tasks with alternative engines."""

    def __init__(self):
        self.snapshots: Dict[str, ExecutionSnapshot] = {}
        # Engine cost/quality profiles
        self.engine_profiles = {
            "haiku": {"quality": 0.92, "cost": 85},
            "hermes": {"quality": 0.95, "cost": 100},
            "claude": {"quality": 0.98, "cost": 3150},
            "local": {"quality": 0.85, "cost": 0},
        }

    def record_snapshot(self, snapshot: ExecutionSnapshot) -> None:
        """Record a task execution."""
        self.snapshots[snapshot.task_id] = snapshot

    def simulate_alternative_engine(
        self,
        task_id: str,
        alternative_engine: str,
    ) -> Optional[Dict[str, Any]]:
        """Simulate what would happen with alternative engine.

        Returns: {quality, cost, latency, improvement_percent}
        """
        if task_id not in self.snapshots:
            return None

        original = self.snapshots[task_id]
        profile = self.engine_profiles.get(alternative_engine)
        if not profile:
            return None

        # Estimate outcome with alternative engine
        est_quality = profile["quality"]
        est_cost = profile["cost"]  # Simplified: assume same token count

        # Compare to original
        quality_improvement = (est_quality - original.outcome_quality) * 100
        cost_delta = est_cost - original.outcome_cost_cents

        return {
            "original_engine": original.engine_chosen,
            "alternative_engine": alternative_engine,
            "original_quality": original.outcome_quality,
            "alt_quality": est_quality,
            "quality_improvement_percent": quality_improvement,
            "original_cost_cents": original.outcome_cost_cents,
            "alt_cost_cents": est_cost,
            "cost_delta_cents": cost_delta,
            "recommendation": self._recommend(quality_improvement, cost_delta),
        }

    def _recommend(self, quality_improvement: float, cost_delta: int) -> str:
        """Recommend based on what-if analysis."""
        if quality_improvement > 5 and cost_delta > 100:
            return "Higher quality but more expensive"
        elif quality_improvement > 5 and cost_delta <= 0:
            return "Better quality and cheaper - should use this"
        elif quality_improvement <= 0 and cost_delta < 0:
            return "Cheaper with similar quality - consider this"
        else:
            return "Current choice is good"

    def get_counterfactuals(self, task_id: str) -> Dict[str, Any]:
        """Show all what-if scenarios for a task."""
        if task_id not in self.snapshots:
            return {}

        original = self.snapshots[task_id]
        counterfactuals = {}

        for engine_name in self.engine_profiles:
            if engine_name != original.engine_chosen:
                counterfactuals[engine_name] = self.simulate_alternative_engine(
                    task_id, engine_name
                )

        return counterfactuals

    def verify_determinism(self, task_id: str, replayed_quality: float) -> bool:
        """Verify that replay produces same quality (determinism proof)."""
        if task_id not in self.snapshots:
            return False

        original = self.snapshots[task_id]
        # Allow 1% variance for floating point
        return abs(replayed_quality - original.outcome_quality) < 0.01
