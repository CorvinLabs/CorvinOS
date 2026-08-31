"""Integration tests — K4-001 wiring fix."""

import pytest

from core.skills.grader import GradingManager
from core.skills.integration import SkillSystemIntegration
from core.skills.learning_loop import SkillLearningManager
from core.skills.store import InMemorySkillStore
from core.skills.telemetry import NoOpPublisher, MetricsCollector
from core.skills.telemetry_manager import TelemetryManager
from core.skills.graders.heuristic import HeuristicGrader


@pytest.mark.asyncio
async def test_integration_wiring():
    """Verify all modules are wired together."""
    # Setup
    store = InMemorySkillStore()
    learning = SkillLearningManager(store)
    grading = GradingManager(store, HeuristicGrader())
    collector = MetricsCollector("test", "1.0")
    telemetry = TelemetryManager(collector, NoOpPublisher())

    # Integration
    system = SkillSystemIntegration(learning, grading, telemetry)

    # Verify wiring: all components reachable
    assert system.learning is learning
    assert system.grading is grading
    assert system.telemetry is telemetry
    assert system.health_monitor is not None
    assert system.backoff is not None


@pytest.mark.asyncio
async def test_system_status():
    """Verify system status aggregates all modules."""
    store = InMemorySkillStore()
    learning = SkillLearningManager(store)
    grading = GradingManager(store, HeuristicGrader())
    collector = MetricsCollector("test", "1.0")
    telemetry = TelemetryManager(collector, NoOpPublisher())

    system = SkillSystemIntegration(learning, grading, telemetry)

    status = system.get_system_status()
    assert "grading" in status
    assert "telemetry" in status
    assert "health" in status
    assert "backoff" in status
