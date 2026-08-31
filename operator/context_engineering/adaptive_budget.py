"""Adaptive budget allocation for Phase 3 context optimization (ADR-0391).

Dynamically allocates token budgets across pipeline stages based on task
complexity and performance metrics. Extends the fixed TokenBudget model
with feedback-driven rebalancing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .task_classifier import TaskComplexity


@dataclass
class PerformanceMetric:
    """Per-stage performance measurement."""
    utilization: float  # 0.0-1.0: actual_tokens / allocated_tokens
    confidence: float   # 0.0-1.0: mean relevance/quality score
    quality: float      # 0.0-1.0: LLM success rate
    latency_ms: float   # execution time in milliseconds


@dataclass
class TokenBudget:
    """Fixed token allocation across pipeline stages.

    Represents the base budget (before adaptive rebalancing).
    Used as the foundation for AdaptiveBudget.
    """
    memory: int           # tokens for memory retrieval
    graph: int            # tokens for ADR/graph traversal
    skills: int           # tokens for skill selection
    synthesis: int        # tokens for LLM synthesis


@dataclass
class AdaptiveBudget:
    """Adaptive token allocation with dynamic rebalancing.

    Allocates budgets per task complexity and adjusts based on stage
    performance metrics collected during pipeline execution.
    """
    memory: int
    graph: int
    skills: int
    synthesis: int

    # Tracking for rebalancing
    stage_adjustments: dict[str, float] = field(default_factory=dict)
    last_rebalance_util: dict[str, float] = field(default_factory=dict)

    @classmethod
    def allocate_for_task(cls, complexity: TaskComplexity,
                         base_budget: "TokenBudget | None" = None
                         ) -> AdaptiveBudget:
        """Allocate budget percentages based on task complexity.

        Args:
            complexity: Task complexity level (SIMPLE/MODERATE/COMPLEX)
            base_budget: Base token budget (used for scaling if provided)

        Returns:
            AdaptiveBudget with stage-specific token allocations
        """
        if base_budget is None:
            base_budget = TokenBudget(
                memory=2000,
                graph=800,
                skills=600,
                synthesis=1600
            )

        # Define allocation percentages per complexity level
        if complexity == TaskComplexity.SIMPLE:
            # SIMPLE: skip expensive stages (graph=0, skills=0)
            allocations = {"memory": 0.60, "graph": 0.0, "skills": 0.0, "synthesis": 0.40}
        elif complexity == TaskComplexity.COMPLEX:
            # COMPLEX: all stages get balanced allocation
            allocations = {"memory": 0.30, "graph": 0.20, "skills": 0.20, "synthesis": 0.30}
        else:
            # MODERATE: balanced but less synthesis
            allocations = {"memory": 0.35, "graph": 0.15, "skills": 0.15, "synthesis": 0.35}

        total_tokens = (base_budget.memory + base_budget.graph +
                       base_budget.skills + base_budget.synthesis)

        return cls(
            memory=int(total_tokens * allocations["memory"]),
            graph=int(total_tokens * allocations["graph"]),
            skills=int(total_tokens * allocations["skills"]),
            synthesis=int(total_tokens * allocations["synthesis"]),
        )

    def rebalance_from_metrics(self, stage_metrics: dict[str, PerformanceMetric],
                              *, rebalance_threshold: float = 0.05) -> None:
        """Dynamically rebalance allocations based on stage performance.

        Adjusts future allocations (capped at ±10% per stage) based on:
          - Low utilization (<30%) → reduce allocation by 5%
          - High confidence (>0.8) → increase allocation by 3%
          - Poor quality (<0.5) → reduce allocation by 7%

        Args:
            stage_metrics: Dict mapping stage_id to PerformanceMetric
            rebalance_threshold: Minimum change to trigger rebalancing (default 5%)
        """
        if not stage_metrics:
            return

        stage_names = ["memory", "graph", "skills", "synthesis"]
        deltas: dict[str, float] = {}

        for stage_name in stage_names:
            metric = stage_metrics.get(stage_name)
            if metric is None:
                continue

            delta = 0.0

            # Low utilization → reduce by 5%
            if metric.utilization < 0.30:
                delta -= 0.05

            # High confidence → increase by 3%
            if metric.confidence > 0.80:
                delta += 0.03

            # Poor quality → reduce by 7%
            if metric.quality < 0.50:
                delta -= 0.07

            # Cap adjustments to ±10% per rebalance
            delta = max(-0.10, min(0.10, delta))

            deltas[stage_name] = delta

        # Apply deltas if any stage has crossed the threshold
        if any(abs(d) >= rebalance_threshold for d in deltas.values()):
            self._apply_adjustments(deltas)
            self.last_rebalance_util = {
                name: stage_metrics[name].utilization
                for name in stage_metrics
            }

    def _apply_adjustments(self, deltas: dict[str, float]) -> None:
        """Apply percentage deltas to current allocations.

        Ensures total token count remains constant by normalizing.
        """
        total = self.memory + self.graph + self.skills + self.synthesis

        if total == 0:
            return

        # Apply deltas as percentage of current allocation
        new_memory = int(self.memory * (1 + deltas.get("memory", 0.0)))
        new_graph = int(self.graph * (1 + deltas.get("graph", 0.0)))
        new_skills = int(self.skills * (1 + deltas.get("skills", 0.0)))
        new_synthesis = int(self.synthesis * (1 + deltas.get("synthesis", 0.0)))

        # Normalize to maintain total (distribute rounding error to synthesis)
        new_total = new_memory + new_graph + new_skills + new_synthesis
        diff = total - new_total
        new_synthesis += diff

        # Ensure no negative allocations
        self.memory = max(0, new_memory)
        self.graph = max(0, new_graph)
        self.skills = max(0, new_skills)
        self.synthesis = max(0, new_synthesis)

        # Track applied adjustments
        self.stage_adjustments = deltas

    def to_dict(self) -> dict[str, int]:
        """Export budget as a dict for pipeline consumption."""
        return {
            "memory": self.memory,
            "graph": self.graph,
            "skills": self.skills,
            "synthesis": self.synthesis,
        }

    def total(self) -> int:
        """Total allocated tokens across all stages."""
        return self.memory + self.graph + self.skills + self.synthesis

    def percentages(self) -> dict[str, float]:
        """Return allocation as percentages (0.0-1.0)."""
        total = self.total()
        if total == 0:
            return {"memory": 0.0, "graph": 0.0, "skills": 0.0, "synthesis": 0.0}
        return {
            "memory": self.memory / total,
            "graph": self.graph / total,
            "skills": self.skills / total,
            "synthesis": self.synthesis / total,
        }
