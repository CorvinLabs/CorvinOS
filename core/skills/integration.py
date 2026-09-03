"""Skill System Integration — wires all modules together (K4-001 fix + ADR-0314).

Learning events (ADR-0314) go through ``core.learning.event_emitter.EventEmitter``
in its CURRENT shape — ``EventEmitter(EventStore(tenant_home))`` with a
synchronous, non-blocking ``emit(LearningEvent) -> bool`` and ``stop()``.
The previous version used the pre-``df125e48`` API (``EventEmitter(tenant_home,
tenant_id)``, ``await start()/flush()/emit_confidence_score()/read_events()``):
construction succeeded with a ``PosixPath`` as the store and every emit raised
``TypeError`` (adversarial review D-08).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Optional

from .backoff import BackoffConfig, SelfHealingBackoff
from .grader import GradingManager
from .health import GradingHealth, HealthMonitor, TelemetryHealth
from .learning_loop import SkillLearningManager
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
        self.tenant_home = Path(tenant_home) if tenant_home is not None else None
        self.tenant_id = tenant_id

        # Health checks
        self.grading_health = GradingHealth(stall_threshold_s=10.0)
        self.telemetry_health = TelemetryHealth(backlog_threshold=100)
        self.health_monitor = HealthMonitor()

        # Backoff for recovery
        self.backoff = SelfHealingBackoff(BackoffConfig(base_delay_s=1.0, max_delay_s=60.0))

        # Learning event store + emitter (ADR-0314). The emitter's worker is a
        # daemon thread with an atexit flush; ``stop()`` joins it explicitly.
        self.event_store = None
        self.event_emitter = None
        if self.tenant_home is not None:
            from core.learning.event_emitter import EventEmitter
            from core.learning.event_store import EventStore

            self.event_store = EventStore(self.tenant_home)
            self.event_emitter = EventEmitter(self.event_store)

    async def run_all_loops(self) -> None:
        """Run all async loops concurrently.

        Orchestration:
        1. Learning loop captures metadata
        2. Grading loop grades skills
        3. Telemetry loop publishes metrics
        4. Health loop monitors system
        5. Backoff recovers on failure
        6. Event emitter persists learning events in the background (ADR-0314)
        """
        try:
            await asyncio.gather(
                self.learning.run_grading_loop(self.grading),  # Learning → Grading
                self.telemetry.collect_and_publish_loop(self.grading),  # Grading → Telemetry
                self._run_health_loop(),  # Telemetry + Grading → Health
                return_exceptions=True,
            )
        finally:
            self.stop_event_emitter()

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

    # ── Learning events (ADR-0314) ────────────────────────────────────────────

    def stop_event_emitter(self) -> None:
        """Flush + join the emitter worker (idempotent)."""
        if self.event_emitter is not None:
            self.event_emitter.stop()

    def emit_learning_event(self, event: Any) -> bool:
        """Queue a ``LearningEvent`` (non-blocking).

        Returns True when queued, False when dropped (queue full, emitter
        stopped, or no tenant_home → emitter not configured).
        """
        if self.event_emitter is None:
            return False
        return self.event_emitter.emit(event)

    def emit_confidence_score(
        self,
        skill_name: str,
        session_id: str,
        relevance: float,
        reliability: float,
        combined: float,
        band: str,
        reasoning: Optional[str] = None,
    ) -> bool:
        """Emit a confidence score learning event (ADR-0315)."""
        if self.event_emitter is None:
            return False
        from core.learning.learning_events import EventType, LearningEvent

        event = LearningEvent.create(
            event_type=EventType.CONFIDENCE,
            skill_id=skill_name,
            tenant_id=self.tenant_id,
            signal={
                "session_id": session_id,
                "relevance": relevance,
                "reliability": reliability,
                "combined": combined,
                "band": band,
                "reasoning": reasoning,
            },
            lom="core.skills.integration:SkillSystemIntegration.emit_confidence_score",
        )
        return self.event_emitter.emit(event)

    def read_learning_events(
        self,
        event_type: Optional[Any] = None,
        skill_name: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> list[Any]:
        """Read persisted learning events of THIS tenant with optional filtering.

        Call ``stop_event_emitter()`` (or wait) first if you need events that
        were queued moments ago — persistence is asynchronous.
        """
        if self.event_store is None:
            return []
        events = self.event_store.query_events(
            tenant_id=self.tenant_id,
            event_type=event_type,
            skill_id=skill_name,
        )
        if session_id is not None:
            events = [e for e in events if (e.signal or {}).get("session_id") == session_id]
        return events

    def get_system_status(self) -> dict[str, Any]:
        """Get overall system status."""
        emitter = self.event_emitter
        return {
            "grading": self.grading.get_stats(),
            "telemetry": self.telemetry.get_stats(),
            "health": self.health_monitor.get_health_summary(),
            "backoff": self.backoff.get_status(),
            "event_emitter": {
                "enabled": emitter is not None,
                "dropped": getattr(emitter, "dropped", 0),
                "write_failures": getattr(emitter, "write_failures", 0),
            },
        }
