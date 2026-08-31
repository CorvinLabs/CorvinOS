"""
Real-time health monitoring for CorvinOS subsystems.

Emits health status events (<100ms latency) for:
- Brain subsystem (learning, routing decisions)
- ContextBridge (state management)
- LoopEngineer (task orchestration)
- Orchestrator (engine selection)
- Plugin system (sandbox health)
- Offline mode (queue size, sync status)

Events streamed via WebSocket to dashboard.
"""

from dataclasses import dataclass, field
from typing import Dict, Optional, List
from datetime import datetime
from enum import Enum
import asyncio


class HealthStatus(Enum):
    """Health status levels."""
    OK = "ok"
    DEGRADED = "degraded"
    ERROR = "error"
    OFFLINE = "offline"


@dataclass
class HealthMetric:
    """Single health metric."""
    name: str
    value: float
    unit: str = ""  # e.g., "ms", "%", "count"
    threshold_warning: Optional[float] = None
    threshold_error: Optional[float] = None


@dataclass
class SubsystemHealth:
    """Health status of a subsystem."""
    subsystem_id: str
    status: HealthStatus
    timestamp: datetime = field(default_factory=datetime.utcnow)
    metrics: List[HealthMetric] = field(default_factory=list)
    message: str = ""
    last_error: Optional[str] = None

    def to_dict(self) -> Dict:
        """Serialize for WebSocket."""
        return {
            "subsystem_id": self.subsystem_id,
            "status": self.status.value,
            "timestamp": self.timestamp.isoformat(),
            "metrics": [
                {"name": m.name, "value": m.value, "unit": m.unit}
                for m in self.metrics
            ],
            "message": self.message,
            "last_error": self.last_error,
        }


class HealthMonitor:
    """
    Monitor subsystem health and emit events.

    Usage:
    1. Subsystems report metrics periodically
    2. Monitor computes health status
    3. Health events broadcast to WebSocket
    """

    def __init__(self):
        """Initialize health monitor."""
        self.subsystems: Dict[str, SubsystemHealth] = {}
        self.event_queue: asyncio.Queue = asyncio.Queue()

    async def report_health(
        self,
        subsystem_id: str,
        status: HealthStatus,
        metrics: List[HealthMetric],
        message: str = "",
    ) -> None:
        """
        Report subsystem health status.

        Args:
            subsystem_id: Subsystem name
            status: Current health status
            metrics: List of metrics
            message: Optional status message
        """
        health = SubsystemHealth(
            subsystem_id=subsystem_id,
            status=status,
            metrics=metrics,
            message=message,
        )
        self.subsystems[subsystem_id] = health

        # Emit event
        await self.event_queue.put({
            "type": "health_status",
            "data": health.to_dict(),
        })

    def get_overall_status(self) -> HealthStatus:
        """Get overall system health."""
        statuses = [s.status for s in self.subsystems.values()]

        if HealthStatus.ERROR in statuses:
            return HealthStatus.ERROR
        elif HealthStatus.DEGRADED in statuses:
            return HealthStatus.DEGRADED
        elif HealthStatus.OFFLINE in statuses and len(statuses) < 3:
            return HealthStatus.DEGRADED
        else:
            return HealthStatus.OK

    async def get_next_event(self, timeout: float = 1.0) -> Optional[Dict]:
        """
        Get next health event (for WebSocket streaming).

        Returns None on timeout.
        """
        try:
            return await asyncio.wait_for(
                self.event_queue.get(),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            return None

    def get_snapshot(self) -> Dict:
        """Get current health snapshot."""
        return {
            "timestamp": datetime.utcnow().isoformat(),
            "overall_status": self.get_overall_status().value,
            "subsystems": {
                sid: s.to_dict()
                for sid, s in self.subsystems.items()
            },
        }
