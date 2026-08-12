"""E2E tests for Confidence Scoring (ADR-0315)."""

import pytest
import asyncio
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from core.learning.confidence_scorer import ConfidenceScorer
from core.learning.event_emitter import EventEmitter
from core.learning.event_schema import LearningEventType
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


class TestConfidenceE2E:
    """End-to-end tests for confidence scoring."""

    @pytest.mark.asyncio
    async def test_score_and_emit(self, temp_tenant_home):
        """Score a skill and emit confidence event."""
        scorer = ConfidenceScorer()
        emitter = EventEmitter(temp_tenant_home, "_default")

        await emitter.start()

        # Calculate score
        score = scorer.score_skill(
            skill_name="code_review",
            task_type="code_review",
            invocation_count=20,
            error_rate=0.05,
            avg_latency_ms=1000.0,
            latency_stddev_ms=100.0,
        )

        # Emit event
        await emitter.emit_confidence_score(
            skill_name="code_review",
            session_id="session-123",
            relevance=score.relevance,
            reliability=score.reliability,
            combined=score.combined,
            band=score.band.value,
            reasoning=score.reasoning,
        )

        await emitter.flush()
        await emitter.stop()

        # Verify event was persisted
        persisted = await emitter.read_events(skill_name="code_review")
        assert len(persisted) == 1
        assert persisted[0].event_type == LearningEventType.CONFIDENCE_SCORE
        assert persisted[0].payload["combined_score"] == score.combined
        assert persisted[0].payload["band"] == score.band.value

    @pytest.mark.asyncio
    async def test_integration_emit_confidence(self, temp_tenant_home):
        """Emit confidence through SkillSystemIntegration."""
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

        # Emit confidence score
        await system.emit_confidence_score(
            skill_name="ranking",
            session_id="session-456",
            relevance=0.85,
            reliability=0.92,
            combined=0.88,
            band="high",
            reasoning="Good match + low variance",
        )

        await system.flush_events()

        # Read back
        events = await system.read_learning_events(skill_name="ranking")
        assert len(events) == 1
        assert events[0].payload["relevance_score"] == 0.85
        assert events[0].payload["reliability_score"] == 0.92

    @pytest.mark.asyncio
    async def test_multiple_confidence_scores(self, temp_tenant_home):
        """Emit multiple confidence scores for different skills."""
        scorer = ConfidenceScorer()
        emitter = EventEmitter(temp_tenant_home, "_default")

        await emitter.start()

        # Score multiple skills
        skills = [
            ("code_review", "code_review", 0.95),
            ("ranking", "summarize", 0.9),
            ("summarizer", "code_review", 0.2),
        ]

        for skill_name, task_type, expected_relevance in skills:
            score = scorer.score_skill(
                skill_name=skill_name,
                task_type=task_type,
                invocation_count=30,
                error_rate=0.05,
                avg_latency_ms=1000.0,
                latency_stddev_ms=100.0,
            )

            await emitter.emit_confidence_score(
                skill_name=skill_name,
                session_id="session-multi",
                relevance=score.relevance,
                reliability=score.reliability,
                combined=score.combined,
                band=score.band.value,
            )

        await emitter.flush()
        await emitter.stop()

        # Verify all events persisted
        all_events = await emitter.read_events()
        assert len(all_events) == 3

        # Check filtering by skill
        code_review_events = await emitter.read_events(skill_name="code_review")
        assert len(code_review_events) == 1
        assert code_review_events[0].skill_name == "code_review"
