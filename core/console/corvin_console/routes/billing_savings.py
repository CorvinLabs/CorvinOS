"""Billing Savings API Route (Phase 4, ADR-0668)."""

from __future__ import annotations

from datetime import datetime
from typing import Optional, Annotated

from fastapi import APIRouter, HTTPException, Query, Depends

from core.storage.savings_store import SavingsStore, MonthlySavingsReport
from core.learning.token_savings_tracker import TokenSavingsTracker
from corvin_console.deps import require_session
from corvin_console import auth as session_auth

router = APIRouter(prefix="/v1/console/billing", tags=["billing"])


class BillingSavingsService:
    """Service for billing and savings queries."""

    def __init__(self, corvin_home: str):
        self.savings_store = SavingsStore(corvin_home)

    def get_monthly_report(
        self,
        user_id: str,
        year: Optional[int] = None,
        month: Optional[int] = None,
    ) -> dict:
        """Get monthly savings report for user.

        Args:
            user_id: User ID
            year: Year (default: current year)
            month: Month 1-12 (default: current month)

        Returns:
            MonthlySavingsReport as dict
        """
        if not year or not month:
            now = datetime.utcnow()
            year = year or now.year
            month = month or now.month

        report = self.savings_store.get_monthly_rollup(user_id, year, month)
        return report.to_dict()

    def get_all_time_savings(self, user_id: str) -> dict:
        """Get all-time savings for user."""
        total = self.savings_store.get_all_time_savings(user_id)
        return {
            "user_id": user_id,
            "all_time_savings_tokens": total,
            "all_time_credit_usd": total * 0.01,
        }


# Global service instance
_service: Optional[BillingSavingsService] = None


def init_service(corvin_home: str):
    """Initialize billing service."""
    global _service
    _service = BillingSavingsService(corvin_home)


def get_service() -> BillingSavingsService:
    """Get billing service instance."""
    if not _service:
        raise RuntimeError("Billing service not initialized")
    return _service


@router.get("/savings")
async def get_savings(
    month: Optional[str] = Query(None, description="Month in YYYY-MM format"),
    session: Annotated[session_auth.SessionRecord, Depends(require_session)] = ...,
) -> dict:
    """Get monthly savings report for current user.

    Args:
        month: Month in YYYY-MM format (default: current month)
        session: Authenticated session (provides user_id)

    Returns:
        {
            "user_id": "user123",
            "year": 2026,
            "month": 9,
            "total_savings": 15000,
            "event_count": 25,
            "credit_usd": 150.0
        }

    SECURITY FIX (Phase 9 P0, Issue #8): user_id extracted from authenticated session,
    not hardcoded. Each user now sees their own billing data (fail-closed: 401 if unauthenticated).
    """
    service = get_service()

    # ✅ FIXED: Get user_id from authenticated session, not hardcoded
    user_id = session.sid  # session.sid is the authenticated user ID

    # Parse month if provided
    year = None
    month_num = None
    if month:
        try:
            parts = month.split("-")
            year = int(parts[0])
            month_num = int(parts[1])
        except (ValueError, IndexError):
            raise HTTPException(status_code=400, detail="Invalid month format (YYYY-MM)")

    return service.get_monthly_report(user_id, year, month_num)


@router.get("/savings/all-time")
async def get_all_time_savings(
    session: Annotated[session_auth.SessionRecord, Depends(require_session)] = ...,
) -> dict:
    """Get all-time savings for current user.

    SECURITY FIX (Phase 9 P0, Issue #8): user_id extracted from authenticated session,
    not hardcoded. Each user now sees their own billing data (fail-closed: 401 if unauthenticated).
    """
    service = get_service()

    # ✅ FIXED: Get user_id from authenticated session, not hardcoded
    user_id = session.sid  # session.sid is the authenticated user ID

    return service.get_all_time_savings(user_id)
