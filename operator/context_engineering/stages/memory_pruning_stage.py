"""Memory Pruning Stage (Phase 5, ADR-0394).

Non-destructively removes low-confidence and expired memories from the context.
Memories are kept in the audit trail but not rendered in the prompt.

Stage: memory_pruning
Requires: memory (to have memory_context with matches)
Effect: pure (read-only, transforms brief data)
"""
from __future__ import annotations

import logging
from datetime import datetime
from .base import StageTelemetry
from .registry import register_stage
from ..memory_pruning import MemoryPruner

logger = logging.getLogger(__name__)


class MemoryPruningStage:
    """Prune memories by confidence, age, and quota."""

    id = "memory_pruning"
    requires = ("memory",)  # Must run after memory to have matches
    effect = "pure"
    trust = "builtin"

    def run(self, bundle, ctx):
        """Prune brief memory_context.matches by confidence, age, and quota.

        Non-destructive: pruned memories are not rendered but remain in audit trail.
        """
        tel = StageTelemetry(stage=self.id, status="ok")

        try:
            # Get config
            config = getattr(ctx, "config", {}) or {}
            confidence_floor = float(config.get("confidence_floor", 0.3))
            max_age_days = int(config.get("max_age_days", 30))
            per_tenant_quota = int(config.get("per_tenant_quota", 5))
            enabled = config.get("enabled", True)

            if not enabled:
                tel.status = "skipped"
                tel.reason = "disabled_by_config"
                return bundle, tel

            # Get memory matches from brief
            brief = getattr(bundle, "brief", None)
            if brief is None:
                tel.status = "skipped"
                tel.reason = "no_brief"
                return bundle, tel

            mc = getattr(brief, "memory_context", None)
            if mc is None:
                tel.status = "skipped"
                tel.reason = "no_memory_context"
                return bundle, tel

            matches = getattr(mc, "matches", None)
            if not matches:
                tel.status = "ok"
                tel.reason = "no_matches_to_prune"
                return bundle, tel

            # Prune
            pruner = MemoryPruner(
                confidence_floor=confidence_floor,
                max_age_days=max_age_days,
                per_tenant_quota=per_tenant_quota,
            )
            tenant_id = getattr(ctx, "tenant_id", "_default")
            pruned_matches, prune_tel = pruner.prune(matches, tenant_id)

            # Update brief with pruned matches
            mc.matches = pruned_matches

            # Record telemetry
            tel.status = "ok"
            tel.sources = [
                {"id": "dropped_count", "score": prune_tel.get("dropped_count", 0)},
                {"id": "confidence_floor", "score": confidence_floor},
                {"id": "max_age_days", "score": max_age_days},
            ]

            logger.info(
                f"MemoryPruningStage (tenant={tenant_id}): "
                f"{prune_tel.get('memories_before', 0)} → {prune_tel.get('memories_after', 0)} "
                f"(dropped={prune_tel.get('dropped_count', 0)})"
            )

        except Exception as e:
            tel.status = "failed"
            tel.error = str(e)[:120]
            logger.exception(f"MemoryPruningStage failed: {e}")

        return bundle, tel


register_stage(MemoryPruningStage())
