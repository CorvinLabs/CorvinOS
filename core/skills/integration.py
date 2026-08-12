"""Skill System Integration — wires all modules together (K4-001 fix + ADR-0314)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Optional

from .backoff import BackoffConfig, SelfHealingBackoff
from .grader import GradingManager
from .health import GradingHealth, HealthMonitor, QueueHealth, TelemetryHealth
from .learning_loop import SkillLearningManager
from .telemetry import MetricsCollector, NoOpPublisher
from .telemetry_manager import TelemetryManager


class SkillSystemIntegration:
    """Orchestrates all skill modules: Learning → Grading → Telemetry → Health → Backoff → Events (ADR-0314)."""

    def __init__(
        self,
        learning_manager: SkillLearningManager,
        grading_manager: GradingManager,
        telemetry_manager: TelemetryManager,
        tenant_home: Optional[Path] = None,
        tenant_id: str = "_default",
    ):
        self.learning = learning_manager
        self.grading = grading_manager
        self.telemetry = telemetry_manager
        self.tenant_home = tenant_home
        self.tenant_id = tenant_id

        # Health checks
        self.grading_health = GradingHealth(stall_threshold_s=10.0)
        self.telemetry_health = TelemetryHealth(backlog_threshold=100)
        self.health_monitor = HealthMonitor()

        # Backoff for recovery
        self.backoff = SelfHealingBackoff(BackoffConfig(base_delay_s=1.0, max_delay_s=60.0))

        # Learning event emitter (ADR-0314)
        self.event_emitter = None
        if tenant_home:
            from core.learning.event_emitter import EventEmitter

            self.event_emitter = EventEmitter(tenant_home, tenant_id)

    async def run_all_loops(self) -> None:
        """Run all async loops concurrently.

        Orchestration:
        1. Learning loop captures metadata
        2. Grading loop grades skills
        3. Telemetry loop publishes metrics
        4. Health loop monitors system
        5. Backoff recovers on failure
        6. Event emitter processes learning events (ADR-0314)
        """
        # Start event emitter if available
        if self.event_emitter:
            await self.event_emitter.start()

        try:
            await asyncio.gather(
                self.learning.run_grading_loop(self.grading),  # Learning → Grading
                self.telemetry.collect_and_publish_loop(self.grading),  # Grading → Telemetry
                self._run_health_loop(),  # Telemetry + Grading → Health
                return_exceptions=True,
            )
        finally:
            # Stop event emitter cleanly
            if self.event_emitter:
                await self.event_emitter.stop()
                await self.event_emitter.flush()

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

    def create_and_register_skill(self, name: str, version: str, body: str, tags: list[str] | None = None) -> None:
        """K2-001 Fix: Create skill and register with learning system.

        This establishes the skill lifecycle contract:
        1. Skill created here via create_and_register_skill()
        2. @skill_learnable decorator wraps the actual function
        3. Decorator queues grades to GradingManager
        4. GradingManager persists via Learning loop integration
        """
        from .skill import Skill

        skill = Skill(name=name, version=version, body=body, tags=tags or [])
        self.learning.register_skill(skill)

    async def start_event_emitter(self) -> None:
        """Start the event emitter worker loop."""
        if self.event_emitter:
            await self.event_emitter.start()

    async def flush_events(self) -> None:
        """Wait for all pending events to be persisted."""
        if self.event_emitter:
            await self.event_emitter.flush()

    async def emit_learning_event(self, event: Any) -> None:
        """Emit a learning event asynchronously (non-blocking).

        Args:
            event: LearningEvent to emit

        Note:
            If event_emitter not initialized, silently skips (backward-compatible).
        """
        if self.event_emitter:
            await self.event_emitter.emit(event)

    async def read_learning_events(
        self,
        event_type: Optional[Any] = None,
        skill_name: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> list[Any]:
        """Read persisted learning events with optional filtering.

        Args:
            event_type: Filter by event type
            skill_name: Filter by skill name
            session_id: Filter by session ID

        Returns:
            List of LearningEvent objects, or empty list if emitter not initialized
        """
        if self.event_emitter:
            return await self.event_emitter.read_events(
                event_type=event_type,
                skill_name=skill_name,
                session_id=session_id,
            )
        return []

    def get_system_status(self) -> dict[str, Any]:
        """Get overall system status."""
        return {
            "grading": self.grading.get_stats(),
            "telemetry": self.telemetry.get_stats(),
            "health": self.health_monitor.get_health_summary(),
            "backoff": self.backoff.get_status(),
            "event_emitter": {"enabled": self.event_emitter is not None},
        }
