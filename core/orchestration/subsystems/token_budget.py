"""TokenBudget context manager for per-stage token allocation (ADR-0388, Phase 2).

Implements the Stage Allocation Model:
  - Memory: 30% of pool
  - ADRs (graph): 20% of pool
  - Skills: 15% of pool
  - Synthesis: 35% of pool (no hard limit; cascade pool flows here)

Cascade logic: unused budget from each stage flows downstream.
  - Memory uses 200/300 (claimed) → unused=100
  - ADR pool = unused(100) + next_alloc(200) = 300
  - ADR uses 150/300 → unused=150
  - Skills pool = unused(150) + next_alloc(150) = 300

This is a PURE ALLOCATION mechanism — it does not truncate or modify content,
only tracks claims and computes cascading pools. The calling stage is responsible
for respecting the allocation when rendering its output.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Stage allocation percentages (must sum to 100.0)
STAGE_ALLOCATIONS = {
    "memory": 0.30,      # 30%
    "graph": 0.20,       # 20% (ADR stage)
    "skill": 0.15,       # 15%
    "synthesis": 0.35,   # 35% (no hard limit)
}

STAGE_ORDER = ["memory", "graph", "skill", "synthesis"]


@dataclass
class StageBudget:
    """Per-stage budget tracking."""
    stage_id: str
    allocated: int          # tokens allocated to this stage (base allocation)
    claimed: int = 0        # tokens claimed by this stage
    spent: int = 0          # actual tokens spent
    cascade_received: int = 0  # cascade pool received from previous stage
    skipped: bool = False   # stage ran but produced nothing

    def effective_allocation(self) -> int:
        """Base allocation + cascade pool."""
        return self.allocated + self.cascade_received

    def remaining(self) -> int:
        """Remaining tokens in this stage's effective allocation."""
        return max(0, self.effective_allocation() - self.spent)

    def utilization(self) -> float:
        """Utilization ratio [0.0, 1.0]."""
        effective = self.effective_allocation()
        if effective == 0:
            return 0.0
        return min(1.0, self.spent / effective)


class TokenBudget:
    """Per-stage token budget allocator with cascade logic.

    Usage:
        budget = TokenBudget(pool_tokens=4000)
        for stage_id in ["memory", "graph", "skill", "synthesis"]:
            allocated = budget.claim(stage_id, estimated_tokens)
            # Render output to fit allocated tokens
            actual = output.token_count()
            budget.spent_for(stage_id, actual)
            # Next stage: budget.claim() gets cascade pool
    """

    def __init__(self, pool_tokens: int, cascade: bool = True):
        """Initialize token budget.

        Args:
            pool_tokens: Total token pool for the entire pipeline.
            cascade: Enable cascade logic (default True).
        """
        self.pool_tokens = pool_tokens
        self.cascade_enabled = cascade
        self._budgets: Dict[str, StageBudget] = {}

        # Initialize stage budgets with base allocations
        for stage_id, pct in STAGE_ALLOCATIONS.items():
            allocated = int(pool_tokens * pct)
            self._budgets[stage_id] = StageBudget(stage_id, allocated)

        logger.debug(
            f"TokenBudget initialized: pool={pool_tokens}, cascade={cascade}, "
            f"stages={list(STAGE_ALLOCATIONS.keys())}"
        )

    def claim(self, stage_id: str, requested: int) -> int:
        """Allocate tokens to a stage.

        Respects cascade logic: if a previous stage didn't use its full allocation,
        the unused budget flows here. Returns the allocated amount (≤ requested).

        Args:
            stage_id: Stage identifier (memory, graph, skill, synthesis).
            requested: Requested token count.

        Returns:
            Allocated token count (≤ requested).
        """
        if stage_id not in self._budgets:
            logger.warning(f"Unknown stage: {stage_id}, returning requested={requested}")
            return requested

        budget = self._budgets[stage_id]
        effective_allocation = budget.effective_allocation()

        # Claim ≤ effective allocation, ≤ requested
        claimed = min(effective_allocation, requested)
        budget.claimed = claimed

        logger.debug(
            f"TokenBudget.claim({stage_id}): "
            f"requested={requested}, base_allocated={budget.allocated}, "
            f"cascade_received={budget.cascade_received}, "
            f"total_available={effective_allocation}, claimed={claimed}"
        )

        return claimed

    def spent_for(self, stage_id: str, actual_tokens: int) -> None:
        """Record actual tokens spent by a stage.

        Args:
            stage_id: Stage identifier.
            actual_tokens: Actual token count produced.
        """
        if stage_id not in self._budgets:
            logger.warning(f"Unknown stage: {stage_id}")
            return

        budget = self._budgets[stage_id]
        budget.spent = actual_tokens

        # Calculate unused budget for cascade (from effective allocation)
        effective = budget.effective_allocation()
        unused = max(0, effective - actual_tokens)

        # Flow unused to next stage
        if self.cascade_enabled and unused > 0:
            # Find next stage in pipeline order
            try:
                current_idx = STAGE_ORDER.index(stage_id)
                if current_idx < len(STAGE_ORDER) - 1:
                    next_stage = STAGE_ORDER[current_idx + 1]
                    self._budgets[next_stage].cascade_received = unused
                    logger.debug(
                        f"TokenBudget.cascade: {stage_id} → {next_stage}, "
                        f"unused={unused} (from effective={effective}, spent={actual_tokens})"
                    )
            except (ValueError, IndexError):
                logger.warning(f"Could not cascade from {stage_id}")

    def spent(self, stage_id: str) -> int:
        """Get actual tokens spent by a stage.

        Args:
            stage_id: Stage identifier.

        Returns:
            Actual tokens spent.
        """
        if stage_id not in self._budgets:
            return 0
        return self._budgets[stage_id].spent

    def remaining(self) -> int:
        """Total budget remaining across all stages.

        Returns:
            Sum of remaining tokens across all stages.
        """
        total = 0
        for budget in self._budgets.values():
            total += budget.remaining()
        return total

    def get_stats(self, stage_id: Optional[str] = None) -> Dict:
        """Get budget statistics for a stage or all stages.

        Args:
            stage_id: Stage identifier (None = all stages).

        Returns:
            Dict with budget stats (allocated, claimed, spent, utilization).
        """
        if stage_id:
            if stage_id not in self._budgets:
                return {}
            budget = self._budgets[stage_id]
            return {
                "stage_id": stage_id,
                "allocated": budget.allocated,
                "cascade_pool": budget.cascade_received,
                "effective_allocation": budget.effective_allocation(),
                "claimed": budget.claimed,
                "spent": budget.spent,
                "remaining": budget.remaining(),
                "utilization": budget.utilization(),
            }

        # All stages
        result = {}
        for stage_id, budget in self._budgets.items():
            result[stage_id] = {
                "allocated": budget.allocated,
                "cascade_pool": budget.cascade_received,
                "effective_allocation": budget.effective_allocation(),
                "claimed": budget.claimed,
                "spent": budget.spent,
                "remaining": budget.remaining(),
                "utilization": budget.utilization(),
            }
        result["total"] = {
            "pool": self.pool_tokens,
            "total_spent": sum(b.spent for b in self._budgets.values()),
            "total_remaining": self.remaining(),
        }
        return result

    def __repr__(self) -> str:
        stats = self.get_stats()
        total = stats.pop("total", {})
        remaining_str = f"remaining={total.get('total_remaining', 0)}"
        stages_str = ", ".join(
            f"{s}:{self._budgets[s].spent}/{self._budgets[s].allocated}"
            for s in STAGE_ORDER if s in self._budgets
        )
        return f"TokenBudget(pool={self.pool_tokens}, {stages_str}, {remaining_str})"
