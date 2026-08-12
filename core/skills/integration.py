"""Skill System Integration — wires all modules together (K4-001 fix)."""

from __future__ import annotations

import asyncio
from typing import Any

from .backoff import BackoffConfig, SelfHealingBackoff
from .grader import GradingManager
from .health import GradingHealth, HealthMonitor, QueueHealth, TelemetryHealth
from .learning_loop import SkillLearningManager
from .telemetry import MetricsCollector, NoOpPublisher
from .telemetry_manager import TelemetryManager


class SkillSystemIntegration:
    """Orchestrates all skill modules: Learning → Grading → Telemetry → Health → Backoff."""

    def __init__(
        self,
        learning_manager: SkillLearningManager,
        grading_manager: GradingManager,
        telemetry_manager: TelemetryManager,
    ):
        self.learning = learning_manager
        self.grading = grading_manager
        self.telemetry = telemetry_manager

        # Health checks
        self.grading_health = GradingHealth(stall_threshold_s=10.0)
        self.telemetry_health = TelemetryHealth(backlog_threshold=100)
        self.health_monitor = HealthMonitor()

        # Backoff for recovery
        self.backoff = SelfHealingBackoff(BackoffConfig(base_delay_s=1.0, max_delay_s=60.0))

    async def run_all_loops(self) -> None:
        """Run all async loops concurrently.

        Orchestration:
        1. Learning loop captures metadata
        2. Grading loop grades skills
        3. Telemetry loop publishes metrics
        4. Health loop monitors system
        5. Backoff recovers on failure
        """
        await asyncio.gather(
            self.learning.run_grading_loop(self.grading),  # Learning → Grading
            self.telemetry.collect_and_publish_loop(self.grading),  # Grading → Telemetry
            self._run_health_loop(),  # Telemetry + Grading → Health
            return_exceptions=True,
        )

    async def _run_health_loop(self) -> None:
        """Health monitoring and recovery loop."""
        checks = [
            (
                "grading",
                lambda: self.grading_health.check(self.grading),
            ),
            (
                "telemetry",
                lambda: self.telemetry_health.check(self.telemetry),
            ),
        ]

        while True:
            try:
                # Run health checks
                for name, check_fn in checks:
                    status = await check_fn()
                    self.health_monitor.register_check(status)

                # If unhealthy, attempt recovery
                if not self.health_monitor.is_healthy():
                    await self._attempt_recovery()

                await asyncio.sleep(5.0)
            except Exception:
                await asyncio.sleep(5.0)

    async def _attempt_recovery(self) -> None:
        """Attempt to recover from unhealthy state via backoff."""
        async def recovery_fn():
            # Try to reset grading stats (simple recovery)
            self.grading.reset_stats()

        async def health_check():
            return self.health_monitor.is_healthy()

        await self.backoff.execute_with_backoff(recovery_fn, health_check)

    def get_system_status(self) -> dict[str, Any]:
        """Get overall system status."""
        return {
            "grading": self.grading.get_stats(),
            "telemetry": self.telemetry.get_stats(),
            "health": self.health_monitor.get_health_summary(),
            "backoff": self.backoff.get_status(),
        }
