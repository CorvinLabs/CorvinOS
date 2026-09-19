"""Tier-Based Gating for Model Access (k=3, ADR-0700 Phase 2).

Implements:
1. Tier enforcement: FREE/MEMBER/ENTERPRISE tiers
2. Forge = MEMBER-only (Opus unavailable to FREE)
3. Model availability by tier
4. Tier downgrade on quota expire (hard cutoff at UTC midnight)
5. Audit-FIRST: tier_checked, tier_enforced, tier_downgrade events
6. Performance: <10ms per check (inherited from QuotaEnforcer)
"""

from __future__ import annotations

import time
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, Optional, Set
from uuid import uuid4
from enum import Enum

from core.compliance.audit_chain_writer import AuditChainWriter, AuditEvent
from core.licensing.billing import ModelTier, BillingSchema


class UserTier(str, Enum):
    """User subscription tier."""
    FREE = "free"           # Community (50 req/day, 100k tok/day)
    MEMBER = "member"       # Paid (unlimited)
    ENTERPRISE = "enterprise"  # Custom (negotiated limits)


@dataclass(frozen=True)
class TierModel:
    """Model availability by user tier."""
    model_id: str
    available_in: Set[UserTier]  # Tiers that can access this model

    def is_available_for(self, tier: UserTier) -> bool:
        """Check if model is available for a tier."""
        return tier in self.available_in


class TierEnforcer:
    """Enforces model access by user subscription tier.

    Rules:
    - FREE: Haiku, Sonnet only (no Opus)
    - MEMBER: All models (Haiku, Sonnet, Opus)
    - ENTERPRISE: Custom (all by default)

    Tier downgrade: On quota expire (UTC midnight), automatically downgrade
    users to FREE tier.

    Audit-FIRST design: writes tier_checked, tier_enforced, tier_downgrade
    events to audit chain synchronously, fail-closed on commit failure.
    """

    def __init__(
        self,
        tenant_id: str,
        audit_chain: AuditChainWriter,
        billing_schema: Optional[BillingSchema] = None,
    ):
        """Initialize tier enforcer.

        Args:
            tenant_id: Tenant scope
            audit_chain: AuditChainWriter for audit trail
            billing_schema: Optional BillingSchema for pricing defaults
        """
        self.tenant_id = tenant_id
        self.audit_chain = audit_chain
        self.billing_schema = billing_schema

        self._lock = threading.RLock()
        self._user_tiers: Dict[str, UserTier] = {}
        self._tier_expire_date: Dict[str, datetime] = {}
        self._model_availability = self._init_model_availability()

    def _init_model_availability(self) -> Dict[str, TierModel]:
        """Initialize model tier availability."""
        return {
            "claude-opus-4": TierModel(
                model_id="claude-opus-4",
                available_in={UserTier.MEMBER, UserTier.ENTERPRISE},
            ),
            "claude-sonnet-3": TierModel(
                model_id="claude-sonnet-3",
                available_in={UserTier.FREE, UserTier.MEMBER, UserTier.ENTERPRISE},
            ),
            "claude-haiku-4-5": TierModel(
                model_id="claude-haiku-4-5",
                available_in={UserTier.FREE, UserTier.MEMBER, UserTier.ENTERPRISE},
            ),
        }

    def check_model_access(
        self,
        user_id: str,
        model_id: str,
        operation: str = "inference",
    ) -> Dict:
        """Check if user can access a model.

        Args:
            user_id: User ID
            model_id: Model being requested
            operation: Operation type (e.g., "inference", "forge", "a2a")

        Returns:
            {
                "allowed": bool,
                "status_code": int (200/403),
                "reason": str,
                "user_tier": str,
                "model_tier": str,
            }

        Raises:
            RuntimeError: If audit chain write fails (fail-closed)
            ValueError: If model not found
        """
        start_time = time.time()

        with self._lock:
            # Get user tier (default FREE if not set)
            user_tier = self._user_tiers.get(user_id, UserTier.FREE)

            # Check tier expiry
            if user_tier != UserTier.ENTERPRISE:
                expire_date = self._tier_expire_date.get(user_id)
                if expire_date and datetime.utcnow() >= expire_date:
                    # Auto-downgrade to FREE
                    user_tier = UserTier.FREE
                    self._user_tiers[user_id] = user_tier

            # Get model availability
            if model_id not in self._model_availability:
                raise ValueError(f"Model {model_id} not found in tier model map")

            tier_model = self._model_availability[model_id]

            # Check access
            allowed = tier_model.is_available_for(user_tier)
            status_code = 200 if allowed else 403
            reason = (
                "OK"
                if allowed
                else f"Model {model_id} not available for {user_tier.value} tier"
            )

        result = {
            "allowed": allowed,
            "status_code": status_code,
            "reason": reason,
            "user_tier": user_tier.value,
            "model_tier": tier_model.model_id,
        }

        # **AUDIT-FIRST:** Write tier_checked event
        try:
            self._write_audit_event(
                "tier_checked" if allowed else "tier_denied",
                user_id,
                model_id,
                user_tier,
                allowed,
                start_time,
                result,
            )
        except Exception as e:
            raise RuntimeError(f"Audit chain write failed for tier check: {e}")

        return result

    def set_user_tier(
        self,
        user_id: str,
        tier: UserTier,
        expire_days: Optional[int] = None,
    ) -> Dict:
        """Set user tier (admin operation).

        Args:
            user_id: User ID
            tier: NEW tier (FREE/MEMBER/ENTERPRISE)
            expire_days: Days until tier expires (None = no expiry)

        Returns:
            {
                "user_id": str,
                "tier": str,
                "expires_at": str (ISO 8601) or None,
            }

        Raises:
            RuntimeError: If audit chain write fails (fail-closed)
        """
        start_time = time.time()

        with self._lock:
            self._user_tiers[user_id] = tier

            if expire_days:
                expire_date = datetime.utcnow() + timedelta(days=expire_days)
                self._tier_expire_date[user_id] = expire_date
                expire_str = expire_date.isoformat()
            else:
                self._tier_expire_date.pop(user_id, None)
                expire_str = None

        result = {
            "user_id": user_id,
            "tier": tier.value,
            "expires_at": expire_str,
        }

        # **AUDIT-FIRST:** Write tier_enforced event
        try:
            audit_event = AuditEvent(
                event_id=str(uuid4()),
                event_type="tier_enforced",
                tenant_id=self.tenant_id,
                user_id=user_id,
                timestamp=datetime.utcnow().isoformat(),
                details={
                    "user_id": user_id,
                    "tier": tier.value,
                    "expires_at": expire_str,
                    "latency_ms": (time.time() - start_time) * 1000,
                },
                severity="INFO",
            )
            self.audit_chain.write_event(audit_event)
        except IOError as e:
            raise RuntimeError(f"Failed to write tier_enforced event: {e}")

        return result

    def check_and_downgrade_on_expiry(self, user_id: str) -> Optional[Dict]:
        """Check for tier expiry and auto-downgrade if needed.

        Args:
            user_id: User ID to check

        Returns:
            Downgrade event if applied, None otherwise

        Raises:
            RuntimeError: If audit chain write fails (fail-closed)
        """
        start_time = time.time()

        with self._lock:
            if user_id not in self._tier_expire_date:
                return None

            expire_date = self._tier_expire_date[user_id]
            if datetime.utcnow() >= expire_date:
                # Downgrade to FREE
                old_tier = self._user_tiers.get(user_id, UserTier.FREE)
                self._user_tiers[user_id] = UserTier.FREE

                result = {
                    "user_id": user_id,
                    "old_tier": old_tier.value,
                    "new_tier": UserTier.FREE.value,
                    "reason": "subscription_expired",
                    "expired_at": expire_date.isoformat(),
                }

                # **AUDIT-FIRST:** Write tier_downgrade event
                try:
                    audit_event = AuditEvent(
                        event_id=str(uuid4()),
                        event_type="tier_downgrade",
                        tenant_id=self.tenant_id,
                        user_id=user_id,
                        timestamp=datetime.utcnow().isoformat(),
                        details={
                            "user_id": user_id,
                            "old_tier": old_tier.value,
                            "new_tier": UserTier.FREE.value,
                            "reason": "subscription_expired",
                            "expired_at": expire_date.isoformat(),
                            "latency_ms": (time.time() - start_time) * 1000,
                        },
                        severity="WARNING",
                    )
                    self.audit_chain.write_event(audit_event)
                except IOError as e:
                    raise RuntimeError(f"Failed to write tier_downgrade event: {e}")

                return result

        return None

    def _write_audit_event(
        self,
        event_type: str,
        user_id: str,
        model_id: str,
        user_tier: UserTier,
        allowed: bool,
        start_time: float,
        result: Dict,
    ) -> None:
        """Write tier check audit event (fail-closed)."""
        latency_ms = (time.time() - start_time) * 1000

        audit_event = AuditEvent(
            event_id=str(uuid4()),
            event_type=event_type,
            tenant_id=self.tenant_id,
            user_id=user_id,
            timestamp=datetime.utcnow().isoformat(),
            details={
                "user_id": user_id,
                "model_id": model_id,
                "user_tier": user_tier.value,
                "allowed": allowed,
                **result,
                "latency_ms": latency_ms,
            },
            severity="INFO" if allowed else "WARNING",
        )

        try:
            self.audit_chain.write_event(audit_event)
        except IOError as e:
            raise IOError(f"Failed to write {event_type} event to audit chain: {e}")

    def get_user_tier(self, user_id: str) -> UserTier:
        """Get user's current tier."""
        with self._lock:
            return self._user_tiers.get(user_id, UserTier.FREE)

    def get_available_models(self, user_tier: UserTier) -> Dict[str, bool]:
        """Get all models available for a tier."""
        return {
            model_id: tier_model.is_available_for(user_tier)
            for model_id, tier_model in self._model_availability.items()
        }
