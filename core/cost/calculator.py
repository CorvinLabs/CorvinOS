"""Cost Calculator for Phase 1 Week 3 (Stories 1-3, ADR-2031).

Computes cost per LLM call, aggregates by skill, and tracks per-task costs.

License: Apache-2.0
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CallCost:
    """Immutable cost record for a single LLM call (Story 1)."""

    call_id: str
    timestamp: str  # ISO 8601
    tenant_id: str
    model_id: str

    # Token counts
    input_tokens: int
    output_tokens: int

    # Pricing (in EUR, as strings to preserve precision)
    input_cost: str  # Decimal stringified
    output_cost: str
    total_cost: str  # Sum of all costs

    # Optional cache components (defaulted fields must follow required ones)
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_cost: str = "0"
    cache_write_cost: str = "0"

    # Metadata
    skill_id: Optional[str] = None  # Which skill invoked this call (Story 2)
    task_id: Optional[str] = None   # Which task owns this call (Story 3)

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def total_cost_decimal(self) -> Decimal:
        """Parse total_cost back to Decimal."""
        return Decimal(self.total_cost)


@dataclass
class CostBreakdown:
    """Cost breakdown by component (for Story 6 charts)."""

    timestamp: str  # ISO 8601
    llm_cost: Decimal = Decimal("0")       # LLM calls
    compute_cost: Decimal = Decimal("0")   # Worker/compute
    storage_cost: Decimal = Decimal("0")   # Data storage

    @property
    def total_cost(self) -> Decimal:
        return self.llm_cost + self.compute_cost + self.storage_cost

    @property
    def llm_pct(self) -> float:
        """LLM as % of total."""
        total = float(self.total_cost)
        if total == 0:
            return 0.0
        return (float(self.llm_cost) / total) * 100

    @property
    def compute_pct(self) -> float:
        total = float(self.total_cost)
        if total == 0:
            return 0.0
        return (float(self.compute_cost) / total) * 100

    @property
    def storage_pct(self) -> float:
        total = float(self.total_cost)
        if total == 0:
            return 0.0
        return (float(self.storage_cost) / total) * 100

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "llm_cost": str(self.llm_cost),
            "compute_cost": str(self.compute_cost),
            "storage_cost": str(self.storage_cost),
            "total_cost": str(self.total_cost),
            "llm_pct": self.llm_pct,
            "compute_pct": self.compute_pct,
            "storage_pct": self.storage_pct,
        }


@dataclass
class SkillCost:
    """Aggregated cost for a single skill (Story 2)."""

    skill_id: str
    total_cost: Decimal = Decimal("0")
    call_count: int = 0
    avg_cost_per_call: Decimal = Decimal("0")

    def to_dict(self) -> dict:
        return {
            "skill_id": self.skill_id,
            "total_cost": str(self.total_cost),
            "call_count": self.call_count,
            "avg_cost_per_call": str(self.avg_cost_per_call),
        }


@dataclass
class TaskCostRecord:
    """Cost record for a single task (Story 3)."""

    task_id: str
    timestamp: str  # ISO 8601
    tenant_id: str

    total_cost: Decimal = Decimal("0")
    llm_call_count: int = 0
    worker_call_count: int = 0
    duration_seconds: int = 0

    # Cost breakdown by skill
    cost_by_skill: dict[str, Decimal] = field(default_factory=dict)

    # Cost breakdown by model
    cost_by_model: dict[str, Decimal] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "timestamp": self.timestamp,
            "tenant_id": self.tenant_id,
            "total_cost": str(self.total_cost),
            "llm_call_count": self.llm_call_count,
            "worker_call_count": self.worker_call_count,
            "duration_seconds": self.duration_seconds,
            "cost_by_skill": {k: str(v) for k, v in self.cost_by_skill.items()},
            "cost_by_model": {k: str(v) for k, v in self.cost_by_model.items()},
        }


class CostCalculator:
    """Compute costs per call, skill, and task (Stories 1-3)."""

    def __init__(self, corvin_home: str | Path):
        """Initialize calculator.

        Args:
            corvin_home: Path to ~/.corvin
        """
        self.corvin_home = Path(corvin_home)
        self.costs_dir = (
            self.corvin_home / "tenants" / "_default" / "global" / "costs"
        )
        self.costs_dir.mkdir(parents=True, exist_ok=True)

    def calculate_call_cost(
        self,
        call_id: str,
        model_id: str,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        skill_id: Optional[str] = None,
        task_id: Optional[str] = None,
        tenant_id: str = "_default",
    ) -> CallCost:
        """Calculate cost for a single LLM call (Story 1).

        Args:
            call_id: Unique call identifier
            model_id: Model used (e.g., "claude-opus-5")
            input_tokens: Input token count
            output_tokens: Output token count
            cache_read_tokens: Cached tokens read
            cache_write_tokens: Cache write tokens
            skill_id: Associated skill (for Story 2 aggregation)
            task_id: Associated task (for Story 3 tracking)
            tenant_id: Tenant scope

        Returns:
            CallCost with detailed breakdown
        """
        # Use model-specific pricing (stubbed for now; integrate with BillingSchema)
        pricing = self._get_model_pricing(model_id)

        # Calculate costs
        input_cost = Decimal(input_tokens) * pricing["input_cost"]
        output_cost = Decimal(output_tokens) * pricing["output_cost"]
        cache_read_cost = Decimal(cache_read_tokens) * pricing["cache_read_cost"]
        cache_write_cost = Decimal(cache_write_tokens) * pricing["cache_write_cost"]

        total_cost = input_cost + output_cost + cache_read_cost + cache_write_cost

        call_record = CallCost(
            call_id=call_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            tenant_id=tenant_id,
            model_id=model_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            input_cost=str(input_cost),
            output_cost=str(output_cost),
            cache_read_cost=str(cache_read_cost),
            cache_write_cost=str(cache_write_cost),
            total_cost=str(total_cost),
            skill_id=skill_id,
            task_id=task_id,
        )

        # Persist
        self._record_call_cost(call_record)

        return call_record

    def get_skill_cost(self, skill_id: str) -> SkillCost:
        """Get aggregated cost for a skill (Story 2).

        Args:
            skill_id: Skill identifier

        Returns:
            SkillCost with aggregated totals
        """
        total_cost = Decimal("0")
        call_count = 0

        # Sum across all call cost files
        for filepath in self.costs_dir.glob("calls_*.jsonl"):
            with open(filepath, "r") as f:
                for line in f:
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    if data.get("skill_id") == skill_id:
                        call_count += 1
                        total_cost += Decimal(data["total_cost"])

        avg_cost = (
            total_cost / Decimal(call_count)
            if call_count > 0
            else Decimal("0")
        )

        return SkillCost(
            skill_id=skill_id,
            total_cost=total_cost,
            call_count=call_count,
            avg_cost_per_call=avg_cost,
        )

    def get_task_cost(self, task_id: str) -> TaskCostRecord:
        """Get cost record for a task (Story 3).

        Args:
            task_id: Task identifier

        Returns:
            TaskCostRecord with cost breakdown
        """
        total_cost = Decimal("0")
        llm_call_count = 0
        worker_call_count = 0
        cost_by_skill: dict[str, Decimal] = {}
        cost_by_model: dict[str, Decimal] = {}

        # Search all call cost files
        for filepath in self.costs_dir.glob("calls_*.jsonl"):
            with open(filepath, "r") as f:
                for line in f:
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    if data.get("task_id") == task_id:
                        call_cost = Decimal(data["total_cost"])
                        total_cost += call_cost
                        llm_call_count += 1

                        # Aggregate by skill
                        if data.get("skill_id"):
                            skill = data["skill_id"]
                            cost_by_skill[skill] = (
                                cost_by_skill.get(skill, Decimal("0")) + call_cost
                            )

                        # Aggregate by model
                        model = data["model_id"]
                        cost_by_model[model] = (
                            cost_by_model.get(model, Decimal("0")) + call_cost
                        )

        # Compute average for description
        now = datetime.now(timezone.utc)

        return TaskCostRecord(
            task_id=task_id,
            timestamp=now.isoformat(),
            tenant_id="_default",
            total_cost=total_cost,
            llm_call_count=llm_call_count,
            cost_by_skill=cost_by_skill,
            cost_by_model=cost_by_model,
        )

    def get_daily_cost(self, date: Optional[str] = None) -> Decimal:
        """Get total cost for a day.

        Args:
            date: ISO date (YYYY-MM-DD), default: today

        Returns:
            Total cost for the day
        """
        if not date:
            date = datetime.now(timezone.utc).date().isoformat()

        total_cost = Decimal("0")

        # Search all call cost files for this date
        for filepath in self.costs_dir.glob("calls_*.jsonl"):
            with open(filepath, "r") as f:
                for line in f:
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    # Extract date from timestamp
                    ts = datetime.fromisoformat(data["timestamp"])
                    if ts.date().isoformat() == date:
                        total_cost += Decimal(data["total_cost"])

        return total_cost

    def get_daily_costs_trend(
        self,
        days: int = 30,
    ) -> list[dict]:
        """Get daily costs for N days (for Story 4 chart).

        Args:
            days: Number of days to retrieve (default: 30)

        Returns:
            List of {"date": ISO, "cost": decimal_str} dicts
        """
        result = []

        for i in range(days):
            d = datetime.now(timezone.utc).date() - timedelta(days=i)
            date_str = d.isoformat()
            daily_cost = self.get_daily_cost(date_str)
            result.append({
                "date": date_str,
                "cost": str(daily_cost),
            })

        return sorted(result, key=lambda x: x["date"])

    def get_cost_by_model(
        self,
        days: int = 30,
    ) -> dict[str, str]:
        """Get total cost per model over N days (for Story 6 chart).

        Args:
            days: Number of days to aggregate

        Returns:
            Dict of {model_id: total_cost_str}
        """
        result: dict[str, Decimal] = {}
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        for filepath in self.costs_dir.glob("calls_*.jsonl"):
            with open(filepath, "r") as f:
                for line in f:
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    ts = datetime.fromisoformat(data["timestamp"])
                    if ts > cutoff:
                        model = data["model_id"]
                        cost = Decimal(data["total_cost"])
                        result[model] = result.get(model, Decimal("0")) + cost

        return {k: str(v) for k, v in result.items()}

    def _record_call_cost(self, call: CallCost) -> None:
        """Persist call cost to disk."""
        now = datetime.now(timezone.utc)
        filename = f"calls_{now.year}-{now.month:02d}-{now.day:02d}.jsonl"
        filepath = self.costs_dir / filename

        try:
            line = json.dumps(call.to_dict(), separators=(",", ":"))
            with open(filepath, "a", encoding="utf-8") as f:
                f.write(line + "\n")
            filepath.chmod(0o600)
        except Exception as e:
            logger.error(f"Failed to persist call cost: {e}")

    def _get_model_pricing(self, model_id: str) -> dict:
        """Get pricing for a model.

        Currently stubbed; integrates with BillingSchema in production.
        """
        # Placeholder pricing (replace with BillingSchema lookup)
        pricing_table = {
            "claude-opus-5": {
                "input_cost": Decimal("0.003"),      # $3 per million input tokens
                "output_cost": Decimal("0.015"),     # $15 per million output tokens
                "cache_read_cost": Decimal("0.0003"),   # 10% of input
                "cache_write_cost": Decimal("0.0003"),  # Same as input
            },
            "claude-sonnet-5": {
                "input_cost": Decimal("0.003"),
                "output_cost": Decimal("0.015"),
                "cache_read_cost": Decimal("0.0003"),
                "cache_write_cost": Decimal("0.0003"),
            },
            "claude-haiku-4-5": {
                "input_cost": Decimal("0.00008"),
                "output_cost": Decimal("0.0004"),
                "cache_read_cost": Decimal("0.00001"),
                "cache_write_cost": Decimal("0.00001"),
            },
        }

        return pricing_table.get(
            model_id,
            pricing_table["claude-opus-5"],  # Default
        )
