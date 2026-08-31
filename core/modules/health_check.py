"""Health Check Engine (ADR-0327).

Concurrent per-module health probes with aggregate system health.
Fail-closed: any probe failure marks module degraded, never suppressed.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Awaitable, Callable, Optional, Union

logger = logging.getLogger(__name__)


class HealthState(str, Enum):
    """Health state of a module."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ModuleHealthReport:
    """Health status of one module."""

    module_id: str
    state: HealthState
    last_probe_time: datetime
    probe_duration_ms: float
    message: str
    recoverable: bool = True
    details: dict = field(default_factory=dict)

    def to_audit_event(self) -> dict:
        """Convert to audit format."""
        return {
            "event_type": "health.module_status",
            "module_id": self.module_id,
            "state": self.state.value,
            "probe_duration_ms": self.probe_duration_ms,
            "message": self.message,
            "recoverable": self.recoverable,
            "timestamp": self.last_probe_time.isoformat() + "Z",
        }


@dataclass(frozen=True)
class SystemHealthReport:
    """Aggregate health status of all modules."""

    overall_state: HealthState
    timestamp: datetime
    module_reports: list[ModuleHealthReport]
    healthy_count: int
    degraded_count: int
    unhealthy_count: int

    def to_audit_event(self) -> dict:
        """Convert to audit format."""
        return {
            "event_type": "health.system_status",
            "overall_state": self.overall_state.value,
            "healthy": self.healthy_count,
            "degraded": self.degraded_count,
            "unhealthy": self.unhealthy_count,
            "timestamp": self.timestamp.isoformat() + "Z",
        }


class HealthCheckEngine:
    """Probes module health with timeout and concurrency safety."""

    def __init__(self, probe_timeout_seconds: float = 5.0):
        """Initialize health check engine.

        Args:
            probe_timeout_seconds: Timeout for each probe
        """
        self.probe_timeout_seconds = probe_timeout_seconds
        self._probes: dict[str, Union[Callable[[], bool], Callable[[], Awaitable[bool]]]] = {}
        self._last_reports: dict[str, ModuleHealthReport] = {}

    def register_probe(
        self, module_id: str, probe_fn: Union[Callable[[], bool], Callable[[], Awaitable[bool]]]
    ) -> None:
        """Register a health probe for a module.

        Args:
            module_id: Module identifier
            probe_fn: Sync or async function that returns True if healthy

        Raises:
            ValueError: If module_id invalid
        """
        if not module_id or not isinstance(module_id, str):
            raise ValueError(f"Invalid module_id: {module_id}")

        if not callable(probe_fn):
            raise ValueError(f"probe_fn must be callable")

        self._probes[module_id] = probe_fn
        logger.debug(f"Registered health probe for module: {module_id}")

    async def probe_module_health(self, module_id: str) -> ModuleHealthReport:
        """Probe one module's health with timeout.

        Args:
            module_id: Module to probe

        Returns:
            ModuleHealthReport with state

        Raises:
            ValueError: If module not registered
        """
        if module_id not in self._probes:
            raise ValueError(f"Module '{module_id}' not registered")

        probe_fn = self._probes[module_id]
        start_time = time.time()
        start_datetime = datetime.utcnow()

        try:
            # Run probe with timeout
            result = await asyncio.wait_for(
                self._run_probe(probe_fn),
                timeout=self.probe_timeout_seconds,
            )

            duration_ms = (time.time() - start_time) * 1000

            if result:
                state = HealthState.HEALTHY
                message = f"Module '{module_id}' passed health check"
            else:
                state = HealthState.DEGRADED
                message = f"Module '{module_id}' probe returned False"

        except asyncio.TimeoutError:
            duration_ms = (time.time() - start_time) * 1000
            state = HealthState.UNHEALTHY
            message = f"Module '{module_id}' health probe timed out after {self.probe_timeout_seconds}s"

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            state = HealthState.UNHEALTHY
            message = f"Module '{module_id}' health probe failed: {type(e).__name__}: {str(e)}"

        report = ModuleHealthReport(
            module_id=module_id,
            state=state,
            last_probe_time=start_datetime,
            probe_duration_ms=duration_ms,
            message=message,
        )

        self._last_reports[module_id] = report
        logger.debug(f"Health probe: {report}")

        return report

    async def _run_probe(self, probe_fn: Callable) -> bool:
        """Run probe (handles both sync and async)."""
        if asyncio.iscoroutinefunction(probe_fn):
            return await probe_fn()
        else:
            return probe_fn()

    async def aggregate_health_state(self) -> SystemHealthReport:
        """Probe all registered modules and compute system health.

        Returns:
            SystemHealthReport with aggregate state

        Raises:
            ValueError: If no modules registered
        """
        if not self._probes:
            raise ValueError("No health probes registered")

        # Run all probes concurrently
        reports = await asyncio.gather(
            *[self.probe_module_health(mid) for mid in self._probes.keys()],
            return_exceptions=False,
        )

        # Count states
        healthy_count = sum(1 for r in reports if r.state == HealthState.HEALTHY)
        degraded_count = sum(1 for r in reports if r.state == HealthState.DEGRADED)
        unhealthy_count = sum(1 for r in reports if r.state == HealthState.UNHEALTHY)

        # Compute overall state (fail-closed: any unhealthy = system unhealthy)
        if unhealthy_count > 0:
            overall_state = HealthState.UNHEALTHY
        elif degraded_count > 0:
            overall_state = HealthState.DEGRADED
        else:
            overall_state = HealthState.HEALTHY

        report = SystemHealthReport(
            overall_state=overall_state,
            timestamp=datetime.utcnow(),
            module_reports=reports,
            healthy_count=healthy_count,
            degraded_count=degraded_count,
            unhealthy_count=unhealthy_count,
        )

        logger.info(
            f"System health: {overall_state.value} "
            f"(healthy={healthy_count}, degraded={degraded_count}, unhealthy={unhealthy_count})"
        )

        return report

    def get_unhealthy_modules(self) -> list[str]:
        """Return list of currently unhealthy modules.

        Returns:
            List of module IDs with state != HEALTHY
        """
        return [
            mid
            for mid, report in self._last_reports.items()
            if report.state in (HealthState.DEGRADED, HealthState.UNHEALTHY)
        ]

    def reset_for_testing(self) -> None:
        """Clear all state (TEST ONLY)."""
        self._probes.clear()
        self._last_reports.clear()
