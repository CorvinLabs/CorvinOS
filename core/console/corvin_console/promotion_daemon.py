"""Auto-promotion daemon (ADR-0288).

Runs hourly. Checks every flag: can it promote? Should it demote?
Logs audit events for all transitions.
Fully automatic; no maintainer approval needed.

Telemetry (ADR-0325):
  - Emits KPI: promotion_daemon_runs (counter)
  - Emits KPI: skills_promoted_24h (gauge)
  - Emits KPI: skills_demoted_24h (gauge)
  - Logs promotion/demotion decisions to audit trail
"""
from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

from .promotion_gates import (
    DemotionGates,
    PromotionGates,
)
from core.telemetry import get_flag_metrics
from core.telemetry.source_of_truth import TelemetryRegistry, MetricType

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


def _initialize_telemetry_contracts() -> None:
    """Register telemetry metrics for promotion daemon.

    Registers:
      - promotion_daemon_runs: counter (how many times daemon checked flags)
      - skills_promoted_24h: gauge (skills promoted in last 24h)
      - skills_demoted_24h: gauge (skills demoted in last 24h)
    """
    registry = TelemetryRegistry()

    if not registry.is_metric_registered("promotion_daemon_runs"):
        registry.register_metric(
            "promotion_daemon_runs",
            MetricType.COUNTER,
            required_labels={"tenant_id"},
            description="Number of times promotion daemon ran promotion checks",
            unit="count",
        )

    if not registry.is_metric_registered("skills_promoted_24h"):
        registry.register_metric(
            "skills_promoted_24h",
            MetricType.GAUGE,
            required_labels={"tenant_id"},
            description="Number of skills promoted in the last 24 hours",
            unit="count",
        )

    if not registry.is_metric_registered("skills_demoted_24h"):
        registry.register_metric(
            "skills_demoted_24h",
            MetricType.GAUGE,
            required_labels={"tenant_id"},
            description="Number of skills demoted in the last 24 hours",
            unit="count",
        )


class PromotionDaemon:
    """Daemon that checks promotion gates hourly and demotes on error spikes."""

    def __init__(
        self,
        audit_fn: Callable[[AuditEvent], None] | None = None,
        registry_getter: Callable[[], dict] | None = None,
        interval_seconds: int = 3600,
        enabled: bool = True,
        tenant_id: str = "_default",
    ):
        """Initialize daemon.

        Args:
            audit_fn: Function to log audit events
            registry_getter: Function to get current registry (flag_id → tier)
            interval_seconds: How often to check (default 1 hour)
            enabled: Whether daemon is enabled
            tenant_id: Tenant ID for metric isolation
        """
        self.audit_fn = audit_fn
        self.registry_getter = registry_getter or (lambda: {})
        self.interval_seconds = interval_seconds
        self.enabled = enabled
        self.tenant_id = tenant_id
        self._running = False
        self._task: asyncio.Task | None = None
        self._start_lock = threading.Lock()
        self._promotions_count = 0
        self._demotions_count = 0

        # Initialize telemetry contracts on creation
        _initialize_telemetry_contracts()

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

        # Emit KPI: daemon ran
        try:
            registry = TelemetryRegistry()
            registry.record_metric(
                "promotion_daemon_runs",
                value=1.0,
                labels={"tenant_id": self.tenant_id},
                tenant_id=self.tenant_id,
            )
        except Exception as e:
            logger.warning(f"Failed to record promotion_daemon_runs KPI: {e}")

        registry = self.registry_getter()
        for flag_id, current_tier in registry.items():
            try:
                await self.check_flag(flag_id, current_tier)
            except Exception as e:
                logger.error(f"Error checking flag {flag_id}: {e}", exc_info=True)

        # Emit KPI: promotions/demotions in this run
        try:
            registry = TelemetryRegistry()
            registry.record_metric(
                "skills_promoted_24h",
                value=float(self._promotions_count),
                labels={"tenant_id": self.tenant_id},
                tenant_id=self.tenant_id,
            )
            registry.record_metric(
                "skills_demoted_24h",
                value=float(self._demotions_count),
                labels={"tenant_id": self.tenant_id},
                tenant_id=self.tenant_id,
            )
        except Exception as e:
            logger.warning(f"Failed to record promotion/demotion KPIs: {e}")

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
            return should_demote, reason

        elif tier == "stable":
            should_demote, reason = DemotionGates.check_stable_demotion(error_rate)
            return should_demote, reason

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
        self._promotions_count += 1

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
        self._demotions_count += 1

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
        """Start daemon (async) — thread-safe."""
        with self._start_lock:
            if self._task is None or self._task.done():
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
    tenant_id: str = "_default",
) -> PromotionDaemon:
    """Initialize the global promotion daemon.

    Args:
        audit_fn: Function to log audit events
        registry_getter: Function to get current registry (flag_id → tier)
        enabled: Whether daemon is enabled
        tenant_id: Tenant ID for metric isolation
    """
    global _DAEMON
    _DAEMON = PromotionDaemon(
        audit_fn=audit_fn,
        registry_getter=registry_getter,
        enabled=enabled,
        tenant_id=tenant_id,
    )
    return _DAEMON


def get_daemon() -> PromotionDaemon | None:
    """Get the global promotion daemon."""
    return _DAEMON
