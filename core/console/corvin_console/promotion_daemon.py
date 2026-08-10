"""Auto-promotion daemon (ADR-0288).

Runs hourly. Checks every flag: can it promote? Should it demote?
Logs audit events for all transitions.
Fully automatic; no maintainer approval needed.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Callable

from core.console.corvin_console.promotion_gates import (
    PromotionGates,
    DemotionGates,
    PromotionEvent,
)
from core.telemetry import compute_digest, get_flag_metrics

logger = logging.getLogger(__name__)


@dataclass
class AuditEvent:
    """Audit trail event for tier transitions."""

    event_type: str  # "flag_auto_promoted", "flag_auto_demoted", etc.
    timestamp: str
    flag_id: str
    old_tier: str
    new_tier: str
    reason: str
    metrics_snapshot: dict


class PromotionDaemon:
    """Daemon that checks promotion gates hourly and demotes on error spikes."""

    def __init__(
        self,
        audit_fn: Callable[[AuditEvent], None] | None = None,
        registry_getter: Callable[[], dict] | None = None,
        interval_seconds: int = 3600,
        enabled: bool = True,
    ):
        """Initialize daemon.

        Args:
            audit_fn: Function to log audit events
            registry_getter: Function to get current registry (flag_id → tier)
            interval_seconds: How often to check (default 1 hour)
            enabled: Whether daemon is enabled
        """
        self.audit_fn = audit_fn
        self.registry_getter = registry_getter or (lambda: {})
        self.interval_seconds = interval_seconds
        self.enabled = enabled
        self._running = False
        self._task: asyncio.Task | None = None
        self._last_error_hours: dict[str, int] = {}  # flag_id → consecutive error hours

    async def run(self) -> None:
        """Main daemon loop."""
        if not self.enabled:
            logger.debug("Promotion daemon disabled")
            return

        self._running = True
        logger.info(f"Promotion daemon started (interval: {self.interval_seconds}s)")

        while self._running:
            try:
                await asyncio.sleep(self.interval_seconds)
                await self.check_all_flags()
            except asyncio.CancelledError:
                logger.info("Promotion daemon cancelled")
                break
            except Exception as e:
                logger.error(f"Promotion daemon error: {e}", exc_info=True)

    async def check_all_flags(self) -> None:
        """Check all flags for promotion/demotion eligibility."""
        if not self.enabled:
            return

        registry = self.registry_getter()
        for flag_id, current_tier in registry.items():
            try:
                await self.check_flag(flag_id, current_tier)
            except Exception as e:
                logger.error(f"Error checking flag {flag_id}: {e}", exc_info=True)

    async def check_flag(self, flag_id: str, current_tier: str) -> None:
        """Check one flag for promotion or demotion."""
        metrics = get_flag_metrics(flag_id).get_24h_stats()
        if not metrics:
            return

        # Check demotion first (fail-safe; higher priority)
        should_demote, reason = self._check_demotion(current_tier, metrics)
        if should_demote:
            self._demote_flag(flag_id, current_tier, reason, metrics)
            return

        # Check promotion
        can_promote, new_tier, reason = self._check_promotion(current_tier, metrics)
        if can_promote:
            self._promote_flag(flag_id, current_tier, new_tier, reason, metrics)

    def _check_demotion(self, tier: str, metrics: dict) -> tuple[bool, str]:
        """Check if flag should demote."""
        error_rate = metrics.get("error_rate_24h", 0.0)

        if tier == "beta":
            should_demote, reason = DemotionGates.check_beta_demotion(error_rate)
            if should_demote:
                self._last_error_hours["beta"] = self._last_error_hours.get("beta", 0) + 1
                return should_demote, reason
            else:
                self._last_error_hours["beta"] = 0
                return False, ""

        elif tier == "stable":
            should_demote, reason = DemotionGates.check_stable_demotion(error_rate)
            if should_demote:
                self._last_error_hours["stable"] = self._last_error_hours.get("stable", 0) + 1
                return should_demote, reason
            else:
                self._last_error_hours["stable"] = 0
                return False, ""

        elif tier == "production":
            should_demote, reason = DemotionGates.check_production_demotion(error_rate)
            return should_demote, reason

        return False, ""

    def _check_promotion(self, tier: str, metrics: dict) -> tuple[bool, str | None, str]:
        """Check if flag can promote to next tier.

        Returns: (can_promote, target_tier, reason)
        """
        if tier == "alpha":
            can_promote, reason = PromotionGates.check_alpha_to_beta(metrics)
            if can_promote:
                return True, "beta", reason
            return False, None, reason

        elif tier == "beta":
            can_promote, reason = PromotionGates.check_beta_to_stable(metrics)
            if can_promote:
                return True, "stable", reason
            return False, None, reason

        elif tier == "stable":
            can_promote, reason = PromotionGates.check_stable_to_production(metrics)
            if can_promote:
                return True, "production", reason
            return False, None, reason

        return False, None, ""

    def _promote_flag(
        self,
        flag_id: str,
        old_tier: str,
        new_tier: str,
        reason: str,
        metrics: dict,
    ) -> None:
        """Promote a flag (automatic)."""
        logger.info(f"Promoting {flag_id}: {old_tier} → {new_tier} ({reason})")

        event = AuditEvent(
            event_type="flag_auto_promoted",
            timestamp=datetime.utcnow().isoformat() + "Z",
            flag_id=flag_id,
            old_tier=old_tier,
            new_tier=new_tier,
            reason=reason,
            metrics_snapshot=metrics,
        )

        if self.audit_fn:
            self.audit_fn(event)

    def _demote_flag(
        self,
        flag_id: str,
        current_tier: str,
        reason: str,
        metrics: dict,
    ) -> None:
        """Demote a flag on error spike (automatic)."""
        # Determine target tier (demote one level)
        demotion_map = {"beta": "alpha", "stable": "beta", "production": "stable"}
        new_tier = demotion_map.get(current_tier)

        if not new_tier:
            logger.warning(f"Cannot demote {flag_id} from {current_tier}")
            return

        logger.warning(f"Demoting {flag_id}: {current_tier} → {new_tier} ({reason})")

        event = AuditEvent(
            event_type="flag_auto_demoted",
            timestamp=datetime.utcnow().isoformat() + "Z",
            flag_id=flag_id,
            old_tier=current_tier,
            new_tier=new_tier,
            reason=reason,
            metrics_snapshot=metrics,
        )

        if self.audit_fn:
            self.audit_fn(event)

    def start(self) -> None:
        """Start daemon (async)."""
        if self._task is None:
            self._task = asyncio.create_task(self.run())

    def stop(self) -> None:
        """Stop daemon."""
        self._running = False
        if self._task:
            self._task.cancel()


# Singleton instance
_DAEMON: PromotionDaemon | None = None


def initialize_daemon(
    audit_fn: Callable[[AuditEvent], None] | None = None,
    registry_getter: Callable[[], dict] | None = None,
    enabled: bool = True,
) -> PromotionDaemon:
    """Initialize the global promotion daemon."""
    global _DAEMON
    _DAEMON = PromotionDaemon(audit_fn=audit_fn, registry_getter=registry_getter, enabled=enabled)
    return _DAEMON


def get_daemon() -> PromotionDaemon | None:
    """Get the global promotion daemon."""
    return _DAEMON
