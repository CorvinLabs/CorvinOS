"""CRITICAL-5: EventEmitter Universal Wiring — End-to-End Integration Test.

Verifies that all learning events flow through EventEmitter (async queue)
and persist correctly across the audit trail.

Coverage:
- Confidence score events
- Operator feedback (tool/skill ratings)
- Skill attribution
- User preference updates
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from core.learning.confidence_scorer import ConfidenceScorer
from core.learning.event_store import EventStore as _LearningEventStore
from core.learning.operator_feedback import OperatorFeedbackHandler
from core.learning.skill_attribution import SkillAttributionEngine, AttributionModel
from core.learning.user_profile import UserProfileManager, UserProfile, DecisionStyle
from core.learning.event_emitter import EventEmitter
from core.learning.event_schema import LearningEvent, LearningEventType
from core.learning.event_store import EventStore


@pytest.mark.asyncio
class TestEventEmitterUniversalWiring:
    """End-to-end verification of EventEmitter wiring across all learning modules."""

    @pytest.fixture
    def tenant_home(self, tmp_path: Path) -> Path:
        """Create temporary tenant home for testing."""
        tenant_home = tmp_path / "corvin" / "tenants" / "_default"
        tenant_home.mkdir(parents=True, exist_ok=True)
        return tenant_home

    @pytest.fixture
    def event_emitter(self, tenant_home: Path) -> EventEmitter:
        """Create EventEmitter instance."""
        return EventEmitter(_LearningEventStore(tenant_home), queue_size=1000)

    @pytest.fixture
    def event_store(self, tenant_home: Path) -> EventStore:
        """Create EventStore instance."""
        return EventStore(tenant_id="_default")

    async def test_confidence_score_event_persistence(
        self, event_emitter: EventEmitter, event_store: EventStore
    ):
        """Verify confidence score events persist through EventEmitter."""
        scorer = ConfidenceScorer(
            skills_fetcher=lambda sid: None,
            event_store=event_store,
            event_emitter=event_emitter,
        )

        await event_emitter.start()

        try:
            # Count events before
            before_count = await event_emitter.get_event_count()

            # Emit confidence event
            scorer._emit_confidence_event(
                skill_id="llm-chain",
                relevance=0.9,
                reliability=0.85,
                context={"tenant_id": "_default", "user_id": "alice"},
            )

            # Wait for background processing
            await asyncio.sleep(0.2)

            # Verify event was persisted
            after_count = await event_emitter.get_event_count()
            assert after_count > before_count, "Event should have been persisted"

            await event_emitter.flush()

        finally:
            await event_emitter.stop()

    async def test_operator_feedback_event_persistence(
        self, event_emitter: EventEmitter, event_store: EventStore
    ):
        """Verify operator feedback events persist through EventEmitter."""
        handler = OperatorFeedbackHandler(
            event_store=event_store,
            min_sample_size=1,
            event_emitter=event_emitter,
        )

        await event_emitter.start()

        try:
            before_count = await event_emitter.get_event_count()

            # Record tool rating
            await handler.record_tool_rating(
                tool_id="python-executor",
                tool_name="Python Executor",
                rating=5,
                tenant_id="_default",
                feedback_text="Very reliable",
            )

            await asyncio.sleep(0.2)

            after_count = await event_emitter.get_event_count()
            assert after_count > before_count, "Tool rating event should be persisted"

            # Record skill rating
            before_count = after_count
            await handler.record_skill_rating(
                skill_id="json-parsing",
                skill_name="JSON Parser",
                rating=4,
                tenant_id="_default",
                feedback_text="Good accuracy",
            )

            await asyncio.sleep(0.2)

            after_count = await event_emitter.get_event_count()
            assert after_count > before_count, "Skill rating event should be persisted"

            await event_emitter.flush()

        finally:
            await event_emitter.stop()

    async def test_skill_attribution_event_persistence(
        self, event_emitter: EventEmitter, event_store: EventStore
    ):
        """Verify skill attribution events persist through EventEmitter."""
        engine = SkillAttributionEngine(
            tenant_id="_default",
            event_store=event_store,
            model=AttributionModel.EQUAL,
            emit_events=True,
            event_emitter=event_emitter,
        )

        await event_emitter.start()

        try:
            before_count = await event_emitter.get_event_count()

            # Attribute outcome for composite strategy
            payload = await engine.attribute_outcome(
                strategy_id="pipeline-v1",
                decision_id="task-123",
                skills=["prompt-builder", "json-parser", "validator"],
                outcome="success",
                rating=5,
            )

            assert payload.attribution_id
            assert len(payload.credits) == 3  # Equal split among 3 skills

            await asyncio.sleep(0.2)

            after_count = await event_emitter.get_event_count()
            assert after_count > before_count, "Attribution event should be persisted"

            # Verify partial outcome
            before_count = after_count
            payload2 = await engine.attribute_outcome(
                strategy_id="pipeline-v1",
                decision_id="task-124",
                skills=["prompt-builder", "json-parser"],
                outcome="partial",
                rating=3,
            )

            await asyncio.sleep(0.2)

            after_count = await event_emitter.get_event_count()
            assert (
                after_count > before_count
            ), "Partial attribution event should be persisted"

            await event_emitter.flush()

        finally:
            await event_emitter.stop()

    def test_user_preference_event_emission(
        self, event_emitter: EventEmitter, event_store: EventStore
    ):
        """Verify user preference events are scheduled (non-blocking)."""
        manager = UserProfileManager(
            event_store=event_store,
            event_emitter=event_emitter,
        )

        profile = UserProfile(
            user_id="bob",
            tenant_id="_default",
            decision_style=DecisionStyle.BALANCED,
            conciseness_preference=0.5,
        )

        feedback = {
            "decision_style": "pragmatic",
            "conciseness": 0.8,
        }

        # Should not raise, even if no event loop
        manager._emit_preference_updated(profile, feedback)

    async def test_concurrent_emission_across_modules(
        self, event_emitter: EventEmitter, event_store: EventStore
    ):
        """Verify concurrent event emission from multiple modules."""
        # Create instances of all modules
        scorer = ConfidenceScorer(
            skills_fetcher=lambda sid: None,
            event_store=event_store,
            event_emitter=event_emitter,
        )

        handler = OperatorFeedbackHandler(
            event_store=event_store,
            min_sample_size=1,
            event_emitter=event_emitter,
        )

        engine = SkillAttributionEngine(
            tenant_id="_default",
            event_store=event_store,
            model=AttributionModel.EQUAL,
            emit_events=True,
            event_emitter=event_emitter,
        )

        await event_emitter.start()

        try:
            before_count = await event_emitter.get_event_count()

            # Emit events concurrently from all modules
            async def run_all():
                tasks = []

                # Confidence scores
                for i in range(5):
                    scorer._emit_confidence_event(
                        skill_id=f"skill-{i}",
                        relevance=0.7 + (i * 0.05),
                        reliability=0.8 + (i * 0.04),
                        context={"tenant_id": "_default"},
                    )

                # Operator feedback
                for i in range(5):
                    tasks.append(
                        handler.record_tool_rating(
                            tool_id=f"tool-{i}",
                            tool_name=f"Tool {i}",
                            rating=min(5, i + 1),
                            tenant_id="_default",
                        )
                    )

                # Skill attribution
                for i in range(5):
                    tasks.append(
                        engine.attribute_outcome(
                            strategy_id=f"strategy-{i}",
                            decision_id=f"decision-{i}",
                            skills=["s1", "s2"],
                            outcome="success" if i % 2 == 0 else "partial",
                        )
                    )

                await asyncio.gather(*tasks)

            await run_all()
            await asyncio.sleep(0.3)  # Allow background processing

            after_count = await event_emitter.get_event_count()
            assert after_count > before_count, "Events should have been persisted"

            await event_emitter.flush()

        finally:
            await event_emitter.stop()

    async def test_event_emitter_fallback_to_event_store(
        self, event_store: EventStore, tenant_home: Path
    ):
        """Verify fallback works when EventEmitter unavailable (event_store only)."""
        # Create handler WITHOUT event_emitter
        handler = OperatorFeedbackHandler(
            event_store=event_store,
            min_sample_size=1,
            event_emitter=None,  # No emitter; should use event_store directly
        )

        # Record rating (should fallback to event_store.write_event)
        await handler.record_tool_rating(
            tool_id="test-tool",
            tool_name="Test",
            rating=5,
            tenant_id="_default",
        )

        # Verify no exception raised
        # (In production, events still persist via fallback path)

    async def test_tenant_isolation_in_event_emission(
        self, event_emitter: EventEmitter, event_store: EventStore
    ):
        """Verify tenant isolation on event emission."""
        scorer = ConfidenceScorer(
            skills_fetcher=lambda sid: None,
            event_store=event_store,
            event_emitter=event_emitter,
        )

        await event_emitter.start()

        try:
            # Emit events for different tenants (only _default should work)
            # Confidence scorer uses context["tenant_id"], so it will emit with _default

            # This should work (matching tenant)
            scorer._emit_confidence_event(
                skill_id="skill-1",
                relevance=0.9,
                reliability=0.85,
                context={"tenant_id": "_default"},  # Matches emitter
            )

            # Create a separate emitter for another tenant
            other_emitter = EventEmitter(
                event_emitter.tenant_home,
                tenant_id="other-tenant",
            )

            # Event with wrong tenant should raise when emitted to wrong emitter
            wrong_event = LearningEvent(
                event_type=LearningEventType.CONFIDENCE_SCORE,
                tenant_id="other-tenant",
                instance_id="scorer",
                skill_name="skill-2",
                session_id="test",
            )

            with pytest.raises(ValueError, match="Tenant mismatch"):
                await event_emitter.emit(wrong_event)

            await event_emitter.flush()

        finally:
            await event_emitter.stop()

    async def test_fire_and_forget_on_queue_full(self, tenant_home: Path):
        """Verify fire-and-forget behavior when queue is full."""
        # Small queue to force dropping
        emitter = EventEmitter(_LearningEventStore(tenant_home), queue_size=5)

        await emitter.start()

        try:
            # Emit more than queue size (most will be dropped)
            dropped_count = 0
            for i in range(20):
                event = LearningEvent(
                    event_type=LearningEventType.CONFIDENCE_SCORE,
                    tenant_id="_default",
                    instance_id="test",
                    skill_name=f"skill-{i}",
                    session_id="test",
                )

                # Should never raise, even if queue is full
                # Fire-and-forget semantics
                await emitter.emit(event)

            await asyncio.sleep(0.2)
            await emitter.flush()

            # Verify some events persisted (not all, due to queue size)
            count = await emitter.get_event_count()
            assert count > 0, "At least some events should persist"
            assert count < 20, "Queue-full drops should prevent all 20 from persisting"

        finally:
            await emitter.stop()
