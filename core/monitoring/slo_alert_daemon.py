"""
SLO Alert Monitoring Daemon (Phase 5 / CRITICAL-3).

Runs periodically to:
1. Collect KPI metrics from system
2. Check SLO thresholds via AlertEngine
3. Send alerts via configured channels (Slack, Console, Email)
4. Emit audit events for all alerts
5. Stream health status to WebSocket

Usage:
    daemon = SLOAlertDaemon(check_interval_seconds=60)
    await daemon.start()
"""

import asyncio
import logging
import os
import sys
from datetime import datetime
from typing import Dict, Optional, List

from core.observability.alert_engine import get_alert_engine, AlertEvent, AlertSeverity
from core.observability.alert_channels import (
    SlackChannel,
    SlackConfig,
    ConsoleChannel,
    EmailChannel,
    EmailConfig,
)
from core.observability.health_monitor import HealthMonitor, HealthStatus, HealthMetric


logger = logging.getLogger(__name__)


class KPICollector:
    """Collect live KPI metrics from system sources."""

    def __init__(self):
        self.kpis: Dict[str, float] = {}
        self.last_update = datetime.utcnow()

    def update_kpi(self, slo_name: str, value: float) -> None:
        """Update a KPI value."""
        self.kpis[slo_name] = value
        self.last_update = datetime.utcnow()

    def get_current_kpis(self) -> Dict[str, float]:
        """Get current KPI snapshot."""
        return dict(self.kpis)

    def get_plugin_availability(self) -> float:
        """
        Calculate plugin availability from system state.

        TODO: Query actual plugin registry.
        For now, returns mock value.
        """
        return 0.995

    def get_delegation_latency_p95(self) -> float:
        """
        Get p95 latency for work delegation.

        TODO: Query actual delegation metrics.
        For now, returns mock value.
        """
        return 185.0

    def get_audit_chain_integrity(self) -> float:
        """
        Get audit chain integrity (1.0 = 100%).

        TODO: Verify actual audit chain.
        For now, returns mock value.
        """
        return 1.0

    def collect(self) -> Dict[str, float]:
        """Collect all KPIs."""
        return {
            "plugin_availability": self.get_plugin_availability(),
            "delegation_latency_p95": self.get_delegation_latency_p95(),
            "audit_chain_integrity": self.get_audit_chain_integrity(),
        }


class SLOAlertDaemon:
    """
    Background daemon for SLO compliance monitoring and alerting.

    Runs periodically to check SLO thresholds and emit alerts.
    """

    def __init__(
        self,
        check_interval_seconds: int = 60,
        health_monitor: Optional[HealthMonitor] = None,
        audit_writer=None,
    ):
        """
        Initialize alert daemon.

        Args:
            check_interval_seconds: How often to run checks (default 60)
            health_monitor: Health monitor for event streaming
            audit_writer: Audit trail writer (optional)
        """
        self.check_interval = check_interval_seconds
        self.health_monitor = health_monitor or HealthMonitor()
        self.audit_writer = audit_writer
        self.kpi_collector = KPICollector()
        self.alert_engine = get_alert_engine()

        self.running = False
        self.task: Optional[asyncio.Task] = None

        # Wire alert channels
        self._setup_alert_channels()

    def _setup_alert_channels(self) -> None:
        """Configure and register alert channels."""
        # Slack channel
        slack_webhook = os.environ.get("CORVIN_SLACK_WEBHOOK_URL")
        if slack_webhook:
            slack_config = SlackConfig(webhook_url=slack_webhook)
            slack_ch = SlackChannel(slack_config)
            self.alert_engine.register_alert_callback(slack_ch.send)
            logger.info("Slack channel enabled")

        # Console channel (always enabled)
        console_ch = ConsoleChannel(
            audit_writer=self.audit_writer,
            console_out=sys.stderr,
        )
        self.alert_engine.register_alert_callback(console_ch.send)
        logger.info("Console channel enabled")

        # Email channel
        email_to = os.environ.get("CORVIN_EMAIL_TO_ADDRS")
        if email_to:
            email_config = EmailConfig(
                smtp_host=os.environ.get("CORVIN_SMTP_HOST", "localhost"),
                smtp_port=int(os.environ.get("CORVIN_SMTP_PORT", "587")),
                use_tls=os.environ.get("CORVIN_SMTP_TLS", "true").lower() == "true",
                username=os.environ.get("CORVIN_SMTP_USER", ""),
                password=os.environ.get("CORVIN_SMTP_PASSWORD", ""),
                from_addr=os.environ.get(
                    "CORVIN_ALERT_FROM_ADDR", "alerts@corvin.local"
                ),
                to_addrs=[a.strip() for a in email_to.split(",") if a.strip()],
                subject_prefix="[CorvinOS Alert]",
            )
            email_ch = EmailChannel(email_config)
            self.alert_engine.register_alert_callback(email_ch.send)
            logger.info("Email channel enabled")

    async def _check_slos_once(self) -> List[AlertEvent]:
        """Run one check cycle."""
        try:
            # Collect KPIs
            kpis = self.kpi_collector.collect()
            logger.debug(f"Collected KPIs: {kpis}")

            # Check all SLOs
            alerts = self.alert_engine.check_all_slos(kpis)

            if alerts:
                logger.warning(f"Triggered {len(alerts)} alert(s)")
                for alert in alerts:
                    logger.warning(
                        f"  - {alert.slo_name}: {alert.severity.value} "
                        f"({alert.measured_value:.2f})"
                    )

            # Emit health status
            overall_state = self.alert_engine.get_current_state()
            await self._emit_health_status(overall_state, alerts)

            return alerts

        except Exception as e:
            logger.error(f"SLO check failed: {e}", exc_info=True)
            return []

    async def _emit_health_status(
        self,
        alert_state: Dict,
        recent_alerts: List[AlertEvent],
    ) -> None:
        """Emit health status to monitor."""
        try:
            # Determine overall health
            critical_count = sum(
                1 for s in alert_state.values()
                if s["severity"] == "critical"
            )
            warning_count = sum(
                1 for s in alert_state.values()
                if s["severity"] == "warning"
            )

            if critical_count > 0:
                status = HealthStatus.ERROR
            elif warning_count > 0:
                status = HealthStatus.DEGRADED
            else:
                status = HealthStatus.OK

            # Build metrics
            metrics = [
                HealthMetric(
                    name="critical_slos",
                    value=float(critical_count),
                    unit="count",
                ),
                HealthMetric(
                    name="warning_slos",
                    value=float(warning_count),
                    unit="count",
                ),
                HealthMetric(
                    name="recent_alerts",
                    value=float(len(recent_alerts)),
                    unit="count",
                ),
            ]

            # Report to health monitor
            await self.health_monitor.report_health(
                subsystem_id="slo_alert_monitor",
                status=status,
                metrics=metrics,
                message=f"{critical_count} critical, {warning_count} warning",
            )

        except Exception as e:
            logger.error(f"Health status emission failed: {e}", exc_info=True)

    async def run_forever(self) -> None:
        """Run monitoring daemon until stopped."""
        self.running = True
        logger.info(f"SLO alert daemon started (interval: {self.check_interval}s)")

        try:
            while self.running:
                await self._check_slos_once()
                await asyncio.sleep(self.check_interval)
        except asyncio.CancelledError:
            logger.info("SLO alert daemon cancelled")
        except Exception as e:
            logger.error(f"SLO alert daemon error: {e}", exc_info=True)
        finally:
            self.running = False

    async def start(self) -> None:
        """Start the daemon in background."""
        if self.task:
            logger.warning("Daemon already running")
            return

        self.task = asyncio.create_task(self.run_forever())
        logger.info("SLO alert daemon background task created")

    async def stop(self) -> None:
        """Stop the daemon."""
        self.running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None
        logger.info("SLO alert daemon stopped")

    def get_alert_history(self, limit: int = 100) -> List[Dict]:
        """Get recent alerts."""
        return [
            a.to_dict()
            for a in self.alert_engine.get_alert_history(limit=limit)
        ]

    def get_current_status(self) -> Dict:
        """Get current monitoring status."""
        return {
            "running": self.running,
            "check_interval_seconds": self.check_interval,
            "alert_engine_state": self.alert_engine.get_current_state(),
            "kpis": self.kpi_collector.get_current_kpis(),
        }


# Global daemon instance
_slo_alert_daemon: Optional[SLOAlertDaemon] = None


def get_slo_alert_daemon() -> SLOAlertDaemon:
    """Get or create global SLO alert daemon."""
    global _slo_alert_daemon
    if _slo_alert_daemon is None:
        check_interval = int(
            os.environ.get("CORVIN_ALERT_CHECK_INTERVAL_S", "60")
        )
        _slo_alert_daemon = SLOAlertDaemon(check_interval_seconds=check_interval)
    return _slo_alert_daemon


def set_slo_alert_daemon(daemon: SLOAlertDaemon) -> None:
    """Set global SLO alert daemon (for testing)."""
    global _slo_alert_daemon
    _slo_alert_daemon = daemon
