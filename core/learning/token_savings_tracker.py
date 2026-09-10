"""Token Savings Tracker — tracks baseline vs. actual costs (Phase 4, ADR-0668).

Tracks routing decisions:
1. Baseline cost (what legacy router would use)
2. Actual cost (what ACP router uses)
3. Calculate savings (baseline - actual)
4. Create audit event + persist to chain
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from core.compliance.audit_chain_writer import AuditChainWriter, AuditEvent
from core.skills.license_binding import UserLicense

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SavingsEvent:
    """Immutable token savings event."""

    event_id: str
    timestamp: str  # ISO 8601
    user_id: str
    tenant_id: str
    request_id: str
    skill_id: str
    skill_version: str
    user_tier: str
    baseline_tokens: int
    actual_tokens: int
    savings_tokens: int
    confidence: float
    engine_chosen: str


class TokenSavingsTracker:
    """Tracks and audits token savings per routing decision.

    Responsibilities:
    - Estimate baseline vs. actual cost
    - Calculate savings
    - Create audit events
    - Accumulate to monthly bucket
    """

    # Cost estimation models (baseline vs optimized)
    BASELINE_COST_MODEL = {
        "simple": 100,     # Basic routing
        "moderate": 500,   # Medium complexity
        "complex": 2000,   # High complexity
    }

    OPTIMIZED_COST_MODEL = {
        "simple": 80,      # 20% savings
        "moderate": 350,   # 30% savings
        "complex": 1200,   # 40% savings
    }

    def __init__(
        self,
        audit_chain: Optional[AuditChainWriter] = None,
        savings_store=None,
    ):
        """Initialize tracker.

        Args:
            audit_chain: AuditChainWriter for logging
            savings_store: Storage backend for monthly rollups
        """
        self.audit_chain = audit_chain
        self.savings_store = savings_store

    def track_routing_decision(
        self,
        request_id: str,
        request_complexity: str,  # "simple" | "moderate" | "complex"
        skill_id: str,
        skill_version: str,
        user: UserLicense,
        engine_chosen: str,
        confidence: float = 0.95,
        tenant_id: str = "_default"
    ) -> SavingsEvent:
        """Track a routing decision and calculate savings.

        Args:
            request_id: Unique request ID
            request_complexity: Complexity level for cost estimation
            skill_id: Which Skill made the decision
            skill_version: Skill version
            user: User making the request
            engine_chosen: Engine that was selected (haiku, sonnet, opus)
            confidence: Confidence in the decision
            tenant_id: Tenant scope

        Returns:
            SavingsEvent with calculated savings
        """
        # Estimate costs
        baseline_cost = self._estimate_cost_legacy(request_complexity)
        actual_cost = self._estimate_cost_optimized(request_complexity, engine_chosen)
        savings_tokens = baseline_cost - actual_cost

        # Create savings event
        event_id = f"savings_{request_id}"
        event = SavingsEvent(
            event_id=event_id,
            timestamp=datetime.utcnow().isoformat(),
            user_id=user.user_id,
            tenant_id=tenant_id,
            request_id=request_id,
            skill_id=skill_id,
            skill_version=skill_version,
            user_tier=user.license_tier,
            baseline_tokens=baseline_cost,
            actual_tokens=actual_cost,
            savings_tokens=max(0, savings_tokens),  # Never negative
            confidence=confidence,
            engine_chosen=engine_chosen,
        )

        # Log to audit chain
        self._log_audit(event, tenant_id)

        # Accumulate to monthly bucket
        if self.savings_store:
            self.savings_store.accumulate(user.user_id, event.savings_tokens, event_id, tenant_id)

        logger.info(
            f"Tracked savings: user={user.user_id}, savings={event.savings_tokens}, "
            f"skill={skill_id}, engine={engine_chosen}"
        )

        return event

    def _estimate_cost_legacy(self, complexity: str) -> int:
        """Estimate baseline cost (legacy router)."""
        return self.BASELINE_COST_MODEL.get(complexity, 500)

    def _estimate_cost_optimized(self, complexity: str, engine: str) -> int:
        """Estimate optimized cost (ACP router).

        Cheaper engines (haiku) get further discounts.
        """
        base = self.OPTIMIZED_COST_MODEL.get(complexity, 350)

        # Engine-based discounts
        if engine == "haiku":
            return int(base * 0.7)  # 30% additional discount
        elif engine == "sonnet":
            return int(base * 0.9)  # 10% additional discount
        else:  # opus
            return base

    def _log_audit(self, event: SavingsEvent, tenant_id: str) -> None:
        """Log savings event to audit chain."""
        if not self.audit_chain:
            return

        audit_event = AuditEvent(
            event_id=event.event_id,
            event_type="token_savings_tracked",
            tenant_id=tenant_id,
            user_id=event.user_id,
            timestamp=event.timestamp,
            details={
                "request_id": event.request_id,
                "skill_id": event.skill_id,
                "skill_version": event.skill_version,
                "user_tier": event.user_tier,
                "baseline_tokens": event.baseline_tokens,
                "actual_tokens": event.actual_tokens,
                "savings_tokens": event.savings_tokens,
                "confidence": event.confidence,
                "engine": event.engine_chosen,
            },
            severity="info",
        )

        try:
            self.audit_chain.write_event(audit_event)
        except Exception as e:
            logger.warning(f"Failed to log savings event: {e}")
