"""Cron Service — scheduled polling of loss-trigger detector.

Manages a background APScheduler job that runs CronTriggerPoller on a
configurable interval (default: 1 hour). Started at CorvinOS boot and
stopped on shutdown.

Features:
- Singleton service
- Pausable/resumable via API
- Manual trigger support (for testing)
- Fail-closed error handling
- Comprehensive logging

ADR-0613: Loss signals feed autonomous forge loop.
"""

import json
import logging
import time
from pathlib import Path
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from core.paths import corvin_home
from corvin_operator.skill_forge.automation.cron_trigger_poller import (
    CronTriggerPoller,
)

logger = logging.getLogger(__name__)


class CronService:
    """Scheduled polling service for loss-trigger detection.

    Manages background APScheduler job. Singleton pattern ensures only one
    scheduler instance runs per CorvinOS process.

    Constraints (load-bearing):
    - Only one instance per process (singleton)
    - Fail-closed: scheduler errors don't crash the service
    - Pausable: can be paused/resumed via API
    - Configurable: poll interval from config file
    """

    _instance: Optional["CronService"] = None

    def __new__(cls):
        """Singleton factory."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        """Initialize service (once per singleton)."""
        if self._initialized:
            return

        self._initialized = True
        self.poller = CronTriggerPoller()
        self.scheduler: Optional[BackgroundScheduler] = None
        self._paused = False
        self._poll_interval_minutes = 60  # default

        logger.info("CronService initialized")

    def start(self) -> None:
        """Start the scheduled polling job.

        Creates and starts the background scheduler with configured interval.
        Idempotent: calling start() twice has no effect.

        Side effects:
            - Creates APScheduler BackgroundScheduler
            - Registers poll_all_tenants job
            - Scheduler runs in daemon thread
        """
        if self.scheduler is not None:
            logger.debug("CronService already started")
            return

        logger.info("Starting CronService scheduler...")

        # Read poll interval from config
        self._poll_interval_minutes = self._read_poll_interval()

        # Create scheduler
        self.scheduler = BackgroundScheduler(daemon=True)

        # Register job
        job = self.scheduler.add_job(
            func=self._run_poll,
            trigger=IntervalTrigger(minutes=self._poll_interval_minutes),
            id="cron_trigger_poll",
            name="Cron Trigger Poll",
            replace_existing=True,
            coalesce=True,  # Don't queue multiple if missed
            max_instances=1,  # Only one running at a time
        )

        # Start scheduler
        try:
            self.scheduler.start()
            logger.info(
                f"CronService started with {self._poll_interval_minutes} "
                f"minute interval"
            )
        except Exception as e:
            logger.error(
                f"Failed to start scheduler: {type(e).__name__}: {e}",
                exc_info=True,
            )
            self.scheduler = None
            raise

    def stop(self) -> None:
        """Stop the scheduled polling job.

        Gracefully shuts down the scheduler. Idempotent: calling stop()
        twice has no effect.

        Side effects:
            - Stops APScheduler background thread
            - Logs shutdown message
        """
        if self.scheduler is None:
            logger.debug("CronService not running")
            return

        logger.info("Stopping CronService scheduler...")
        try:
            self.scheduler.shutdown(wait=True)
            self.scheduler = None
            logger.info("CronService stopped")
        except Exception as e:
            logger.error(
                f"Error stopping scheduler: {type(e).__name__}: {e}",
                exc_info=True,
            )

    def trigger_now(self, tenant_id: str) -> dict:
        """Manually trigger poll for a specific tenant.

        Used for testing and operator-initiated checks. Runs immediately
        in the calling thread (blocking).

        Args:
            tenant_id: Tenant identifier

        Returns:
            dict: Poll result {tenant_id, loss_signals, timestamp}
        """
        logger.info(f"Manual trigger for tenant {tenant_id}")

        start_time = time.time()
        triggers = self.poller.run_once(tenant_id)
        elapsed = time.time() - start_time

        return {
            "tenant_id": tenant_id,
            "loss_signals": len(triggers),
            "timestamp": start_time,
            "elapsed_seconds": elapsed,
        }

    def trigger_now_all(self) -> dict:
        """Manually trigger poll for all tenants.

        Runs immediately in calling thread (blocking). Used for testing
        and full-system checks.

        Returns:
            dict: Poll result {total_loss_signals, timestamp, elapsed_seconds}
        """
        logger.info("Manual trigger for all tenants")

        start_time = time.time()
        total = self.poller.poll_all_tenants()
        elapsed = time.time() - start_time

        return {
            "total_loss_signals": total,
            "timestamp": start_time,
            "elapsed_seconds": elapsed,
        }

    def pause(self) -> None:
        """Pause scheduled polling without stopping scheduler.

        Removes the job from scheduler. Resume to re-add it.

        Side effects:
            - Removes cron_trigger_poll job from scheduler
            - Sets _paused flag
        """
        if self.scheduler is None:
            logger.warning("Scheduler not running; cannot pause")
            return

        try:
            self.scheduler.remove_job("cron_trigger_poll")
            self._paused = True
            logger.info("CronService polling paused")
        except Exception as e:
            logger.error(
                f"Error pausing scheduler: {type(e).__name__}: {e}",
                exc_info=True,
            )

    def resume(self) -> None:
        """Resume scheduled polling after pause.

        Re-adds the job to scheduler with configured interval.

        Side effects:
            - Re-registers cron_trigger_poll job
            - Clears _paused flag
        """
        if self.scheduler is None:
            logger.warning("Scheduler not running; cannot resume")
            return

        try:
            self.scheduler.add_job(
                func=self._run_poll,
                trigger=IntervalTrigger(minutes=self._poll_interval_minutes),
                id="cron_trigger_poll",
                name="Cron Trigger Poll",
                replace_existing=True,
            )
            self._paused = False
            logger.info("CronService polling resumed")
        except Exception as e:
            logger.error(
                f"Error resuming scheduler: {type(e).__name__}: {e}",
                exc_info=True,
            )

    def get_status(self) -> dict:
        """Get service status.

        Returns:
            dict: {running, paused, last_poll_time, poll_interval_minutes}
        """
        poller_status = self.poller.get_status()
        return {
            "running": self.scheduler is not None,
            "paused": self._paused,
            "poll_interval_minutes": self._poll_interval_minutes,
            "last_poll_time": poller_status.get("last_poll_time"),
            "last_poll_count": poller_status.get("last_poll_count"),
        }

    # Private methods

    def _run_poll(self) -> None:
        """Internal job function called by scheduler.

        Calls poller.poll_all_tenants() with error handling.
        Fails gracefully: exceptions are logged, don't crash scheduler.

        Side effects:
            - Logs poll results
            - Updates poller status
        """
        if self._paused:
            logger.debug("Poll skipped (paused)")
            return

        try:
            logger.debug("Running scheduled cron poll...")
            total = self.poller.poll_all_tenants()
            logger.debug(f"Scheduled cron poll complete: {total} triggers")
        except Exception as e:
            logger.error(
                f"Scheduled poll failed: {type(e).__name__}: {e}",
                exc_info=True,
            )

    def _read_poll_interval(self) -> int:
        """Read poll interval from config file.

        Looks for autonomous_forge.cron_poll_interval_minutes in config.
        Defaults to 60 minutes if not found or invalid.

        Returns:
            int: Poll interval in minutes (minimum 1)
        """
        config_path = corvin_home() / "global" / "autonomous_forge.yaml"

        if not config_path.exists():
            logger.debug(f"Config not found: {config_path}; using default 60")
            return 60

        try:
            import yaml

            with open(config_path, "r") as f:
                config = yaml.safe_load(f) or {}

            autonomous_forge = config.get("autonomous_forge", {})
            interval = autonomous_forge.get("cron_poll_interval_minutes", 60)

            if not isinstance(interval, int) or interval < 1:
                logger.warning(
                    f"Invalid poll interval {interval}; using default 60"
                )
                return 60

            return interval

        except Exception as e:
            logger.error(
                f"Error reading config: {type(e).__name__}: {e}; "
                f"using default 60",
                exc_info=True,
            )
            return 60


# Global singleton access
_service: Optional[CronService] = None


def get_cron_service() -> CronService:
    """Get or create the global CronService singleton."""
    global _service
    if _service is None:
        _service = CronService()
    return _service


def start_cron_service() -> None:
    """Start the cron service (called at CorvinOS boot)."""
    service = get_cron_service()
    service.start()


def stop_cron_service() -> None:
    """Stop the cron service (called at CorvinOS shutdown)."""
    service = get_cron_service()
    service.stop()
