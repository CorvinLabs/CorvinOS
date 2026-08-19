"""Memory Pruning (Phase 5, ADR-0394).

Non-destructively removes low-confidence and expired memories from the context.
Memories are kept in the audit trail but not rendered in the prompt.

Rules:
- Drop memories with confidence < 0.3 (strict quality threshold)
- Drop memories older than 30 days (retention policy)
- Keep at most 5 memories per tenant (per-user quota)
- Sort by confidence (highest first)

Expected savings: 5-10% context reduction.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Any, List, Optional, Tuple

logger = logging.getLogger(__name__)


class MemoryPruner:
    """Non-destructively remove memories below quality/age/quota thresholds."""

    def __init__(
        self,
        confidence_floor: float = 0.3,
        max_age_days: int = 30,
        per_tenant_quota: int = 5,
    ):
        """Initialize the memory pruner.

        Args:
            confidence_floor: Minimum confidence score to keep (0.0-1.0).
                            Default 0.3 = only keep memories 30%+ confident.
            max_age_days: Maximum memory age in days before expiry.
                         Default 30 = 30-day retention policy.
            per_tenant_quota: Maximum memories to keep per tenant.
                            Default 5 = at most 5 memories per tenant.
        """
        if not (0.0 <= confidence_floor <= 1.0):
            raise ValueError(f"confidence_floor must be in [0.0, 1.0], got {confidence_floor}")
        if max_age_days < 0:
            raise ValueError(f"max_age_days must be non-negative, got {max_age_days}")
        if per_tenant_quota < 0:
            raise ValueError(f"per_tenant_quota must be non-negative, got {per_tenant_quota}")

        self.confidence_floor = confidence_floor
        self.max_age_days = max_age_days
        self.per_tenant_quota = per_tenant_quota

    def prune(
        self, memories: List[Any], tenant_id: str = "_default",
        now: Optional[datetime] = None
    ) -> Tuple[List[Any], dict]:
        """Prune low-confidence and expired memories.

        Pipeline:
        1. Filter by confidence floor
        2. Filter by age (relative to 'now')
        3. Sort by confidence (highest first)
        4. Truncate to quota
        5. Return pruned list + telemetry

        Args:
            memories: List of memory objects with 'confidence' and 'created_at' fields.
            tenant_id: Tenant identifier (for logging/audit).
            now: Reference time for age calculation. Default: datetime.now().

        Returns:
            Tuple of (pruned_memories, telemetry_dict)
        """
        start = time.time()
        if now is None:
            now = datetime.now()

        if not memories:
            return [], {
                "memories_before": 0,
                "memories_after": 0,
                "dropped_count": 0,
                "dropped_reasons": {},
                "duration_ms": (time.time() - start) * 1000,
                "tenant_id": tenant_id,
            }

        # Filter by confidence floor
        by_confidence = []
        dropped_by_confidence = 0

        for memory in memories:
            confidence = getattr(memory, "confidence", 0.0) or 0.0
            if confidence >= self.confidence_floor:
                by_confidence.append(memory)
            else:
                dropped_by_confidence += 1

        # Filter by age
        by_age = []
        dropped_by_age = 0
        max_age_delta = timedelta(days=self.max_age_days)

        for memory in by_confidence:
            created_at = getattr(memory, "created_at", None)
            if created_at is None:
                # No timestamp = assume recent
                by_age.append(memory)
                continue

            # Parse timestamp if string
            if isinstance(created_at, str):
                try:
                    created_at = datetime.fromisoformat(created_at)
                except (ValueError, TypeError):
                    # Unparseable timestamp = drop it
                    dropped_by_age += 1
                    continue

            # Check age
            if now - created_at <= max_age_delta:
                by_age.append(memory)
            else:
                dropped_by_age += 1

        # Sort by confidence (highest first)
        sorted_memories = sorted(
            by_age,
            key=lambda m: getattr(m, "confidence", 0.0) or 0.0,
            reverse=True
        )

        # Apply quota
        pruned = sorted_memories[:self.per_tenant_quota]
        dropped_by_quota = len(sorted_memories) - len(pruned)

        duration = (time.time() - start) * 1000

        telemetry = {
            "memories_before": len(memories),
            "memories_after": len(pruned),
            "dropped_count": len(memories) - len(pruned),
            "dropped_reasons": {
                "confidence_below_floor": dropped_by_confidence,
                "age_exceeds_retention": dropped_by_age,
                "quota_exceeded": dropped_by_quota,
            },
            "confidence_floor": self.confidence_floor,
            "max_age_days": self.max_age_days,
            "per_tenant_quota": self.per_tenant_quota,
            "duration_ms": duration,
            "tenant_id": tenant_id,
        }

        logger.debug(
            f"MemoryPruner (tenant={tenant_id}): {len(memories)} memories → {len(pruned)} "
            f"({telemetry['dropped_count']} dropped; "
            f"confidence={dropped_by_confidence}, age={dropped_by_age}, quota={dropped_by_quota})"
        )

        return pruned, telemetry
