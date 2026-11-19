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
from typing import Iterator, Optional

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

    def __init__(self, corvin_home: str | Path, *, tenant_root: str | Path | None = None):
        """Initialize calculator.

        Args:
            corvin_home: Path to ~/.corvin (the ``_default`` tenant is used)
            tenant_root: A tenant home (``tenant_home(tenant_id)``). When given
                it wins: costs are read from ``<tenant_root>/global/costs``.
        """
        self.corvin_home = Path(corvin_home)
        if tenant_root is not None:
            self.costs_dir = Path(tenant_root) / "global" / "costs"
        else:
            self.costs_dir = (
                self.corvin_home / "tenants" / "_default" / "global" / "costs"
            )
        # Not created here: constructing a calculator (every console GET does)
        # must not write. ``_record_call_cost`` makes it on the first record.
        #: Cost-file lines (or whole files) this instance could not use.
        self.skipped_records = 0

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
        for data, _ts, cost in self._records(sorted(self.costs_dir.glob("calls_*.jsonl"))):
            if data.get("skill_id") == skill_id:
                call_count += 1
                total_cost += cost

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
        for data, _ts, call_cost in self._records(sorted(self.costs_dir.glob("calls_*.jsonl"))):
            if data.get("task_id") == task_id:
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

    def _cost_files(self, first_day=None, last_day=None) -> list[Path]:
        """Cost files that can hold a record dated in ``[first_day, last_day]``.

        A file is named for the UTC day it was written on (``calls_YYYY-MM-DD``),
        while a record is bucketed by the date of its own timestamp, which may
        carry another offset: one day of slack on both sides covers that. A file
        whose name carries no parseable date is always read.
        """
        files = []
        for filepath in sorted(self.costs_dir.glob("calls_*.jsonl")):
            try:
                day = datetime.strptime(filepath.stem[len("calls_"):], "%Y-%m-%d").date()
            except ValueError:
                files.append(filepath)
                continue
            if first_day is not None and day < first_day - timedelta(days=1):
                continue
            if last_day is not None and day > last_day + timedelta(days=1):
                continue
            files.append(filepath)
        return files

    def _records(self, files) -> Iterator[tuple[dict, datetime, Decimal]]:
        """``(record, timestamp, total_cost)`` for every USABLE line of *files*.

        One defective line - cut off by a crash, hand-edited, a non-finite
        amount - used to raise out of every reader, so a single bad byte in
        one day's file turned every cost route into a 500. Such a line is
        skipped and counted in :attr:`skipped_records`; so is a file that
        cannot be read. A timestamp without an offset is taken as UTC.
        """
        for filepath in files:
            try:
                handle = open(filepath, "r", encoding="utf-8", errors="replace")
            except OSError:
                self.skipped_records += 1
                continue
            with handle as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                        ts = datetime.fromisoformat(data["timestamp"])
                        cost = Decimal(str(data["total_cost"]))
                        if not isinstance(data.get("model_id"), str) or not cost.is_finite():
                            raise ValueError("unusable cost record")
                    except (ValueError, TypeError, KeyError, ArithmeticError):
                        self.skipped_records += 1
                        continue
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    yield data, ts, cost

    def _costs_by_date(self, first_day, last_day) -> dict[str, Decimal]:
        """Total cost per ISO date in ``[first_day, last_day]``; ONE pass per file."""
        totals: dict[str, Decimal] = {}
        lo, hi = first_day.isoformat(), last_day.isoformat()
        for _data, ts, cost in self._records(self._cost_files(first_day, last_day)):
            day = ts.date().isoformat()
            if lo <= day <= hi:
                totals[day] = totals.get(day, Decimal("0")) + cost
        return totals

    def usage_stats(self, days: int = 7) -> dict:
        """MEASURED call counts over the last *days* days, from the cost files.

        ``cache_hit_rate`` is ``cache_read_tokens / (input_tokens +
        cache_read_tokens)`` and ``None`` when no record carries input tokens -
        an unknown rate is not a rate of zero.
        """
        days = max(1, min(int(days), self.MAX_TREND_DAYS))
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        calls = 0
        by_model: dict[str, int] = {}
        input_tokens = 0
        cache_read_tokens = 0
        active_days: set[str] = set()
        for data, ts, _cost in self._records(self._cost_files(first_day=cutoff.date())):
            if ts <= cutoff:
                continue
            calls += 1
            by_model[data["model_id"]] = by_model.get(data["model_id"], 0) + 1
            active_days.add(ts.date().isoformat())
            for key in ("input_tokens", "cache_read_tokens"):
                value = data.get(key)
                if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                    if key == "input_tokens":
                        input_tokens += value
                    else:
                        cache_read_tokens += value
        denominator = input_tokens + cache_read_tokens
        return {
            "days": days,
            "calls": calls,
            "calls_by_model": by_model,
            "days_with_calls": len(active_days),
            "cache_hit_rate": (cache_read_tokens / denominator) if denominator > 0 else None,
        }

    def get_daily_cost(self, date: Optional[str] = None) -> Decimal:
        """Get total cost for a day.

        Args:
            date: ISO date (YYYY-MM-DD), default: today

        Returns:
            Total cost for the day
        """
        if not date:
            date = datetime.now(timezone.utc).date().isoformat()
        try:
            day = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            return Decimal("0")
        return self._costs_by_date(day, day).get(day.isoformat(), Decimal("0"))

    #: Upper bound for one trend read (the console caps its own query at 366).
    MAX_TREND_DAYS = 366

    def get_daily_costs_trend(
        self,
        days: int = 30,
    ) -> list[dict]:
        """Get daily costs for N days (for Story 4 chart).

        Each cost file inside the window is read ONCE. Until 2026-11-19 every
        day of the window re-read every cost file ever written (days x files).

        Args:
            days: Number of days to retrieve (default: 30, at most
                :attr:`MAX_TREND_DAYS`)

        Returns:
            List of {"date": ISO, "cost": decimal_str} dicts, oldest first
        """
        days = max(0, min(int(days), self.MAX_TREND_DAYS))
        if days == 0:
            return []
        last_day = datetime.now(timezone.utc).date()
        first_day = last_day - timedelta(days=days - 1)
        totals = self._costs_by_date(first_day, last_day)
        return [
            {"date": d.isoformat(), "cost": str(totals.get(d.isoformat(), Decimal("0")))}
            for d in (first_day + timedelta(days=i) for i in range(days))
        ]

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

        for data, ts, cost in self._records(self._cost_files(first_day=cutoff.date())):
            if ts > cutoff:
                model = data["model_id"]
                result[model] = result.get(model, Decimal("0")) + cost

        return {k: str(v) for k, v in result.items()}

    def _record_call_cost(self, call: CallCost) -> None:
        """Persist call cost to disk."""
        now = datetime.now(timezone.utc)
        filename = f"calls_{now.year}-{now.month:02d}-{now.day:02d}.jsonl"
        filepath = self.costs_dir / filename

        try:
            line = json.dumps(call.to_dict(), separators=(",", ":"))
            self.costs_dir.mkdir(parents=True, exist_ok=True)
            with open(filepath, "a", encoding="utf-8") as f:
                f.write(line + "\n")
            filepath.chmod(0o600)
        except Exception as e:
            logger.error(f"Failed to persist call cost: {e}")

    def _get_model_pricing(self, model_id: str) -> dict:
        """Get pricing for a model.

        Currently stubbed; integrates with BillingSchema in production.
        """
        # Placeholder pricing (replace with BillingSchema lookup), in USD PER
        # MILLION tokens. ``calculate_call_cost`` multiplies by a token count,
        # so the table is converted to a per-token rate below. The table used
        # to hold "0.003" commented "$3 per million" and multiplied it per
        # token — every recorded call cost 1000x its price, and a 7-day test
        # flow tripped a 500 EUR budget at 6300 %.
        per_million = {
            "claude-opus-5": {
                "input_cost": Decimal("3"),
                "output_cost": Decimal("15"),
                "cache_read_cost": Decimal("0.3"),   # 10% of input
                "cache_write_cost": Decimal("0.3"),
            },
            "claude-sonnet-5": {
                "input_cost": Decimal("3"),
                "output_cost": Decimal("15"),
                "cache_read_cost": Decimal("0.3"),
                "cache_write_cost": Decimal("0.3"),
            },
            "claude-haiku-4-5": {
                "input_cost": Decimal("0.08"),
                "output_cost": Decimal("0.4"),
                "cache_read_cost": Decimal("0.01"),
                "cache_write_cost": Decimal("0.01"),
            },
        }
        _per_token = Decimal(1_000_000)
        pricing_table = {
            model: {k: v / _per_token for k, v in rates.items()}
            for model, rates in per_million.items()
        }

        return pricing_table.get(
            model_id,
            pricing_table["claude-opus-5"],  # Default
        )
