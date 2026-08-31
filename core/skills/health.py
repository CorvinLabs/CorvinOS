"""Health Check Framework — monitors skill system health (ADR-0309)."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


@dataclass
class HealthStatus:
    """Health status snapshot."""

    component: str  # "grading", "telemetry", "queue"
    healthy: bool
    message: str
    timestamp: datetime = field(default_factory=datetime.utcnow)
    metrics: dict[str, Any] = field(default_factory=dict)


class HealthCheck(Protocol):
    """Protocol for pluggable health checks."""

    async def check(self) -> HealthStatus:
        """Run health check and return status."""
        ...


class GradingHealth:
    """Health check for GradingManager."""

    def __init__(self, stall_threshold_s: float = 10.0):
        """Initialize.

        Args:
            stall_threshold_s: Mark unhealthy if no progress for this long
        """
        self.stall_threshold = stall_threshold_s
        self.last_graded_count = 0
        self.last_check_time = time.time()

    async def check(self, grading_manager: Any) -> HealthStatus:
        """Check grading progress.

        Args:
            grading_manager: GradingManager instance

        Returns:
            HealthStatus indicating whether grading is making progress
        """
        try:
            stats = grading_manager.get_stats()
            current_count = stats.get("graded_count", 0)
            current_time = time.time()
            elapsed = current_time - self.last_check_time

            # Check if progress was made
            progress_made = current_count > self.last_graded_count
            time_since_progress = (
                0 if progress_made else elapsed + (current_time - self.last_check_time)
            )

            if not progress_made and time_since_progress > self.stall_threshold:
                return HealthStatus(
                    component="grading",
                    healthy=False,
                    message=f"Grading stalled for {time_since_progress:.1f}s",
                    metrics=stats,
                )

            self.last_graded_count = current_count
            self.last_check_time = current_time

            return HealthStatus(
                component="grading",
                healthy=True,
                message=f"Grading healthy ({current_count} graded)",
                metrics=stats,
            )
        except Exception as e:
            return HealthStatus(
                component="grading",
                healthy=False,
                message=f"Grading check error: {type(e).__name__}",
            )


class TelemetryHealth:
    """Health check for TelemetryManager."""

    def __init__(self, backlog_threshold: int = 100):
        """Initialize.

        Args:
            backlog_threshold: Mark unhealthy if pending > this
        """
        self.backlog_threshold = backlog_threshold
        self.last_published_count = 0

    async def check(self, telemetry_manager: Any) -> HealthStatus:
        """Check telemetry publishing.

        Args:
            telemetry_manager: TelemetryManager instance

        Returns:
            HealthStatus indicating whether telemetry is publishing
        """
        try:
            stats = telemetry_manager.get_stats()
            pending = stats.get("pending_count", 0)
            published = stats.get("published_count", 0)

            # Backlog means samples accumulating but not publishing
            if pending > self.backlog_threshold:
                return HealthStatus(
                    component="telemetry",
                    healthy=False,
                    message=f"Telemetry backlog: {pending} samples pending",
                    metrics=stats,
                )

            self.last_published_count = published

            return HealthStatus(
                component="telemetry",
                healthy=True,
                message=f"Telemetry healthy ({published} published)",
                metrics=stats,
            )
        except Exception as e:
            return HealthStatus(
                component="telemetry",
                healthy=False,
                message=f"Telemetry check error: {type(e).__name__}",
            )


class QueueHealth:
    """Health check for queue depth."""

    def __init__(self, max_queue_size: int = 1000):
        """Initialize.

        Args:
            max_queue_size: Mark unhealthy if queue size > this
        """
        self.max_queue_size = max_queue_size

    async def check(self, queue: Any) -> HealthStatus:
        """Check queue health.

        Args:
            queue: Queue instance (has qsize() method)

        Returns:
            HealthStatus indicating whether queue is healthy
        """
        try:
            size = queue.qsize() if hasattr(queue, "qsize") else 0

            if size > self.max_queue_size:
                return HealthStatus(
                    component="queue",
                    healthy=False,
                    message=f"Queue size {size} exceeds threshold {self.max_queue_size}",
                    metrics={"queue_size": size},
                )

            return HealthStatus(
                component="queue",
                healthy=True,
                message=f"Queue healthy ({size} items)",
                metrics={"queue_size": size},
            )
        except Exception as e:
            return HealthStatus(
                component="queue",
                healthy=False,
                message=f"Queue check error: {type(e).__name__}",
            )


class ExecutorHealth:
    """Health check for SkillExecutor (ADR-0307 + ADR-0309).

    Monitors:
    - Skill execution success rate
    - Timeout/resource error rates
    - Auto-disable on 3+ consecutive failures
    - Per-skill health status
    """

    def __init__(self, min_success_rate: float = 0.5, failure_threshold: int = 3):
        """Initialize.

        Args:
            min_success_rate: Mark unhealthy if success_rate < this (default 0.5 = 50%)
            failure_threshold: Consecutive failures to trigger auto-disable (default 3)
        """
        self.min_success_rate = min_success_rate
        self.failure_threshold = failure_threshold
        self.disabled_skills: set[str] = set()  # Skills that have been auto-disabled

    async def check(self, executor: Any, tenant_id: str) -> HealthStatus:
        """Check executor health across all skills in tenant.

        Args:
            executor: SkillExecutor instance
            tenant_id: Tenant identifier

        Returns:
            HealthStatus indicating executor health
        """
        try:
            stats_by_skill = executor.get_all_stats(tenant_id)

            if not stats_by_skill:
                return HealthStatus(
                    component="executor",
                    healthy=True,
                    message="Executor idle (no executions)",
                    metrics={},
                )

            # Check for unhealthy skills
            unhealthy_skills = []
            newly_disabled = []

            for skill_name, stats in stats_by_skill.items():
                # Check success rate
                if stats.total_executions > 0:
                    if stats.success_rate < self.min_success_rate:
                        unhealthy_skills.append(
                            f"{skill_name} (success_rate={stats.success_rate:.2%})"
                        )

                # Check for auto-disable
                if stats.is_disabled and skill_name not in self.disabled_skills:
                    self.disabled_skills.add(skill_name)
                    newly_disabled.append(skill_name)

            # Compile health metrics
            metrics = {
                "total_skills": len(stats_by_skill),
                "unhealthy_skills": len(unhealthy_skills),
                "auto_disabled_skills": len(self.disabled_skills),
                "newly_disabled": newly_disabled,
                "skills": {
                    name: {
                        "success_rate": stats.success_rate,
                        "total_executions": stats.total_executions,
                        "is_disabled": stats.is_disabled,
                    }
                    for name, stats in stats_by_skill.items()
                },
            }

            healthy = len(unhealthy_skills) == 0 and len(newly_disabled) == 0
            message = (
                f"Executor healthy ({len(stats_by_skill)} skills)"
                if healthy
                else f"Executor degraded: {len(unhealthy_skills)} unhealthy, "
                     f"{len(newly_disabled)} newly disabled"
            )

            return HealthStatus(
                component="executor",
                healthy=healthy,
                message=message,
                metrics=metrics,
            )
        except Exception as e:
            return HealthStatus(
                component="executor",
                healthy=False,
                message=f"Executor check error: {type(e).__name__}: {str(e)}",
            )


class HealthMonitor:
    """Monitors multiple health checks."""

    def __init__(self):
        """Initialize."""
        self.checks: dict[str, HealthStatus] = {}
        self._lock = asyncio.Lock()

    def register_check(self, status: HealthStatus) -> None:
        """Register a health check result."""
        self.checks[status.component] = status

    def get_health_summary(self) -> dict[str, HealthStatus]:
        """Get current health status for all components."""
        return dict(self.checks)

    def is_healthy(self) -> bool:
        """Return True if all registered checks are healthy."""
        return all(s.healthy for s in self.checks.values())

    async def wait_healthy(self, timeout_s: float = 60.0) -> bool:
        """Wait until all checks are healthy.

        Args:
            timeout_s: Max time to wait

        Returns:
            True if all healthy before timeout, False if timeout
        """
        start_t = time.time()
        while time.time() - start_t < timeout_s:
            if self.is_healthy():
                return True
            await asyncio.sleep(0.5)
        return False

    async def run_monitoring_loop(
        self,
        checks: list[tuple[str, Any]],  # (component_name, check_fn)
        interval_s: float = 5.0,
    ) -> None:
        """Run async monitoring loop (infinite).

        Args:
            checks: List of (component_name, async_check_fn) tuples
            interval_s: Check interval in seconds
        """
        while True:
            try:
                for name, check_fn in checks:
                    try:
                        status = await check_fn()
                        self.register_check(status)
                    except Exception:
                        pass  # Log silently, continue
                await asyncio.sleep(interval_s)
            except Exception:
                await asyncio.sleep(interval_s)
