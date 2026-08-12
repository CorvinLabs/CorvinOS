"""E2E tests for Learning Infrastructure (ADR-0314)."""

import pytest
import asyncio
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from core.learning.event_schema import LearningEvent, LearningEventType
from core.learning.event_emitter import EventEmitter
from core.skills.integration import SkillSystemIntegration
from core.skills.grader import GradingManager
from core.skills.learning_loop import SkillLearningManager
from core.skills.store import InMemorySkillStore
from core.skills.telemetry import MetricsCollector, NoOpPublisher
from core.skills.telemetry_manager import TelemetryManager
from core.skills.graders.heuristic import HeuristicGrader


@pytest.fixture
def temp_tenant_home():
    """Create a temporary tenant home directory."""
    with TemporaryDirectory() as tmpdir:
        tenant_home = Path(tmpdir) / "tenants" / "_default"
        tenant_home.mkdir(parents=True, exist_ok=True)
        yield tenant_home


class TestLearningE2E:
    """End-to-end tests for learning infrastructure."""

    @pytest.mark.asyncio
    async def test_emit_event_through_integration(self, temp_tenant_home):
        """Emit learning event through SkillSystemIntegration."""
        store = InMemorySkillStore()
        learning = SkillLearningManager(store)
        grading = GradingManager(store, HeuristicGrader())
        collector = MetricsCollector("test", "1.0")
        telemetry = TelemetryManager(collector, NoOpPublisher())

        system = SkillSystemIntegration(
            learning,
            grading,
            telemetry,
            tenant_home=temp_tenant_home,
            tenant_id="_default",
        )

        # Emit a confidence score event
        event = LearningEvent(
            event_type=LearningEventType.CONFIDENCE_SCORE,
            tenant_id="_default",
            instance_id="console-1",
            skill_name="ranking",
            session_id="session-123",
            timestamp_utc=datetime.utcnow(),
            payload={"relevance_score": 0.95, "reliability_score": 0.87},
        )

        await system.start_event_emitter()
        await system.emit_learning_event(event)
        await system.flush_events()

        # Verify event was persisted
        persisted = await system.read_learning_events()
        assert len(persisted) == 1
        assert persisted[0].event_type == LearningEventType.CONFIDENCE_SCORE
        assert persisted[0].payload["relevance_score"] == 0.95

    @pytest.mark.asyncio
    async def test_multiple_event_types(self, temp_tenant_home):
        """Emit multiple event types and verify all persist."""
        store = InMemorySkillStore()
        learning = SkillLearningManager(store)
        grading = GradingManager(store, HeuristicGrader())
        collector = MetricsCollector("test", "1.0")
        telemetry = TelemetryManager(collector, NoOpPublisher())

        system = SkillSystemIntegration(
            learning,
            grading,
            telemetry,
            tenant_home=temp_tenant_home,
            tenant_id="_default",
        )

        await system.start_event_emitter()

        # Emit different event types
        events_to_emit = [
            LearningEvent(
                event_type=LearningEventType.CONFIDENCE_SCORE,
                tenant_id="_default",
                instance_id="console-1",
                skill_name="ranking",
                session_id="session-1",
                timestamp_utc=datetime.utcnow(),
                payload={"relevance_score": 0.95},
            ),
            LearningEvent(
                event_type=LearningEventType.USER_FEEDBACK,
                tenant_id="_default",
                instance_id="console-1",
                skill_name=None,
                session_id="session-1",
                timestamp_utc=datetime.utcnow(),
                user_id="user-1",
                payload={"feedback_type": "rating", "feedback_value": 4},
            ),
            LearningEvent(
                event_type=LearningEventType.OUTCOME_OBSERVED,
                tenant_id="_default",
                instance_id="console-1",
                skill_name="ranking",
                session_id="session-1",
                timestamp_utc=datetime.utcnow(),
                payload={"decision_id": "d1", "outcome_type": "latency", "outcome_value": 1250.5, "window_seconds": 300},
            ),
        ]

        for event in events_to_emit:
            await system.emit_learning_event(event)

        await system.flush_events()

        # Verify all events persisted
        persisted = await system.read_learning_events()
        assert len(persisted) == 3

        event_types = {e.event_type for e in persisted}
        assert LearningEventType.CONFIDENCE_SCORE in event_types
        assert LearningEventType.USER_FEEDBACK in event_types
        assert LearningEventType.OUTCOME_OBSERVED in event_types

    @pytest.mark.asyncio
    async def test_event_filtering(self, temp_tenant_home):
        """Filter events by skill name."""
        store = InMemorySkillStore()
        learning = SkillLearningManager(store)
        grading = GradingManager(store, HeuristicGrader())
        collector = MetricsCollector("test", "1.0")
        telemetry = TelemetryManager(collector, NoOpPublisher())

        system = SkillSystemIntegration(
            learning,
            grading,
            telemetry,
            tenant_home=temp_tenant_home,
            tenant_id="_default",
        )

        await system.start_event_emitter()

        # Emit events for different skills
        event1 = LearningEvent(
            event_type=LearningEventType.CONFIDENCE_SCORE,
            tenant_id="_default",
            instance_id="console-1",
            skill_name="ranking",
            session_id="session-1",
            timestamp_utc=datetime.utcnow(),
            payload={},
        )

        event2 = LearningEvent(
            event_type=LearningEventType.CONFIDENCE_SCORE,
            tenant_id="_default",
            instance_id="console-1",
            skill_name="code_review",
            session_id="session-1",
            timestamp_utc=datetime.utcnow(),
            payload={},
        )

        await system.emit_learning_event(event1)
        await system.emit_learning_event(event2)
        await system.flush_events()

        # Filter by skill
        ranking_events = await system.read_learning_events(skill_name="ranking")
        assert len(ranking_events) == 1
        assert ranking_events[0].skill_name == "ranking"
