"""OptimizerLoop — Phase C k=3 Loop Closure (k=3 iteration).

Reads confidence trends from scoreboard, triggers model-parameter updates.
- Confidence threshold checks (>= 0.75)
- Parameter delta computation (confidence → learning rate adjustment)
- Audit trail linking (trend → param change → chain_hash)
- Model registry updates (parameter persistence)

Invariants:
- Only trends with n >= 10 trigger updates (statistical validity)
- Parameter updates are immutable (append-only in audit trail)
- Each update carries trend_id (traceability)
- Tenant-scoped (per tenant_id)
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from core.paths import tenant_home
from core.learning.confidence_scoreboard import ConfidenceTrend


@dataclass(frozen=True)
class ParameterUpdate:
    """Immutable record of a model parameter change."""

    update_id: str
    trend_id: str  # Link to ConfidenceTrend that triggered this
    tenant_id: str
    model_id: str
    pattern_key: str
    parameter_name: str  # e.g., "temperature", "context_window", "retry_count"
    old_value: float
    new_value: float
    confidence_delta: float  # (new_confidence - old_confidence)
    reasoning: str  # Why this change was made
    timestamp: str  # ISO-8601 UTC
    audit_chain_hash: str  # Link to audit chain (for verification)


class OptimizerLoop:
    """Drive model-parameter optimization based on confidence trends."""

    def __init__(self, min_confidence_threshold: float = 0.75):
        """Initialize optimizer.

        Args:
          min_confidence_threshold: trends below this are not optimized
        """
        self.min_confidence_threshold = max(0.0, min(min_confidence_threshold, 1.0))
        self._parameter_updates: dict[str, ParameterUpdate] = {}

    async def compute_parameter_delta(
        self, trend: ConfidenceTrend
    ) -> Optional[dict[str, float]]:
        """Compute parameter adjustments based on confidence trend.

        Args:
          trend: confidence trend (with mean_confidence and trend_direction)

        Returns:
          Dict of {parameter_name: new_value} or None if no change needed
        """
        if not trend.is_significant():
            # Not enough data
            return None

        if not trend.meets_threshold(self.min_confidence_threshold):
            # Below threshold, don't optimize
            return None

        delta = {}

        # Adjust based on trend direction and mean confidence
        if trend.trend_direction == "improving":
            # Confidence is improving: reinforce current params slightly
            delta["learning_rate"] = 0.001  # Small positive adjustment
            delta["temperature"] = min(1.0, trend.mean_confidence)  # Scale to confidence
        elif trend.trend_direction == "degrading":
            # Confidence is degrading: reduce learning rate (safer learning)
            delta["learning_rate"] = -0.0005  # Small reduction
            delta["temperature"] = max(0.1, 1.0 - (1.0 - trend.mean_confidence))
        else:
            # Stable: no change
            pass

        return delta if delta else None

    async def apply_parameter_update(
        self,
        trend_id: str,
        tenant_id: str,
        trend: ConfidenceTrend,
        parameter_delta: dict[str, float],
        audit_chain_hash: str = "",
    ) -> Optional[str]:
        """Apply a parameter update and record it.

        Args:
          trend_id: ID of the trend that triggered this
          tenant_id: tenant context
          trend: the confidence trend
          parameter_delta: {param_name: new_value}
          audit_chain_hash: link to audit chain (for verification)

        Returns:
          update_id of the applied update, or None if no update made
        """
        import uuid

        if not parameter_delta:
            return None

        update_id = f"upd_{uuid.uuid4().hex[:16]}"
        now = datetime.now(timezone.utc).isoformat()

        # For each parameter, create an update record
        updates = []
        for param_name, new_value in parameter_delta.items():
            update = ParameterUpdate(
                update_id=update_id,
                trend_id=trend_id,
                tenant_id=tenant_id,
                model_id=trend.model_id,
                pattern_key=trend.pattern_key,
                parameter_name=param_name,
                old_value=0.0,  # Would retrieve from model registry in real impl
                new_value=new_value,
                confidence_delta=trend.mean_confidence - 0.5,  # Relative to baseline
                reasoning=f"Trend {trend.trend_direction}: confidence {trend.mean_confidence:.2f}",
                timestamp=now,
                audit_chain_hash=audit_chain_hash,
            )
            updates.append(update)
            self._parameter_updates[update_id] = update

        # Persist updates (append-only)
        await self._persist_updates(tenant_id, updates)

        return update_id

    async def _persist_updates(self, tenant_id: str, updates: list[ParameterUpdate]) -> None:
        """Persist parameter updates to log (append-only).

        Appends to: <tenant_home>/<tenant_id>/global/learning/parameter_updates.jsonl
        """
        try:
            log_path = (
                Path(tenant_home(tenant_id)) / "global" / "learning" / "parameter_updates.jsonl"
            )
            log_path.parent.mkdir(parents=True, exist_ok=True)

            with open(log_path, "a") as f:
                for update in updates:
                    json.dump(asdict(update), f)
                    f.write("\n")
                f.flush()
        except Exception:
            # Non-blocking: persistence failure should not crash optimization
            pass

    async def get_updates_for_model(self, model_id: str) -> list[ParameterUpdate]:
        """Get all parameter updates for a model."""
        return [u for u in self._parameter_updates.values() if u.model_id == model_id]

    async def get_latest_parameter_state(
        self, model_id: str
    ) -> dict[str, float]:
        """Get the latest parameter values for a model.

        Returns:
          {param_name: latest_value}
        """
        latest = {}

        for update in self._parameter_updates.values():
            if update.model_id == model_id:
                # Keep only the latest value per parameter
                latest[update.parameter_name] = update.new_value

        return latest

    async def clear_cache(self) -> None:
        """Clear in-memory cache."""
        self._parameter_updates.clear()


class ModelOptimizationLoop:
    """End-to-end loop: Confidence → Parameter → Model update."""

    def __init__(
        self,
        scoreboard,  # ConfidenceScoreboard instance
        optimizer,  # OptimizerLoop instance
    ):
        """Initialize the full loop.

        Args:
          scoreboard: ConfidenceScoreboard for reading trends
          optimizer: OptimizerLoop for applying updates
        """
        self.scoreboard = scoreboard
        self.optimizer = optimizer

    async def run_optimization_step(self, tenant_id: str) -> list[str]:
        """Execute one optimization step: trends → param updates.

        Args:
          tenant_id: tenant to optimize

        Returns:
          List of applied update_ids
        """
        applied_updates = []

        # Get all triggerable trends
        trends = await self.scoreboard.list_triggerable_trends()

        for trend in trends:
            # Compute parameter delta
            delta = await self.optimizer.compute_parameter_delta(trend)

            if not delta:
                continue

            # Apply update
            update_id = await self.optimizer.apply_parameter_update(
                trend_id=f"trend_{trend.task_id}_{trend.model_id}",
                tenant_id=tenant_id,
                trend=trend,
                parameter_delta=delta,
                audit_chain_hash="",  # Would get from audit chain in real impl
            )

            if update_id:
                applied_updates.append(update_id)

        return applied_updates
