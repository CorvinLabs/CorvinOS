"""Phase 3.1: Multi-Tenant Isolation - quota interface WITHOUT an implementation.

Until 2026-11-19 ``check_quota`` and ``deduct_cost`` both returned ``True``
unconditionally: every tenant always "had quota" and every deduction
"succeeded" while nothing was checked, stored or deducted. A caller wiring
this in would have believed it had a spend limit.

No quota store exists behind this class, so it now says so. Real enforcement
lives elsewhere:

* licence quotas   - ``corvin_operator/license/quota_counter.py``
* budget / spend   - ``core/cost/guardrails.py`` (``BudgetGuard.enforce_spend_cap``)

``MultiTenantManager`` has no production caller.
"""
from __future__ import annotations

import math

from core.tenants import validate_tenant_id


class QuotaNotImplemented(NotImplementedError):
    """No quota backend is wired; nothing was checked or deducted."""


def _validate(tenant_id: str, cost: float) -> None:
    validate_tenant_id(tenant_id)
    if isinstance(cost, bool) or not isinstance(cost, (int, float)):
        raise ValueError("cost must be a number")
    if not math.isfinite(cost) or cost < 0:
        raise ValueError("cost must be finite and not negative")


class MultiTenantManager:
    """Fail-closed placeholder: validates its input, then refuses."""

    async def check_quota(self, tenant_id: str, cost: float) -> bool:
        """Always ``False``: with no quota store, no quota can be confirmed."""
        _validate(tenant_id, cost)
        return False

    async def deduct_cost(self, tenant_id: str, cost: float) -> bool:
        """Raises :class:`QuotaNotImplemented`: nothing can be deducted.

        Returning ``False`` would read as "insufficient funds"; returning
        ``True`` would claim a deduction that never happened.
        """
        _validate(tenant_id, cost)
        raise QuotaNotImplemented(
            "MultiTenantManager has no quota backend; no cost was deducted"
        )
