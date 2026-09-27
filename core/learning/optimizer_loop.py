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
- Tenant-scoped (per tenant_id): an optimisation step reads only that
  tenant's trends
- Audit-FIRST: every recorded update is committed to the tenant's core audit
  chain (``event_persistence.core_audit_event``) BEFORE it is kept or
  persisted; no chain commit -> no update (RuntimeError)

Nothing reads the recorded parameters back into a model: this module RECORDS
proposed parameter values, it does not apply them anywhere. ``old_value`` is
``None`` ("not measured") because there is no parameter registry to read it
from — it used to be a fabricated 0.0.

NOT WIRED: no production caller as of 2026-09-27 (adversarial review).
"""

from __future__ import annotations

import json
import logging
from collections import OrderedDict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from core.paths import tenant_home
from core.learning.confidence_scoreboard import ConfidenceTrend

logger = logging.getLogger(__name__)

#: Chain event for one recorded parameter update (content-free; allowlisted in
#: ``event_persistence._LEARNING_EVENT_ALLOWLISTS``).
PARAMETER_UPDATE_EVENT = "learning.parameter_update_recorded"
#: In-process cap on remembered update groups (oldest evicted first).
MAX_TRACKED_UPDATES = 10_000


@dataclass(frozen=True)
class ParameterUpdate:
    """Immutable record of a model parameter change."""

    update_id: str
    trend_id: str  # Link to ConfidenceTrend that triggered this
    tenant_id: str
    model_id: str
    pattern_key: str
    parameter_name: str  # e.g., "temperature", "context_window", "retry_count"
    old_value: Optional[float]  # None = not measured (no parameter registry exists)
    new_value: float
    confidence_delta: float  # (new_confidence - old_confidence)
    reasoning: str  # Why this change was made
    timestamp: str  # ISO-8601 UTC
    audit_chain_hash: str  # Link to the audit window that produced the trend
    audit_ref: str = ""  # The core-chain record of THIS update (audit-first)


class OptimizerLoop:
    """Drive model-parameter optimization based on confidence trends."""

    def __init__(self, min_confidence_threshold: float = 0.75):
        """Initialize optimizer.

        Args:
          min_confidence_threshold: trends below this are not optimized
        """
        self.min_confidence_threshold = max(0.0, min(min_confidence_threshold, 1.0))
        # update_id -> one ParameterUpdate per parameter of that update. It was
        # a single slot per update_id, so a two-parameter update kept only the
        # last parameter and the first was silently lost.
        self._parameter_updates: "OrderedDict[str, list[ParameterUpdate]]" = OrderedDict()

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

        from core.learning.event_persistence import core_audit_event  # noqa: PLC0415

        if not parameter_delta:
            return None

        update_id = f"upd_{uuid.uuid4().hex[:16]}"
        now = datetime.now(timezone.utc).isoformat()

        # Audit FIRST: raises RuntimeError when the record does not commit, and
        # then nothing below runs (no in-memory record, no persisted line).
        audit_ref = core_audit_event(
            PARAMETER_UPDATE_EVENT,
            tenant_id=tenant_id,
            details={
                "update_id": update_id,
                "trend_id": str(trend_id)[:128],
                "model_id": str(trend.model_id)[:128],
                "pattern_key": str(trend.pattern_key)[:128],
                "parameter_names": ",".join(sorted(parameter_delta))[:512],
                "update_count": len(parameter_delta),
                "tenant_id": tenant_id,
            },
        )

        updates = []
        for param_name, new_value in parameter_delta.items():
            updates.append(ParameterUpdate(
                update_id=update_id,
                trend_id=trend_id,
                tenant_id=tenant_id,
                model_id=trend.model_id,
                pattern_key=trend.pattern_key,
                parameter_name=param_name,
                old_value=None,
                new_value=new_value,
                confidence_delta=trend.mean_confidence - 0.5,  # Relative to baseline
                reasoning=f"Trend {trend.trend_direction}: confidence {trend.mean_confidence:.2f}",
                timestamp=now,
                audit_chain_hash=audit_chain_hash,
                audit_ref=audit_ref,
            ))
        self._parameter_updates[update_id] = updates
        while len(self._parameter_updates) > MAX_TRACKED_UPDATES:
            self._parameter_updates.popitem(last=False)

        # Persist updates (append-only). The chain record already stands; a
        # disk failure is reported, not swallowed.
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
        except OSError as exc:
            raise IOError(
                f"parameter update {updates[0].update_id if updates else ''} is on the audit "
                f"chain but could not be persisted: {type(exc).__name__}"
            ) from exc

    async def get_updates_for_model(self, model_id: str, *, tenant_id: str) -> list[ParameterUpdate]:
        """Get all remembered parameter updates for a model of ONE tenant."""
        return [u for group in self._parameter_updates.values() for u in group
                if u.model_id == model_id and u.tenant_id == tenant_id]

    async def get_latest_parameter_state(
        self, model_id: str, *, tenant_id: str
    ) -> dict[str, float]:
        """Get the latest recorded parameter values for a model of ONE tenant.

        Returns:
          {param_name: latest_value}
        """
        latest = {}
        for group in self._parameter_updates.values():  # insertion order = time order
            for update in group:
                if update.model_id == model_id and update.tenant_id == tenant_id:
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

        # Only THIS tenant's trends (previously every tenant's trends were
        # applied under the caller's tenant id).
        trends = await self.scoreboard.list_triggerable_trends(tenant_id=tenant_id)

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
