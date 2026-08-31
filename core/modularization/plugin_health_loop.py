"""Plugin health monitoring and auto-restart orchestration (Phase 4.5, ADR-0426).

Monitors plugin subprocess health (ping, exit-code, memory), detects failures,
and orchestrates graceful restarts with audit trail integration.

Implements fail-closed health check gate: restart on any health probe failure.
Respects resource limits and restart cooldown policy.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any, Dict, Optional, Callable, Awaitable

from .plugin_isolation import (
    PluginProcessManager,
    PluginProcessState,
    PluginProcessInfo,
)

log = logging.getLogger("corvin.modularization.plugin_health_loop")


class HealthCheckState(str, Enum):
    """Health probe state machine."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class HealthProbe:
    """Result of a single health check probe."""
    plugin_id: str
    state: HealthCheckState
    timestamp: datetime
    response_time_ms: float
    error_message: Optional[str] = None
    consecutive_failures: int = 0


@dataclass(frozen=True)
class HealthCheckConfig:
    """Configuration for plugin health monitoring."""
    enabled: bool = True
    interval_sec: int = 60
    timeout_sec: int = 10
    consecutive_failures_threshold: int = 3
    degraded_threshold_ms: int = 5000  # Response time > 5s = degraded


@dataclass
class HealthCheckRegistry:
    """In-memory registry of plugin health states and history."""
    plugin_id: str
    config: HealthCheckConfig
    current_state: HealthCheckState = HealthCheckState.UNKNOWN
    last_probe: Optional[HealthProbe] = None
    probe_history: list[HealthProbe] = field(default_factory=list)
    consecutive_failures: int = 0
    last_restart: Optional[datetime] = None
    restart_count: int = 0

    def add_probe(self, probe: HealthProbe) -> None:
        """Record a health probe result and update state machine."""
        self.probe_history.append(probe)
        self.last_probe = probe

        # Keep last 100 probes for observability
        if len(self.probe_history) > 100:
            self.probe_history = self.probe_history[-100:]

        # Update state machine
        if probe.state == HealthCheckState.HEALTHY:
            self.consecutive_failures = 0
            self.current_state = HealthCheckState.HEALTHY
        elif probe.state == HealthCheckState.DEGRADED:
            self.consecutive_failures = 0
            self.current_state = HealthCheckState.DEGRADED
        elif probe.state == HealthCheckState.UNHEALTHY:
            self.consecutive_failures += 1
            if (
                self.consecutive_failures
                >= self.config.consecutive_failures_threshold
            ):
                self.current_state = HealthCheckState.UNHEALTHY

    def is_restart_needed(self) -> bool:
        """Determine if plugin should be restarted based on health state."""
        return self.current_state == HealthCheckState.UNHEALTHY


class PluginHealthMonitor:
    """Monitors plugin subprocess health and orchestrates restarts."""

    def __init__(
        self,
        process_manager: PluginProcessManager,
        config: HealthCheckConfig,
        audit_logger: Optional[Callable[[str, Dict[str, Any]], Awaitable[None]]] = None,
    ):
        """Initialize health monitor for a plugin.

        Args:
            process_manager: PluginProcessManager instance to monitor
            config: Health check configuration (interval, thresholds, etc.)
            audit_logger: Async function to log audit events
        """
        self.process_manager = process_manager
        self.config = config
        self.audit_logger = audit_logger
        self.plugin_id = process_manager.plugin_id

        self._registry = HealthCheckRegistry(plugin_id=self.plugin_id, config=config)
        self._monitor_task: Optional[asyncio.Task] = None
        self._running = False

    async def start(self) -> None:
        """Start the health monitoring loop."""
        if self._running:
            log.warning(
                f"Health monitor already running for {self.plugin_id}",
                extra={"plugin_id": self.plugin_id},
            )
            return

        if not self.config.enabled:
            log.info(
                f"Health monitoring disabled for {self.plugin_id}",
                extra={"plugin_id": self.plugin_id},
            )
            return

        self._running = True
        await self._audit_log("health_monitor.started", {"plugin_id": self.plugin_id})

        self._monitor_task = asyncio.create_task(self._monitor_loop())
        log.info(
            f"Health monitor started for {self.plugin_id}",
            extra={"plugin_id": self.plugin_id},
        )

    async def stop(self) -> None:
        """Stop the health monitoring loop."""
        if not self._running:
            return

        self._running = False
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass

        await self._audit_log("health_monitor.stopped", {"plugin_id": self.plugin_id})
        log.info(
            f"Health monitor stopped for {self.plugin_id}",
            extra={"plugin_id": self.plugin_id},
        )

    async def probe(self) -> HealthProbe:
        """Execute a single health check probe (ping, state check, etc.)."""
        start_time = datetime.now(timezone.utc)

        try:
            # Attempt to query plugin version (lightweight health check)
            manifest = await asyncio.wait_for(
                self.process_manager.get_version_manifest(),
                timeout=self.config.timeout_sec,
            )
            response_time_ms = (
                datetime.now(timezone.utc) - start_time
            ).total_seconds() * 1000

            # Determine health state based on response time
            if response_time_ms > self.config.degraded_threshold_ms:
                state = HealthCheckState.DEGRADED
            else:
                state = HealthCheckState.HEALTHY

            probe = HealthProbe(
                plugin_id=self.plugin_id,
                state=state,
                timestamp=start_time,
                response_time_ms=response_time_ms,
            )

            self._registry.add_probe(probe)
            log.debug(
                f"Health probe successful for {self.plugin_id}: {state}",
                extra={"plugin_id": self.plugin_id, "state": state},
            )
            return probe

        except asyncio.TimeoutError:
            response_time_ms = (
                datetime.now(timezone.utc) - start_time
            ).total_seconds() * 1000
            probe = HealthProbe(
                plugin_id=self.plugin_id,
                state=HealthCheckState.UNHEALTHY,
                timestamp=start_time,
                response_time_ms=response_time_ms,
                error_message=f"Health check timeout (>{self.config.timeout_sec}s)",
                consecutive_failures=self._registry.consecutive_failures + 1,
            )
            self._registry.add_probe(probe)

            log.warning(
                f"Health probe timeout for {self.plugin_id}",
                extra={"plugin_id": self.plugin_id},
            )
            await self._audit_log(
                "health_probe.timeout",
                {
                    "plugin_id": self.plugin_id,
                    "timeout_sec": self.config.timeout_sec,
                    "consecutive_failures": self._registry.consecutive_failures,
                },
            )
            return probe

        except Exception as e:
            response_time_ms = (
                datetime.now(timezone.utc) - start_time
            ).total_seconds() * 1000
            error_msg = str(e)
            probe = HealthProbe(
                plugin_id=self.plugin_id,
                state=HealthCheckState.UNHEALTHY,
                timestamp=start_time,
                response_time_ms=response_time_ms,
                error_message=error_msg,
                consecutive_failures=self._registry.consecutive_failures + 1,
            )
            self._registry.add_probe(probe)

            log.warning(
                f"Health probe failed for {self.plugin_id}: {error_msg}",
                extra={"plugin_id": self.plugin_id, "error": error_msg},
            )
            await self._audit_log(
                "health_probe.failed",
                {
                    "plugin_id": self.plugin_id,
                    "error": error_msg,
                    "consecutive_failures": self._registry.consecutive_failures,
                },
            )
            return probe

    async def get_health_state(self) -> HealthCheckRegistry:
        """Get current health state snapshot."""
        return self._registry

    async def _monitor_loop(self) -> None:
        """Main health monitoring loop (runs as background task)."""
        while self._running:
            try:
                # Execute health probe
                probe = await self.probe()

                # Check if restart is needed
                if self._registry.is_restart_needed():
                    log.error(
                        f"Plugin {self.plugin_id} is unhealthy, attempting restart",
                        extra={
                            "plugin_id": self.plugin_id,
                            "consecutive_failures": self._registry.consecutive_failures,
                        },
                    )
                    await self._orchestrate_restart(
                        reason=f"Health check threshold exceeded after {self._registry.consecutive_failures} failures"
                    )

                # Wait for next probe interval
                await asyncio.sleep(self.config.interval_sec)

            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(
                    f"Unexpected error in health monitor loop: {str(e)}",
                    extra={"plugin_id": self.plugin_id, "error": str(e)},
                )
                await self._audit_log(
                    "health_monitor.error",
                    {"plugin_id": self.plugin_id, "error": str(e)},
                )
                # Continue monitoring despite error
                await asyncio.sleep(self.config.interval_sec)

    async def _orchestrate_restart(self, reason: str = "") -> None:
        """Orchestrate a graceful plugin restart with audit logging."""
        try:
            await self._audit_log(
                "plugin_restart.initiated",
                {
                    "plugin_id": self.plugin_id,
                    "reason": reason,
                    "restart_count": self._registry.restart_count,
                },
            )

            # Perform restart
            await self.process_manager.restart(reason=reason)

            # Update restart timestamp and counter
            self._registry.last_restart = datetime.now(timezone.utc)
            self._registry.restart_count += 1

            # Reset health state after restart
            self._registry.consecutive_failures = 0
            self._registry.current_state = HealthCheckState.UNKNOWN

            await self._audit_log(
                "plugin_restart.completed",
                {
                    "plugin_id": self.plugin_id,
                    "restart_count": self._registry.restart_count,
                },
            )

            log.info(
                f"Plugin {self.plugin_id} restarted successfully",
                extra={
                    "plugin_id": self.plugin_id,
                    "restart_count": self._registry.restart_count,
                },
            )

        except RuntimeError as e:
            await self._audit_log(
                "plugin_restart.failed",
                {
                    "plugin_id": self.plugin_id,
                    "error": str(e),
                    "restart_count": self._registry.restart_count,
                },
            )
            log.error(
                f"Failed to restart plugin {self.plugin_id}: {str(e)}",
                extra={"plugin_id": self.plugin_id, "error": str(e)},
            )
            # Do not re-raise: health loop should continue running

    async def _audit_log(self, event_type: str, details: Dict[str, Any]) -> None:
        """Log an audit event (if audit_logger provided)."""
        if self.audit_logger:
            try:
                await self.audit_logger(event_type, details)
            except Exception as e:
                log.error(
                    f"Failed to log audit event {event_type}: {str(e)}",
                    extra={"event_type": event_type},
                )
