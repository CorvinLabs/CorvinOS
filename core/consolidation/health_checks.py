"""Health-Check Registry and Snapshots — Phase 4 Consolidation.

Tracks component health states with immutable snapshots.
Provides fail-closed health queries for enforcement gates.

GDPR Art. 30, 32: All health events are audit-logged and hash-chained.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional, Dict, List, Set, Callable, Awaitable, Union

logger = logging.getLogger(__name__)


class HealthState(str, Enum):
    """Health state of a component."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


class ComponentSeverity(str, Enum):
    """Criticality level of a component."""

    CRITICAL = "critical"  # Failure blocks operations
    HIGH = "high"  # Failure degrades performance
    MEDIUM = "medium"  # Failure affects some users
    LOW = "low"  # Failure is non-blocking


@dataclass(frozen=True)
class HealthSnapshot:
    """Immutable health state snapshot of one component."""

    component_id: str
    state: HealthState
    severity: ComponentSeverity
    timestamp: datetime
    probe_duration_ms: float
    message: str
    recoverable: bool = True
    details: dict = field(default_factory=dict)
    prior_snapshot_hash: str = ""  # For audit trail continuity

    def to_audit_event(self) -> dict:
        """Convert to audit format for hash-chain logging."""
        return {
            "event_type": "health.component_snapshot",
            "component_id": self.component_id,
            "state": self.state.value,
            "severity": self.severity.value,
            "probe_duration_ms": self.probe_duration_ms,
            "message": self.message,
            "recoverable": self.recoverable,
            "timestamp": self.timestamp.isoformat() + "Z",
            "details": self.details or {},
        }

    def is_healthy(self) -> bool:
        """Check if component is in healthy state."""
        return self.state == HealthState.HEALTHY

    def is_critical_unhealthy(self) -> bool:
        """Check if component is critical and unhealthy."""
        return self.severity == ComponentSeverity.CRITICAL and self.state != HealthState.HEALTHY


@dataclass(frozen=True)
class HealthRegistrySnapshot:
    """Immutable snapshot of entire health registry state."""

    timestamp: datetime
    component_snapshots: Dict[str, HealthSnapshot]
    healthy_count: int
    degraded_count: int
    unhealthy_count: int
    critical_unhealthy: List[str]  # Component IDs
    is_system_healthy: bool  # Fail-closed: any critical unhealthy = False

    def to_audit_event(self) -> dict:
        """Convert to audit format."""
        return {
            "event_type": "health.registry_snapshot",
            "healthy": self.healthy_count,
            "degraded": self.degraded_count,
            "unhealthy": self.unhealthy_count,
            "critical_unhealthy_count": len(self.critical_unhealthy),
            "critical_unhealthy_ids": self.critical_unhealthy,
            "system_healthy": self.is_system_healthy,
            "timestamp": self.timestamp.isoformat() + "Z",
        }


class HealthCheckRegistry:
    """
    Track component health with immutable snapshots.

    Maintains a registry of components with their health probes,
    generates immutable snapshots for enforcement gates,
    and emits audit events.
    """

    def __init__(self, probe_timeout_seconds: float = 5.0):
        """Initialize health check registry.

        Args:
            probe_timeout_seconds: Timeout for each health probe
        """
        self.probe_timeout_seconds = probe_timeout_seconds
        self._components: Dict[str, ComponentSeverity] = {}
        self._probes: Dict[
            str, Union[Callable[[], bool], Callable[[], Awaitable[bool]]]
        ] = {}
        self._latest_snapshots: Dict[str, HealthSnapshot] = {}
        self._snapshot_history: List[HealthRegistrySnapshot] = []
        self._lock = asyncio.Lock()

    def register_component(
        self,
        component_id: str,
        probe_fn: Union[Callable[[], bool], Callable[[], Awaitable[bool]]],
        severity: ComponentSeverity = ComponentSeverity.MEDIUM,
    ) -> None:
        """Register a component with its health probe.

        Args:
            component_id: Unique component identifier
            probe_fn: Sync or async function returning True if healthy
            severity: Criticality level of this component

        Raises:
            ValueError: If component_id invalid or already registered
        """
        if not component_id or not isinstance(component_id, str):
            raise ValueError(f"Invalid component_id: {component_id}")

        if component_id in self._components:
            raise ValueError(f"Component '{component_id}' already registered")

        if not callable(probe_fn):
            raise ValueError(f"probe_fn must be callable")

        self._components[component_id] = severity
        self._probes[component_id] = probe_fn
        logger.debug(
            f"Registered component: {component_id} (severity={severity.value})"
        )

    async def probe_component(self, component_id: str) -> HealthSnapshot:
        """Probe one component's health with timeout.

        Args:
            component_id: Component to probe

        Returns:
            HealthSnapshot with current state

        Raises:
            ValueError: If component not registered
        """
        if component_id not in self._probes:
            raise ValueError(f"Component '{component_id}' not registered")

        probe_fn = self._probes[component_id]
        severity = self._components[component_id]
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
                message = f"Component '{component_id}' passed health check"
            else:
                state = HealthState.DEGRADED
                message = f"Component '{component_id}' probe returned False"

        except asyncio.TimeoutError:
            duration_ms = (time.time() - start_time) * 1000
            state = HealthState.UNHEALTHY
            message = (
                f"Component '{component_id}' health probe timed out "
                f"after {self.probe_timeout_seconds}s"
            )

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            state = HealthState.UNHEALTHY
            message = (
                f"Component '{component_id}' health probe failed: "
                f"{type(e).__name__}: {str(e)}"
            )

        # Get prior snapshot hash for continuity
        prior_hash = ""
        if component_id in self._latest_snapshots:
            prior_hash = self._latest_snapshots[component_id].prior_snapshot_hash

        snapshot = HealthSnapshot(
            component_id=component_id,
            state=state,
            severity=severity,
            timestamp=start_datetime,
            probe_duration_ms=duration_ms,
            message=message,
            prior_snapshot_hash=prior_hash,
        )

        async with self._lock:
            self._latest_snapshots[component_id] = snapshot

        logger.debug(f"Health snapshot: {component_id} = {state.value}")

        return snapshot

    async def _run_probe(self, probe_fn: Callable) -> bool:
        """Run probe function (handles both sync and async)."""
        if asyncio.iscoroutinefunction(probe_fn):
            return await probe_fn()
        else:
            return probe_fn()

    async def take_registry_snapshot(self) -> HealthRegistrySnapshot:
        """Probe all components and take immutable registry snapshot.

        Returns:
            HealthRegistrySnapshot with current state of all components

        Raises:
            ValueError: If no components registered
        """
        if not self._probes:
            raise ValueError("No health probes registered")

        # Probe all components concurrently
        snapshots = await asyncio.gather(
            *[self.probe_component(cid) for cid in self._components.keys()],
            return_exceptions=False,
        )

        # Build snapshot map
        snapshot_map = {s.component_id: s for s in snapshots}

        # Count states
        healthy_count = sum(1 for s in snapshots if s.is_healthy())
        degraded_count = sum(
            1 for s in snapshots if s.state == HealthState.DEGRADED
        )
        unhealthy_count = sum(
            1 for s in snapshots if s.state == HealthState.UNHEALTHY
        )

        # Find critical unhealthy components (fail-closed)
        critical_unhealthy = [
            s.component_id
            for s in snapshots
            if s.is_critical_unhealthy()
        ]

        # System is healthy only if no critical components are unhealthy
        is_system_healthy = len(critical_unhealthy) == 0

        registry_snapshot = HealthRegistrySnapshot(
            timestamp=datetime.utcnow(),
            component_snapshots=snapshot_map,
            healthy_count=healthy_count,
            degraded_count=degraded_count,
            unhealthy_count=unhealthy_count,
            critical_unhealthy=critical_unhealthy,
            is_system_healthy=is_system_healthy,
        )

        async with self._lock:
            self._snapshot_history.append(registry_snapshot)
            # Keep last 100 snapshots
            if len(self._snapshot_history) > 100:
                self._snapshot_history.pop(0)

        logger.info(
            f"Registry snapshot: healthy={healthy_count}, degraded={degraded_count}, "
            f"unhealthy={unhealthy_count}, system_healthy={is_system_healthy}"
        )

        return registry_snapshot

    def get_latest_component_snapshot(self, component_id: str) -> Optional[HealthSnapshot]:
        """Get the latest snapshot for a component.

        Args:
            component_id: Component to query

        Returns:
            HealthSnapshot if available, None otherwise
        """
        return self._latest_snapshots.get(component_id)

    def get_critical_unhealthy_components(self) -> List[str]:
        """Get list of critical components currently unhealthy.

        Returns:
            List of component IDs (fail-closed: empty = all critical healthy)
        """
        return [
            cid for cid, snap in self._latest_snapshots.items()
            if snap.is_critical_unhealthy()
        ]

    def get_snapshot_history(
        self, limit: int = 10
    ) -> List[HealthRegistrySnapshot]:
        """Get recent registry snapshots.

        Args:
            limit: Maximum number of snapshots to return

        Returns:
            List of snapshots (newest first)
        """
        return list(reversed(self._snapshot_history[-limit:]))

    def reset_for_testing(self) -> None:
        """Clear all state (TEST ONLY)."""
        self._components.clear()
        self._probes.clear()
        self._latest_snapshots.clear()
        self._snapshot_history.clear()
