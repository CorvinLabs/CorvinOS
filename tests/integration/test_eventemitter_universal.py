"""CRITICAL-5: EventEmitter Universal Wiring — End-to-End Integration Test.

Verifies that the learning producers that accept an ``event_emitter`` really
flow through the ONE sync ``EventEmitter`` (bounded queue + daemon worker) into
the ONE ``event_store.EventStore`` (audit-FIRST, tenant-bound) — and that the
fail-closed edges hold (foreign tenant never lands, queue-full is observable).

Rewritten 2026-09-07 against the real contract. The previous version drove an
imaginary async API (``await emitter.start()``, ``get_event_count()``,
``flush()``, ``EventEmitter(tenant_home, tenant_id=)``) and a second event
schema; none of it existed, so the file proved nothing (7 errors).

Producers wired through the emitter today:
- ``OperatorFeedbackHandler`` (tool / skill ratings) — a drop is an ERROR here
- ``SkillAttributionEngine`` (composite-strategy credit)
- ``UserProfileManager`` (preference updates)
``ConfidenceScorer`` has no emitter path any more — it is deliberately absent.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.learning.event_emitter import EventEmitter
from core.learning.event_store import EventStore
from core.learning.learning_events import EventType, LearningEvent
from core.learning.operator_feedback import OperatorFeedbackHandler
from core.learning.skill_attribution import AttributionModel, SkillAttributionEngine
from core.learning.user_profile import DecisionStyle, UserProfile, UserProfileManager

TENANT = "_default"


@pytest.fixture
def tenant_home(tmp_path: Path) -> Path:
    home = tmp_path / "corvin" / "tenants" / TENANT
    home.mkdir(parents=True, exist_ok=True)
    return home


@pytest.fixture
def event_store(tenant_home: Path) -> EventStore:
    # Tenant-BOUND store: a foreign tenant's event is rejected at write time.
    return EventStore(tenant_home, tenant_id=TENANT)


@pytest.fixture
def event_emitter(event_store: EventStore):
    emitter = EventEmitter(event_store, queue_size=1000)
    yield emitter
    emitter.stop(timeout=5.0)


def _count(store: EventStore, **kw) -> int:
    return len(store.query_events(tenant_id=TENANT, **kw))


class TestEventEmitterUniversalWiring:
    """Real producers → real emitter → real store (audit-first)."""

    def test_operator_feedback_event_persistence(self, event_emitter, event_store):
        handler = OperatorFeedbackHandler(
            event_store=event_store, min_sample_size=1, event_emitter=event_emitter
        )
        before = _count(event_store)

        handler.record_tool_rating(
            tool_id="python-executor", tool_name="Python Executor", rating=5, tenant_id=TENANT
        )
        handler.record_skill_rating(
            skill_id="json-parsing", skill_name="JSON Parser", rating=4, tenant_id=TENANT
        )

        event_emitter.stop()  # drains the queue (worker joins)
        assert event_emitter.write_failures == 0
        assert event_emitter.dropped == 0
        assert _count(event_store) == before + 2
        # Every disk record carries the core-chain reference (audit-FIRST).
        for ev in event_store.query_events(tenant_id=TENANT):
            assert ev.tenant_id == TENANT

    @pytest.mark.asyncio
    async def test_skill_attribution_event_persistence(self, event_emitter, event_store):
        engine = SkillAttributionEngine(
            tenant_id=TENANT,
            event_store=event_store,
            model=AttributionModel.EQUAL,
            emit_events=True,
            event_emitter=event_emitter,
        )
        before = _count(event_store, event_type=EventType.OUTCOME)

        payload = await engine.attribute_outcome(
            strategy_id="pipeline-v1",
            decision_id="task-123",
            skills=["prompt-builder", "json-parser", "validator"],
            outcome="success",
            rating=5,
        )
        assert payload.attribution_id
        assert len(payload.credits) == 3

        await engine.attribute_outcome(
            strategy_id="pipeline-v1",
            decision_id="task-124",
            skills=["prompt-builder", "json-parser"],
            outcome="partial",
            rating=3,
        )

        event_emitter.stop()
        assert event_emitter.write_failures == 0
        outcomes = event_store.query_events(tenant_id=TENANT, event_type=EventType.OUTCOME)
        assert len(outcomes) == before + 2
        assert {e.skill_id for e in outcomes} == {"strategy:pipeline-v1"}

    def test_user_preference_event_emission(self, event_emitter, event_store):
        manager = UserProfileManager(event_store=event_store, event_emitter=event_emitter)
        profile = UserProfile(
            user_id="bob",
            tenant_id=TENANT,
            decision_style=DecisionStyle.BALANCED,
            conciseness_preference=0.5,
        )
        before = _count(event_store)

        manager._emit_preference_updated(profile, {"decision_style": "pragmatic", "conciseness": 0.8})

        event_emitter.stop()
        assert event_emitter.write_failures == 0
        assert _count(event_store) == before + 1

    @pytest.mark.asyncio
    async def test_concurrent_emission_across_modules(self, event_emitter, event_store):
        handler = OperatorFeedbackHandler(
            event_store=event_store, min_sample_size=1, event_emitter=event_emitter
        )
        engine = SkillAttributionEngine(
            tenant_id=TENANT,
            event_store=event_store,
            model=AttributionModel.EQUAL,
            emit_events=True,
            event_emitter=event_emitter,
        )
        before = _count(event_store)

        for i in range(5):
            handler.record_tool_rating(
                tool_id=f"tool-{i}", tool_name=f"Tool {i}", rating=min(5, i + 1), tenant_id=TENANT
            )
        for i in range(5):
            await engine.attribute_outcome(
                strategy_id=f"strategy-{i}",
                decision_id=f"decision-{i}",
                skills=["s1", "s2"],
                outcome="success" if i % 2 == 0 else "partial",
            )

        event_emitter.stop()
        assert event_emitter.write_failures == 0
        assert event_emitter.dropped == 0
        assert _count(event_store) == before + 10

    def test_event_emitter_fallback_to_event_store(self, event_store):
        """No emitter → the handler writes to the SAME store directly."""
        handler = OperatorFeedbackHandler(event_store=event_store, min_sample_size=1, event_emitter=None)
        before = _count(event_store)
        handler.record_tool_rating(tool_id="test-tool", tool_name="Test", rating=5, tenant_id=TENANT)
        assert _count(event_store) == before + 1

    def test_tenant_isolation_in_event_emission(self, event_emitter, event_store):
        """A foreign tenant's event never lands under this tenant's directory."""
        wrong = LearningEvent.create(
            event_type=EventType.OUTCOME,
            skill_id="skill-2",
            tenant_id="other-tenant",
            signal={"kind": "test"},
            lom="tests/integration/test_eventemitter_universal.py:test_tenant_isolation_in_event_emission",
        )
        # The emitter only checks the tenant is a non-empty string; the BOUND
        # store rejects the mismatch in the worker → observable write failure.
        assert event_emitter.emit(wrong) is True
        event_emitter.stop()
        assert event_emitter.write_failures == 1
        assert _count(event_store) == 0
        assert event_store.query_events(tenant_id="other-tenant") == []

        # An empty tenant cannot even be constructed (schema is fail-closed).
        with pytest.raises(ValueError, match="tenant_id is required"):
            LearningEvent.create(
                event_type=EventType.OUTCOME, skill_id="s", tenant_id="", signal={}, lom="t:f"
            )

    def test_fire_and_forget_on_queue_full(self, event_store):
        """A full queue drops (returns False, counts) — it never blocks or raises."""

        class _SlowStore:
            def __init__(self) -> None:
                self.written = 0

            def write_event(self, event) -> None:
                import time

                time.sleep(0.05)
                self.written += 1

        slow = _SlowStore()
        emitter = EventEmitter(slow, queue_size=5)
        try:
            results = []
            for i in range(20):
                ev = LearningEvent.create(
                    event_type=EventType.OUTCOME,
                    skill_id=f"skill-{i}",
                    tenant_id=TENANT,
                    signal={},
                    lom="t:f",
                )
                results.append(emitter.emit(ev))
            emitter.stop(timeout=10.0)
        finally:
            if not emitter._stopped:
                emitter.stop()

        assert results.count(False) == emitter.dropped
        assert 0 < emitter.dropped < 20, "bounded queue must drop some, not all"
        assert slow.written == 20 - emitter.dropped

        # After stop(): refused, not queued.
        late = LearningEvent.create(
            event_type=EventType.OUTCOME, skill_id="late", tenant_id=TENANT, signal={}, lom="t:f"
        )
        assert emitter.emit(late) is False
