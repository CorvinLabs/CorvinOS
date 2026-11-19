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
from pydantic import BaseModel, Field, field_validator, model_validator

from core.cost.calculator import CostCalculator
from core.cost.optimizer import CostOptimizer
from core.cost.guardrails import BudgetGuard, InvalidBudget, validate_budget_amount
from fastapi import Depends
from .. import auth as session_auth
from ..deps import require_csrf, require_session, require_session_csrf_on_mutation

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_session_csrf_on_mutation)], prefix="/cost", tags=["cost"])


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
    #: Cost-file lines that could not be read and are not in the figures.
    skipped_records: int = 0


class CostBreakdownResponse(BaseModel):
    """Cost breakdown by component (Story 5).

    Only LLM cost is measured. Compute and storage carry ``None`` and
    ``components_available`` says so: until 2026-11-19 they were derived from
    the LLM figure with two invented shares (20 % and 5 %) and shown as costs.
    """
    llm_cost: str
    compute_cost: Optional[str] = None
    storage_cost: Optional[str] = None
    #: Sum of the MEASURED components only; None when one is missing.
    total_cost: Optional[str] = None

    llm_pct: Optional[float] = None
    compute_pct: Optional[float] = None
    storage_pct: Optional[float] = None

    components_available: dict = Field(default_factory=dict)
    note: Optional[str] = None
    #: Cost-file lines that could not be read and are not in the figures.
    skipped_records: int = 0


class CostByModelResponse(BaseModel):
    """Cost by model (Story 6)."""
    models: dict  # {model_id: total_cost_str}
    skipped_records: int = 0


class RecommendationsResponse(BaseModel):
    """Cost optimization recommendations (Story 7).

    ``available`` is False - with the reason in ``note`` - when there are too
    few measured calls to recommend anything. ``inputs`` are the measured
    figures the recommendations were derived from.
    """
    available: bool = True
    note: Optional[str] = None
    recommendations: list[dict]
    total_potential_savings: Optional[str] = None
    inputs: dict = Field(default_factory=dict)
    skipped_records: int = 0


def _budget_amount(value: Optional[str], name: str) -> Optional[str]:
    if value is None:
        return None
    try:
        return str(validate_budget_amount(value, name))
    except InvalidBudget as exc:
        raise ValueError(str(exc)) from None


class GuardrailsRequest(BaseModel):
    """Set budget guardrails (Story 9).

    Amounts must be finite and greater than zero: ``NaN``, ``Infinity``, ``0``
    and negatives are a 422. They used to be stored as given - a zero budget
    divided by zero on the next read and an infinite one disabled the cap.
    """
    monthly_budget: str = Field(..., max_length=32)
    daily_budget: Optional[str] = Field(None, max_length=32)
    warning_threshold_pct: float = Field(75.0, gt=0, le=100, allow_inf_nan=False)
    critical_threshold_pct: float = Field(90.0, gt=0, le=100, allow_inf_nan=False)

    @field_validator("monthly_budget")
    @classmethod
    def _v_monthly(cls, v: str) -> str:
        return _budget_amount(v, "monthly_budget")

    @field_validator("daily_budget")
    @classmethod
    def _v_daily(cls, v: Optional[str]) -> Optional[str]:
        return _budget_amount(v, "daily_budget")

    @model_validator(mode="after")
    def _v_thresholds(self) -> "GuardrailsRequest":
        if self.warning_threshold_pct > self.critical_threshold_pct:
            raise ValueError("warning_threshold_pct must not exceed critical_threshold_pct")
        return self


class GuardrailsResponse(BaseModel):
    """Budget status response (Story 9-10).

    ``available`` is False when the stored configuration cannot be read: every
    amount is then ``None`` - never the built-in default dressed up as the
    tenant's limit. ``config_state`` is ``default`` (nothing stored: the
    built-in default applies), ``stored`` or ``invalid``.
    """
    available: bool = True
    config_state: str = "stored"
    detail: Optional[str] = None

    current_spend: Optional[str] = None
    budget_limit: Optional[str] = None
    percentage_used: Optional[float] = None
    remaining_budget: Optional[str] = None

    daily_limit: Optional[str] = None
    daily_spend: Optional[str] = None

    alert_active: bool = False
    alert_level: Optional[str] = None

    # The thresholds in force (what POST stored), so a client can show them.
    warning_threshold_pct: Optional[float] = None
    critical_threshold_pct: Optional[float] = None

    #: Cost-file lines that could not be read and are not in the figures.
    skipped_records: int = 0


#: Below this many measured calls in the window no recommendation is made:
#: a share computed over a handful of calls is one data point, not a pattern.
MIN_CALLS_FOR_RECOMMENDATIONS = 50
RECOMMENDATION_WINDOW_DAYS = 7


# Every handler below is a plain ``def``: FastAPI runs it on the worker
# threadpool. They read the tenant's cost files synchronously, which as
# ``async def`` happened on the event loop.
#
# Service resolution (per request, per tenant)
#
# Until 2026-11-19 three module-level singletons were rooted at the install
# root and hard-wired to tenant ``_default`` - every tenant read and SET the
# same budget - and nothing ever called ``init_services``, so each route
# answered 500. The services are cheap (a directory handle plus one small JSON
# file), so they are built per request from the authenticated session's tenant.

def init_services(corvin_home: str) -> None:
    """Kept for callers of the old API; holds no state anymore."""
    logger.info("Cost services resolve per tenant, per request")


def _tenant_root(rec: session_auth.SessionRecord):
    # The CANONICAL resolver (CLAUDE.md, Multi-tenant Axis). ``core.tenants``
    # additionally refuses reserved names (``test``, ``local``, ``admin``, ...)
    # the canonical one accepts, so such a tenant's session got 403 here.
    from pathlib import Path  # noqa: PLC0415
    from forge import paths as _forge_paths  # noqa: PLC0415 - validates tenant_id
    try:
        return Path(_forge_paths.tenant_home(rec.tenant_id))
    except ValueError:
        raise HTTPException(status_code=403, detail="invalid tenant")


def get_calculator(rec: session_auth.SessionRecord) -> CostCalculator:
    # Neither service creates anything on construction: a GET is read-only.
    root = _tenant_root(rec)
    return CostCalculator(root.parent.parent, tenant_root=root)


def get_optimizer() -> CostOptimizer:
    return CostOptimizer()


def get_guard(rec: session_auth.SessionRecord) -> BudgetGuard:
    root = _tenant_root(rec)
    return BudgetGuard(root.parent.parent, tenant_root=root)


_MAX_DAYS = 366


# Story 4: Daily Spend Trend
@router.get("/summary", response_model=CostSummaryResponse)
def get_cost_summary(
    rec: session_auth.SessionRecord = Depends(require_session),
) -> CostSummaryResponse:
    """Get daily spend summary and trends (Story 4).

    Returns:
        Cost summary with trend analysis
    """
    calculator = get_calculator(rec)

    from datetime import datetime, timedelta, timezone
    from decimal import Decimal

    now = datetime.now(timezone.utc)
    today = now.date().isoformat()
    yesterday = (now - timedelta(days=1)).date().isoformat()

    # ONE pass over the cost files for the whole 30-day window; today,
    # yesterday and the 7-day average are read off it. This handler used to
    # scan every cost file 39 times (2 + 7 + 30 separate per-day scans).
    daily_costs_30 = calculator.get_daily_costs_trend(days=30)
    by_date = {d["date"]: Decimal(d["cost"]) for d in daily_costs_30}
    today_spend = by_date.get(today, Decimal("0"))
    yesterday_spend = by_date.get(yesterday, Decimal("0"))

    daily_costs = daily_costs_30[-7:]
    weekly_avg = Decimal("0")
    if daily_costs:
        weekly_avg = sum(Decimal(d["cost"]) for d in daily_costs) / Decimal(len(daily_costs))

    monthly_avg = Decimal("0")
    if daily_costs_30:
        monthly_avg = sum(by_date.values(), Decimal("0")) / Decimal(len(daily_costs_30))

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
        skipped_records=calculator.skipped_records,
    )


# Story 5: Cost Breakdown
@router.get("/breakdown", response_model=CostBreakdownResponse)
def get_cost_breakdown(
    days: int = Query(30, ge=1, le=_MAX_DAYS, description="Days to aggregate"),
    rec: session_auth.SessionRecord = Depends(require_session),
) -> CostBreakdownResponse:
    """Get cost breakdown by component (Story 5).

    Args:
        days: Number of days to aggregate

    Returns:
        Breakdown of LLM, compute, and storage costs
    """
    from datetime import datetime, timedelta, timezone
    from decimal import Decimal

    calculator = get_calculator(rec)

    # LLM cost is what the cost files record. Nothing measures compute or
    # storage cost on this build, so they are reported as not available -
    # not estimated from the LLM figure.
    daily_costs = calculator.get_daily_costs_trend(days=days)
    total_llm = sum((Decimal(d["cost"]) for d in daily_costs), Decimal("0"))

    return CostBreakdownResponse(
        llm_cost=str(total_llm),
        components_available={"llm": True, "compute": False, "storage": False},
        note="Compute and storage costs are not available on this build; only LLM cost is measured.",
        skipped_records=calculator.skipped_records,
    )


# Story 6: Cost by Model
@router.get("/by-model", response_model=CostByModelResponse)
def get_cost_by_model(
    days: int = Query(30, ge=1, le=_MAX_DAYS, description="Days to aggregate"),
    rec: session_auth.SessionRecord = Depends(require_session),
) -> CostByModelResponse:
    """Get cost breakdown by model (Story 6).

    Args:
        days: Number of days to aggregate

    Returns:
        Dict of {model_id: total_cost_str}
    """
    calculator = get_calculator(rec)
    models = calculator.get_cost_by_model(days=days)

    return CostByModelResponse(models=models, skipped_records=calculator.skipped_records)


# Story 7: Optimization Recommendations
@router.get("/recommendations", response_model=RecommendationsResponse)
def get_recommendations(
    rec: session_auth.SessionRecord = Depends(require_session),
) -> RecommendationsResponse:
    """Get cost optimization recommendations (Story 7).

    Returns:
        List of recommendations with estimated savings
    """
    from datetime import datetime, timedelta, timezone
    from decimal import Decimal

    calculator = get_calculator(rec)
    optimizer = get_optimizer()
    window = RECOMMENDATION_WINDOW_DAYS

    # Every input is MEASURED from the tenant's cost files. This route used to
    # feed the optimizer 1000 calls a day, a fixed 500/400/100 model split and
    # a 35 % cache hit rate - constants, shown to every tenant as their usage.
    daily_costs = calculator.get_daily_costs_trend(days=window)
    daily_avg = (
        sum((Decimal(d["cost"]) for d in daily_costs), Decimal("0")) / Decimal(len(daily_costs))
        if daily_costs
        else Decimal("0")
    )
    stats = calculator.usage_stats(days=window)
    inputs = {
        "window_days": window,
        "calls": stats["calls"],
        "days_with_calls": stats["days_with_calls"],
        "calls_by_model": stats["calls_by_model"],
        "cache_hit_rate": stats["cache_hit_rate"],
        "daily_spend_avg": str(daily_avg),
    }
    if stats["calls"] < MIN_CALLS_FOR_RECOMMENDATIONS:
        return RecommendationsResponse(
            available=False,
            note=(
                f"Not enough measured calls for a recommendation: {stats['calls']} in the "
                f"last {window} days, at least {MIN_CALLS_FOR_RECOMMENDATIONS} are needed."
            ),
            recommendations=[],
            inputs=inputs,
            skipped_records=calculator.skipped_records,
        )

    recommendations = optimizer.generate_recommendations(
        daily_spend=daily_avg,
        daily_call_count=round(stats["calls"] / window),
        # Per-day averages, the unit the optimizer's thresholds are written in.
        model_distribution={m: round(n / window) for m, n in stats["calls_by_model"].items()},
        cache_hit_rate=stats["cache_hit_rate"],
    )

    # Calculate total potential savings
    total_potential_savings = sum(
        (Decimal(r.estimated_savings_monthly) for r in recommendations), Decimal("0")
    )

    return RecommendationsResponse(
        recommendations=[r.to_dict() for r in recommendations],
        total_potential_savings=str(total_potential_savings),
        inputs=inputs,
        skipped_records=calculator.skipped_records,
    )


# Stories 9-10: Budget Guardrails
def _budget_status(rec: session_auth.SessionRecord, guard: BudgetGuard) -> GuardrailsResponse:
    """Current spend against *guard*'s budget. Reads only: no alert is recorded."""
    from datetime import datetime, timezone
    from decimal import Decimal

    if guard.config_state == "invalid":
        return GuardrailsResponse(
            available=False,
            config_state="invalid",
            detail=(
                "The stored budget configuration is unreadable. Set the budget "
                "again to replace it; no limit is shown until then."
            ),
        )

    calculator = get_calculator(rec)
    today = datetime.now(timezone.utc).date().isoformat()

    # Estimate monthly spend (7-day average x 4.33 weeks); one pass.
    daily_costs = calculator.get_daily_costs_trend(days=7)
    daily_spend = next((Decimal(d["cost"]) for d in daily_costs if d["date"] == today), Decimal("0"))
    if daily_costs:
        weekly_avg = sum(Decimal(d["cost"]) for d in daily_costs) / Decimal(len(daily_costs))
        monthly_spend = weekly_avg * Decimal("4.33")
    else:
        monthly_spend = Decimal("0")

    status = guard.check_budget(
        current_spend=monthly_spend,
        daily_spend=daily_spend,
        record_alert=False,
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
        warning_threshold_pct=guard.config.warning_threshold_pct,
        critical_threshold_pct=guard.config.critical_threshold_pct,
        config_state=guard.config_state,
        skipped_records=calculator.skipped_records,
    )


@router.get("/guardrails", response_model=GuardrailsResponse)
def get_guardrails(
    rec: session_auth.SessionRecord = Depends(require_session),
) -> GuardrailsResponse:
    """Get current budget status (Story 9-10). Read-only: creates no file."""
    return _budget_status(rec, get_guard(rec))


@router.post("/guardrails", response_model=GuardrailsResponse)
def set_guardrails(
    request: GuardrailsRequest,
    rec: session_auth.SessionRecord = Depends(require_csrf),
) -> GuardrailsResponse:
    """Set budget guardrails (Story 9): amounts and both alert thresholds."""
    from decimal import Decimal

    guard = get_guard(rec)

    try:
        guard.set_budget(
            Decimal(request.monthly_budget),
            Decimal(request.daily_budget) if request.daily_budget is not None else None,
            warning_threshold_pct=request.warning_threshold_pct,
            critical_threshold_pct=request.critical_threshold_pct,
        )
    except (InvalidBudget, ArithmeticError) as exc:
        # The request model already refuses these; this is the second guard.
        raise HTTPException(status_code=422, detail=str(exc))
    except OSError:
        logger.error("budget config could not be written")
        raise HTTPException(status_code=500, detail="Budget could not be saved")

    return _budget_status(rec, guard)
