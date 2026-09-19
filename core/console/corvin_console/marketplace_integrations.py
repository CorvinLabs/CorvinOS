"""Marketplace integration + Quota HUD (k=5 Track B, ADR-0700 Final Phase)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List
from uuid import uuid4

from core.compliance.audit_chain_writer import AuditChainWriter, AuditEvent
from core.licensing.billing import ModelTier


@dataclass
class TierBadge:
    """Tier badge for marketplace model card."""
    model_id: str
    tier: str
    label: str
    color: str


class MarketplaceIntegrations:
    """Marketplace: tier badges, quota HUD, discovery filters."""

    def __init__(self, audit_chain: AuditChainWriter, tenant_id: str = "_default"):
        self.audit_chain = audit_chain
        self.tenant_id = tenant_id
        self._badges_cache = {}
        self._quota_cache = {}

    def render_tier_badge(self, model_id: str, tier: str) -> Dict:
        """Render tier badge on marketplace card.

        Args:
            model_id: Model ID (e.g., "claude-opus-4")
            tier: Tier (FREE, MEMBER, ENTERPRISE)

        Returns:
            {
                "model_id": str,
                "badge": {"label": str, "color": str},
            }

        Raises:
            RuntimeError: If audit chain write fails (fail-closed)
        """
        start_time = time.time()

        # Map tier to badge
        badge_map = {
            "free": {"label": "FREE", "color": "gray"},
            "member": {"label": "MEMBER", "color": "blue"},
            "enterprise": {"label": "ENTERPRISE", "color": "purple"},
        }

        badge = badge_map.get(tier.lower(), {"label": "UNKNOWN", "color": "gray"})

        result = {
            "model_id": model_id,
            "badge": badge,
            "rendered_at": datetime.utcnow().isoformat(),
        }

        # **AUDIT-FIRST:** Write marketplace_view event
        try:
            audit_event = AuditEvent(
                event_id=str(uuid4()),
                event_type="marketplace_view",
                tenant_id=self.tenant_id,
                user_id=None,
                timestamp=datetime.utcnow().isoformat(),
                details={
                    "model_id": model_id,
                    "tier": tier,
                    "badge": badge,
                    "latency_ms": (time.time() - start_time) * 1000,
                },
                severity="INFO",
            )
            self.audit_chain.write_event(audit_event)
        except IOError as e:
            raise RuntimeError(f"Audit chain write failed for marketplace view: {e}")

        return result

    def render_quota_hud(
        self,
        user_id: str,
        tokens_remaining: int,
        requests_remaining: int,
    ) -> Dict:
        """Render real-time quota HUD meter.

        Args:
            user_id: User ID
            tokens_remaining: Remaining daily tokens
            requests_remaining: Remaining daily requests

        Returns:
            {
                "user_id": str,
                "quota_meter": {
                    "tokens": {"remaining": int, "color": str, "percent": float},
                    "requests": {"remaining": int, "color": str, "percent": float},
                },
                "cached": bool,
                "cache_ttl_seconds": int,
            }

        Raises:
            RuntimeError: If audit chain write fails (fail-closed)
        """
        start_time = time.time()

        # Color coding: green (>50%), yellow (20-50%), red (<20%)
        def get_color(remaining: int, limit: int) -> str:
            percent = (remaining / limit * 100) if limit > 0 else 100
            if percent > 50:
                return "green"
            elif percent > 20:
                return "yellow"
            else:
                return "red"

        tokens_color = get_color(tokens_remaining, 100_000)
        requests_color = get_color(requests_remaining, 50)

        result = {
            "user_id": user_id,
            "quota_meter": {
                "tokens": {
                    "remaining": tokens_remaining,
                    "color": tokens_color,
                    "percent": (tokens_remaining / 100_000 * 100) if tokens_remaining >= 0 else 0,
                },
                "requests": {
                    "remaining": requests_remaining,
                    "color": requests_color,
                    "percent": (requests_remaining / 50 * 100) if requests_remaining >= 0 else 0,
                },
            },
            "cached": True,
            "cache_ttl_seconds": 60,
        }

        # **AUDIT-FIRST:** Write quota_displayed event
        try:
            audit_event = AuditEvent(
                event_id=str(uuid4()),
                event_type="quota_displayed",
                tenant_id=self.tenant_id,
                user_id=user_id,
                timestamp=datetime.utcnow().isoformat(),
                details={
                    "user_id": user_id,
                    "tokens_remaining": tokens_remaining,
                    "requests_remaining": requests_remaining,
                    **result,
                    "latency_ms": (time.time() - start_time) * 1000,
                },
                severity="INFO",
            )
            self.audit_chain.write_event(audit_event)
        except IOError as e:
            raise RuntimeError(f"Audit chain write failed for quota display: {e}")

        return result

    def apply_discovery_filter(self, tier_filter: str) -> Dict:
        """Discovery filter: show models available for tier.

        Args:
            tier_filter: Tier to filter by (FREE, MEMBER, ENTERPRISE)

        Returns:
            {
                "tier_filter": str,
                "available_models": [model_id, ...],
                "count": int,
            }

        Raises:
            RuntimeError: If audit chain write fails (fail-closed)
        """
        start_time = time.time()

        # Map tier to available models
        tier_models = {
            "free": ["claude-haiku-4-5", "claude-sonnet-3"],
            "member": ["claude-haiku-4-5", "claude-sonnet-3", "claude-opus-4"],
            "enterprise": ["claude-haiku-4-5", "claude-sonnet-3", "claude-opus-4"],
        }

        available_models = tier_models.get(tier_filter.lower(), [])

        result = {
            "tier_filter": tier_filter,
            "available_models": available_models,
            "count": len(available_models),
        }

        # **AUDIT-FIRST:** Write discovery_filter event
        try:
            audit_event = AuditEvent(
                event_id=str(uuid4()),
                event_type="discovery_filter",
                tenant_id=self.tenant_id,
                user_id=None,
                timestamp=datetime.utcnow().isoformat(),
                details={
                    "tier_filter": tier_filter,
                    "available_count": len(available_models),
                    "latency_ms": (time.time() - start_time) * 1000,
                },
                severity="INFO",
            )
            self.audit_chain.write_event(audit_event)
        except IOError as e:
            raise RuntimeError(f"Audit chain write failed for discovery filter: {e}")

        return result

    def verify_quota_accuracy(
        self,
        user_id: str,
        measured_tokens: int,
        expected_tokens: int,
    ) -> Dict:
        """Verify quota accuracy: ±5% tolerance.

        Args:
            user_id: User ID
            measured_tokens: Measured remaining tokens
            expected_tokens: Expected remaining tokens

        Returns:
            {
                "accuracy_percent": float,
                "within_tolerance": bool,
                "tolerance_percent": float,
            }

        Raises:
            RuntimeError: If audit chain write fails (fail-closed)
        """
        start_time = time.time()

        if expected_tokens == 0:
            accuracy = 100.0
        else:
            accuracy = (measured_tokens / expected_tokens * 100)

        tolerance = 5.0
        within_tolerance = abs(accuracy - 100) <= tolerance

        result = {
            "user_id": user_id,
            "accuracy_percent": accuracy,
            "within_tolerance": within_tolerance,
            "tolerance_percent": tolerance,
        }

        # **AUDIT-FIRST:** Write quota_verified event
        try:
            audit_event = AuditEvent(
                event_id=str(uuid4()),
                event_type="quota_verified",
                tenant_id=self.tenant_id,
                user_id=user_id,
                timestamp=datetime.utcnow().isoformat(),
                details={
                    "user_id": user_id,
                    "measured_tokens": measured_tokens,
                    "expected_tokens": expected_tokens,
                    "accuracy_percent": accuracy,
                    "within_tolerance": within_tolerance,
                    "latency_ms": (time.time() - start_time) * 1000,
                },
                severity="INFO" if within_tolerance else "WARNING",
            )
            self.audit_chain.write_event(audit_event)
        except IOError as e:
            raise RuntimeError(f"Audit chain write failed for quota verification: {e}")

        return result
