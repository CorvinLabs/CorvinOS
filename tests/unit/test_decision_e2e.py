"""E2E tests for Decision History (ADR-0316) through the persisted event store.

A ``DecisionRecord`` is emitted as a ``decision.record`` learning event via the
tenant-bound ``event_persistence.EventStore`` (audit-chain FIRST, then the
date-partitioned disk record) and read back with ``read_decisions``. The
previous version of this file drove an async ``EventEmitter(tenant_home,
tenant_id)`` with ``emit_decision``/``flush``/``store`` that no module defines.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from core.learning.decision_history import DecisionRecord, DecisionRecorder
from core.learning.event_persistence import EventStore
from core.learning.event_schema import LearningEvent, LearningEventType

TENANT = "_default"


def _decision_event(decision: DecisionRecord) -> LearningEvent:
    """The ONE ``decision.record`` learning event for a recorded decision."""
    return LearningEvent(
        event_type=LearningEventType.DECISION_RECORD,
        tenant_id=decision.tenant_id,
        instance_id="test-instance",
        skill_name=None,
        session_id=decision.session_id,
        timestamp_utc=datetime.now(timezone.utc),
        payload=decision.to_payload(),
    )


@pytest.fixture
def store() -> EventStore:
    # CORVIN_HOME is a per-test temp root (tests/conftest.py), so this is an
    # isolated tenant store with its own audit chain.
    return EventStore(TENANT)


class TestDecisionE2E:
    """End-to-end tests for decision history."""

    @pytest.mark.asyncio
    async def test_record_and_emit_decision(self, store):
        """Record a decision, persist it as an event, read it back."""
        recorder = DecisionRecorder(TENANT)

        decision = recorder.create_decision(
            choice_type="skill_selection",
            candidates=["ranking", "summarizer", "code_review"],
            chosen="ranking",
            session_id="session-123",
            confidence_score=0.85,
            reasoning="High relevance + low variance",
        )

        audit_ref = await store.write_event(_decision_event(decision), TENANT)
        assert audit_ref  # chain record committed BEFORE the disk record

        decisions = await store.read_decisions(tenant_id=TENANT, session_id="session-123")
        assert len(decisions) == 1
        assert decisions[0]["decision_id"] == decision.decision_id
        assert decisions[0]["chosen"] == "ranking"
        assert decisions[0]["confidence_score"] == 0.85

    @pytest.mark.asyncio
    async def test_multiple_decisions_filtering(self, store):
        """Persist multiple decisions and filter by choice_type / session."""
        recorder = DecisionRecorder(TENANT)

        d1 = recorder.create_decision(
            choice_type="skill_selection", candidates=["a", "b"], chosen="a", session_id="s1",
        )
        d2 = recorder.create_decision(
            choice_type="model_choice", candidates=["gpt-4", "claude"], chosen="claude", session_id="s1",
        )
        d3 = recorder.create_decision(
            choice_type="skill_selection", candidates=["c", "d"], chosen="c", session_id="s2",
        )

        for d in (d1, d2, d3):
            await store.write_event(_decision_event(d), TENANT)

        skill_decisions = await store.read_decisions(tenant_id=TENANT, choice_type="skill_selection")
        assert {d["decision_id"] for d in skill_decisions} == {d1.decision_id, d3.decision_id}

        s1_decisions = await store.read_decisions(tenant_id=TENANT, session_id="s1")
        assert {d["decision_id"] for d in s1_decisions} == {d1.decision_id, d2.decision_id}

    @pytest.mark.asyncio
    async def test_tenant_bound_store_rejects_foreign_tenant(self, store):
        """A decision from another tenant never lands in this tenant's store (GDPR Art. 32)."""
        foreign = DecisionRecorder("tenant-b").create_decision(
            choice_type="skill_selection", candidates=["a"], chosen="a", session_id="s9",
        )
        with pytest.raises(ValueError):
            await store.write_event(_decision_event(foreign), "tenant-b")
        with pytest.raises(ValueError):
            await store.write_event(_decision_event(foreign), TENANT)
        assert await store.read_decisions(tenant_id=TENANT, session_id="s9") == []
