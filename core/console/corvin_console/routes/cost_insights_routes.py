"""Cost Insights API Routes for Stories 4-6 (Dashboard Backend).

Endpoints:
- GET /v1/console/cost/summary — Daily spend, trends
- GET /v1/console/cost/breakdown — By component/model/skill
- POST /v1/console/cost/guardrails — Set budget/alerts

License: Apache-2.0
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from core.cost.calculator import CostCalculator
from core.cost.optimizer import CostOptimizer
from core.cost.guardrails import BudgetGuard

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/console/cost", tags=["cost"])


# Pydantic models for request/response

class CostSummaryResponse(BaseModel):
    """Daily spend summary (Story 4)."""
    today_spend: str
    yesterday_spend: str
    weekly_avg: str
    monthly_avg: str

    trend_direction: str  # "up", "down", "stable"
    trend_pct: float      # % change week-over-week

    projections: dict     # {"projected_monthly": str}


class CostBreakdownResponse(BaseModel):
    """Cost breakdown by component (Story 5)."""
    llm_cost: str
    compute_cost: str
    storage_cost: str
    total_cost: str

    llm_pct: float
    compute_pct: float
    storage_pct: float


class CostByModelResponse(BaseModel):
    """Cost by model (Story 6)."""
    models: dict  # {model_id: total_cost_str}


class RecommendationsResponse(BaseModel):
    """Cost optimization recommendations (Story 7)."""
    recommendations: list[dict]
    total_potential_savings: str


class GuardrailsRequest(BaseModel):
    """Set budget guardrails (Story 9)."""
    monthly_budget: str
    daily_budget: Optional[str] = None
    warning_threshold_pct: float = 75.0
    critical_threshold_pct: float = 90.0


class GuardrailsResponse(BaseModel):
    """Budget status response (Story 9-10)."""
    current_spend: str
    budget_limit: str
    percentage_used: float
    remaining_budget: str

    daily_limit: str
    daily_spend: str

    alert_active: bool
    alert_level: Optional[str] = None


# Global service instances

_calculator: Optional[CostCalculator] = None
_optimizer: Optional[CostOptimizer] = None
_guard: Optional[BudgetGuard] = None


def init_services(corvin_home: str):
    """Initialize cost services.

    Args:
        corvin_home: Path to ~/.corvin
    """
    global _calculator, _optimizer, _guard
    _calculator = CostCalculator(corvin_home)
    _optimizer = CostOptimizer()
    _guard = BudgetGuard(corvin_home)


def get_calculator() -> CostCalculator:
    if _calculator is None:
        raise RuntimeError("Cost calculator not initialized")
    return _calculator


def get_optimizer() -> CostOptimizer:
    if _optimizer is None:
        raise RuntimeError("Cost optimizer not initialized")
    return _optimizer


def get_guard() -> BudgetGuard:
    if _guard is None:
        raise RuntimeError("Budget guard not initialized")
    return _guard


# Story 4: Daily Spend Trend
@router.get("/summary", response_model=CostSummaryResponse)
async def get_cost_summary() -> CostSummaryResponse:
    """Get daily spend summary and trends (Story 4).

    Returns:
        Cost summary with trend analysis
    """
    calculator = get_calculator()

    from datetime import datetime, timedelta, timezone
    from decimal import Decimal

    today = datetime.now(timezone.utc).date().isoformat()
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()

    today_spend = calculator.get_daily_cost(today)
    yesterday_spend = calculator.get_daily_cost(yesterday)

    # Calculate weekly and monthly averages
    daily_costs = calculator.get_daily_costs_trend(days=7)
    weekly_avg = Decimal("0")
    if daily_costs:
        total = sum(Decimal(d["cost"]) for d in daily_costs)
        weekly_avg = total / Decimal(len(daily_costs))

    daily_costs_30 = calculator.get_daily_costs_trend(days=30)
    monthly_avg = Decimal("0")
    if daily_costs_30:
        total = sum(Decimal(d["cost"]) for d in daily_costs_30)
        monthly_avg = total / Decimal(len(daily_costs_30))

    # Calculate trend
    trend_direction = "stable"
    trend_pct = 0.0
    if yesterday_spend > 0:
        pct_change = (float(today_spend - yesterday_spend) / float(yesterday_spend)) * 100
        trend_pct = pct_change
        if pct_change > 5:
            trend_direction = "up"
        elif pct_change < -5:
            trend_direction = "down"

    # Project monthly spend
    if weekly_avg > 0:
        projected_monthly = weekly_avg * Decimal("4.33")
    else:
        projected_monthly = Decimal("0")

    return CostSummaryResponse(
        today_spend=str(today_spend),
        yesterday_spend=str(yesterday_spend),
        weekly_avg=str(weekly_avg),
        monthly_avg=str(monthly_avg),
        trend_direction=trend_direction,
        trend_pct=trend_pct,
        projections={"projected_monthly": str(projected_monthly)},
    )


# Story 5: Cost Breakdown
@router.get("/breakdown", response_model=CostBreakdownResponse)
async def get_cost_breakdown(
    days: int = Query(30, description="Days to aggregate"),
) -> CostBreakdownResponse:
    """Get cost breakdown by component (Story 5).

    Args:
        days: Number of days to aggregate

    Returns:
        Breakdown of LLM, compute, and storage costs
    """
    from datetime import datetime, timedelta, timezone
    from decimal import Decimal

    calculator = get_calculator()

    # Get daily costs trend
    daily_costs = calculator.get_daily_costs_trend(days=days)
    total_llm = sum(Decimal(d["cost"]) for d in daily_costs)

    # Stub compute and storage costs (integrate with actual systems)
    # For now: estimate 20% compute, 5% storage (stubbed)
    total_spend = total_llm / Decimal("0.75")  # If LLM is 75%
    compute_cost = total_spend * Decimal("0.20")
    storage_cost = total_spend * Decimal("0.05")

    total = total_llm + compute_cost + storage_cost

    llm_pct = (float(total_llm) / float(total) * 100) if total > 0 else 0
    compute_pct = (float(compute_cost) / float(total) * 100) if total > 0 else 0
    storage_pct = (float(storage_cost) / float(total) * 100) if total > 0 else 0

    return CostBreakdownResponse(
        llm_cost=str(total_llm),
        compute_cost=str(compute_cost),
        storage_cost=str(storage_cost),
        total_cost=str(total),
        llm_pct=llm_pct,
        compute_pct=compute_pct,
        storage_pct=storage_pct,
    )


# Story 6: Cost by Model
@router.get("/by-model", response_model=CostByModelResponse)
async def get_cost_by_model(
    days: int = Query(30, description="Days to aggregate"),
) -> CostByModelResponse:
    """Get cost breakdown by model (Story 6).

    Args:
        days: Number of days to aggregate

    Returns:
        Dict of {model_id: total_cost_str}
    """
    calculator = get_calculator()
    models = calculator.get_cost_by_model(days=days)

    return CostByModelResponse(models=models)


# Story 7: Optimization Recommendations
@router.get("/recommendations", response_model=RecommendationsResponse)
async def get_recommendations() -> RecommendationsResponse:
    """Get cost optimization recommendations (Story 7).

    Returns:
        List of recommendations with estimated savings
    """
    from datetime import datetime, timedelta, timezone
    from decimal import Decimal

    calculator = get_calculator()
    optimizer = get_optimizer()

    # Get current spend metrics
    daily_costs = calculator.get_daily_costs_trend(days=7)
    daily_avg = (
        sum(Decimal(d["cost"]) for d in daily_costs) / Decimal(len(daily_costs))
        if daily_costs
        else Decimal("0")
    )

    # Stub metrics (integrate with actual systems)
    daily_call_count = 1000  # Placeholder
    model_distribution = {
        "claude-opus-5": 500,
        "claude-sonnet-5": 400,
        "claude-haiku-4-5": 100,
    }
    cache_hit_rate = 0.35

    # Generate recommendations
    recommendations = optimizer.generate_recommendations(
        daily_spend=daily_avg,
        daily_call_count=daily_call_count,
        model_distribution=model_distribution,
        cache_hit_rate=cache_hit_rate,
    )

    # Calculate total potential savings
    total_potential_savings = sum(
        Decimal(r.estimated_savings_monthly) for r in recommendations
    )

    return RecommendationsResponse(
        recommendations=[r.to_dict() for r in recommendations],
        total_potential_savings=str(total_potential_savings),
    )


# Stories 9-10: Budget Guardrails
@router.get("/guardrails", response_model=GuardrailsResponse)
async def get_guardrails() -> GuardrailsResponse:
    """Get current budget status (Story 9-10).

    Returns:
        Current spend vs budget
    """
    from datetime import datetime, timezone
    from decimal import Decimal

    calculator = get_calculator()
    guard = get_guard()

    # Get current spend
    today = datetime.now(timezone.utc).date().isoformat()
    daily_spend = calculator.get_daily_cost(today)

    # Estimate monthly spend (7-day average × 30 days)
    daily_costs = calculator.get_daily_costs_trend(days=7)
    if daily_costs:
        weekly_avg = sum(Decimal(d["cost"]) for d in daily_costs) / Decimal(len(daily_costs))
        monthly_spend = weekly_avg * Decimal("4.33")
    else:
        monthly_spend = Decimal("0")

    # Check budget
    status = guard.check_budget(
        current_spend=monthly_spend,
        daily_spend=daily_spend,
    )

    return GuardrailsResponse(
        current_spend=status.current_spend,
        budget_limit=status.budget_limit,
        percentage_used=status.percentage_used,
        remaining_budget=status.remaining_budget,
        daily_limit=status.daily_limit,
        daily_spend=status.daily_spend,
        alert_active=status.alert_active,
        alert_level=status.alert_level.value if status.alert_level else None,
    )


@router.post("/guardrails", response_model=GuardrailsResponse)
async def set_guardrails(request: GuardrailsRequest) -> GuardrailsResponse:
    """Set budget guardrails (Story 9).

    Args:
        request: Budget configuration

    Returns:
        Updated budget status
    """
    from decimal import Decimal

    guard = get_guard()

    monthly_budget = Decimal(request.monthly_budget)
    daily_budget = (
        Decimal(request.daily_budget)
        if request.daily_budget
        else None
    )

    config = guard.set_budget(monthly_budget, daily_budget)

    calculator = get_calculator()
    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).date().isoformat()
    daily_spend = calculator.get_daily_cost(today)

    daily_costs = calculator.get_daily_costs_trend(days=7)
    if daily_costs:
        weekly_avg = sum(Decimal(d["cost"]) for d in daily_costs) / Decimal(len(daily_costs))
        monthly_spend = weekly_avg * Decimal("4.33")
    else:
        monthly_spend = Decimal("0")

    status = guard.check_budget(
        current_spend=monthly_spend,
        daily_spend=daily_spend,
    )

    return GuardrailsResponse(
        current_spend=status.current_spend,
        budget_limit=status.budget_limit,
        percentage_used=status.percentage_used,
        remaining_budget=status.remaining_budget,
        daily_limit=status.daily_limit,
        daily_spend=status.daily_spend,
        alert_active=status.alert_active,
        alert_level=status.alert_level.value if status.alert_level else None,
    )
