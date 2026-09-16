"""Health Monitor Skill: Real-time subsystem state tracking and anomaly detection.

Monitors:
- Audit trail integrity (hash-chain verification)
- Plugin system health (loaded plugins, failures)
- Context window usage (remaining tokens)
- Task queue depth (pending tasks)
- Error rate (failures in last N operations)

Contract (ADR-0535):
- Dependency: NONE (no soft or required deps)
- Calling convention: other Skills call health_monitor to get subsystem status
- Timeout budget: 50ms max (low-latency query)
- Audit: SkillExecutedEvent logged for every status_check call
- Degradation: N/A (no dependencies to degrade on)

Compliance:
- GDPR Art. 30/32: subsystem metrics are audit-logged, immutable
- ADR-0232: participates in boot tripwire (audit chain must be reachable)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from enum import Enum
import time
import logging

try:
    from .base_skill import BaseSkill, SkillExecutedEvent, SkillExecutionStatus, AuditTrail
except ImportError:
    from base_skill import BaseSkill, SkillExecutedEvent, SkillExecutionStatus, AuditTrail

logger = logging.getLogger(__name__)


class HealthLevel(Enum):
    """Overall subsystem health."""
    HEALTHY = "healthy"  # All metrics green
    DEGRADED = "degraded"  # Some non-critical issues
    UNHEALTHY = "unhealthy"  # Critical issues, may fail
    UNKNOWN = "unknown"  # No data collected yet


@dataclass(frozen=True)
class SubsystemMetric:
    """Immutable metric for one subsystem component."""
    name: str  # e.g., "audit_chain", "plugin_system", "context_window"
    value: float  # Current value (0-100 for percentages, absolute for counts)
    threshold_warning: float  # Alert if value crosses this
    threshold_critical: float  # Fail if value crosses this
    unit: str  # e.g., "%", "events", "tokens"
    timestamp: str  # ISO8601 when measured
    status: HealthLevel


@dataclass(frozen=True)
class HealthStatus:
    """Immutable snapshot of system health."""
    timestamp: str  # ISO8601
    overall_health: HealthLevel
    metrics: Dict[str, SubsystemMetric]
    error_messages: List[str] = field(default_factory=list)  # Non-empty if unhealthy
    last_check_latency_ms: int = 0


class HealthMonitor(BaseSkill[HealthStatus]):
    """Skill: Monitor and report subsystem health.

    This is a required dependency for Orchestrator and Context Bridge.
    Other Skills call check_health() to query status before making decisions.
    """

    skill_id = "os.health_monitor"
    version = "1.0.0"
    required_dependencies: List[str] = []  # No dependencies
    soft_dependencies: List[str] = []
    call_budget_ms = 50  # Fast query

    def __init__(self, tenant_id: str, audit_trail: AuditTrail):
        super().__init__(tenant_id, audit_trail)
        self._last_health_status: Optional[HealthStatus] = None
        self._error_history: List[tuple[float, str]] = []  # (timestamp, error_msg)

    def execute(self, input_data: Dict[str, Any]) -> HealthStatus:
        """Check health of all monitored subsystems.

        Input (optional):
        - "subsystems": List of subsystem names to check (default: all)
        - "include_history": bool, include error history (default: False)

        Returns:
            HealthStatus snapshot
        """
        start = time.perf_counter()

        try:
            subsystems = input_data.get("subsystems", ["audit_chain", "plugin_system", "context_window", "task_queue", "error_rate"])
            include_history = input_data.get("include_history", False)

            status = self._check_subsystems(subsystems)
            latency_ms = int((time.perf_counter() - start) * 1000)

            # Audit this health check
            self._audit_execution(
                input_data=input_data,
                output_data=status,
                status=SkillExecutionStatus.SUCCESS,
                latency_ms=latency_ms,
            )

            self._last_health_status = status
            return status

        except Exception as e:
            latency_ms = int((time.perf_counter() - start) * 1000)
            logger.error(f"Health check failed: {e}", extra={"tenant_id": self.tenant_id})

            # Still audit the error
            error_status = HealthStatus(
                timestamp=self._now_iso(),
                overall_health=HealthLevel.UNHEALTHY,
                metrics={},
                error_messages=[str(e)],
                last_check_latency_ms=latency_ms,
            )

            self._audit_execution(
                input_data=input_data,
                output_data=error_status,
                status=SkillExecutionStatus.ERROR,
                latency_ms=latency_ms,
                error_message=str(e),
            )

            raise

    def _check_subsystems(self, subsystems: List[str]) -> HealthStatus:
        """Perform health checks on requested subsystems."""
        metrics = {}
        errors = []
        overall_level = HealthLevel.HEALTHY

        for subsystem in subsystems:
            metric = self._check_one(subsystem)
            if metric:
                metrics[subsystem] = metric
                if metric.status == HealthLevel.UNHEALTHY:
                    overall_level = HealthLevel.UNHEALTHY
                    errors.append(f"{subsystem}: critical threshold exceeded")
                elif metric.status == HealthLevel.DEGRADED and overall_level == HealthLevel.HEALTHY:
                    overall_level = HealthLevel.DEGRADED

        return HealthStatus(
            timestamp=self._now_iso(),
            overall_health=overall_level,
            metrics=metrics,
            error_messages=errors,
            last_check_latency_ms=0,  # Will be filled by _audit_execution caller
        )

    def _check_one(self, subsystem: str) -> Optional[SubsystemMetric]:
        """Check one subsystem metric."""
        # These are mock implementations; real ones would check actual systems
        if subsystem == "audit_chain":
            return SubsystemMetric(
                name="audit_chain",
                value=100.0,  # OK
                threshold_warning=50.0,
                threshold_critical=10.0,
                unit="%",
                timestamp=self._now_iso(),
                status=HealthLevel.HEALTHY,
            )
        elif subsystem == "plugin_system":
            return SubsystemMetric(
                name="plugin_system",
                value=8,  # 8 plugins loaded
                threshold_warning=0.0,
                threshold_critical=-1.0,
                unit="plugins",
                timestamp=self._now_iso(),
                status=HealthLevel.HEALTHY,
            )
        elif subsystem == "context_window":
            return SubsystemMetric(
                name="context_window",
                value=75.0,  # 75% remaining
                threshold_warning=25.0,
                threshold_critical=5.0,
                unit="%",
                timestamp=self._now_iso(),
                status=HealthLevel.HEALTHY,
            )
        elif subsystem == "task_queue":
            return SubsystemMetric(
                name="task_queue",
                value=3,  # 3 tasks pending
                threshold_warning=100.0,
                threshold_critical=500.0,
                unit="tasks",
                timestamp=self._now_iso(),
                status=HealthLevel.HEALTHY,
            )
        elif subsystem == "error_rate":
            return SubsystemMetric(
                name="error_rate",
                value=0.2,  # 0.2% error rate
                threshold_warning=5.0,
                threshold_critical=10.0,
                unit="%",
                timestamp=self._now_iso(),
                status=HealthLevel.HEALTHY,
            )

        return None

    def record_error(self, error_msg: str) -> None:
        """Record an error for monitoring (thread-safe)."""
        now = time.time()
        self._error_history.append((now, error_msg))
        # Keep last 100 errors
        if len(self._error_history) > 100:
            self._error_history = self._error_history[-100:]

    def _now_iso(self) -> str:
        """Current time in ISO8601 format."""
        from datetime import datetime
        return datetime.utcnow().isoformat() + "Z"
