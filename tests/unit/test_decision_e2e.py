"""E2E tests for Decision History (ADR-0316)."""

import pytest
import asyncio
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from core.learning.decision_history import DecisionRecorder
from core.learning.event_emitter import EventEmitter
from core.learning.event_schema import LearningEventType


@pytest.fixture
def temp_tenant_home():
    """Create a temporary tenant home directory."""
    with TemporaryDirectory() as tmpdir:
        tenant_home = Path(tmpdir) / "tenants" / "_default"
        tenant_home.mkdir(parents=True, exist_ok=True)
        yield tenant_home


class TestDecisionE2E:
    """End-to-end tests for decision history."""

    @pytest.mark.asyncio
    async def test_record_and_emit_decision(self, temp_tenant_home):
        """Record a decision and emit as event."""
        recorder = DecisionRecorder("_default")
        emitter = EventEmitter(temp_tenant_home, "_default")

        await emitter.start()

        # Record decision
        decision = recorder.create_decision(
            choice_type="skill_selection",
            candidates=["ranking", "summarizer", "code_review"],
            chosen="ranking",
            session_id="session-123",
            confidence_score=0.85,
            reasoning="High relevance + low variance",
        )

        # Emit as event
        await emitter.emit_decision(
            decision_id=decision.decision_id,
            choice_type=decision.choice_type,
            candidates=decision.candidates,
            chosen=decision.chosen,
            session_id=decision.session_id,
            confidence_score=decision.confidence_score,
            reasoning=decision.reasoning,
        )

        await emitter.flush()
        await emitter.stop()

        # Read back
        decisions = await emitter.store.read_decisions(tenant_id="_default", session_id="session-123")
        assert len(decisions) == 1
        assert decisions[0]["chosen"] == "ranking"
        assert decisions[0]["confidence_score"] == 0.85

    @pytest.mark.asyncio
    async def test_multiple_decisions_filtering(self, temp_tenant_home):
        """Emit multiple decisions and filter by choice_type."""
        recorder = DecisionRecorder("_default")
        emitter = EventEmitter(temp_tenant_home, "_default")

        await emitter.start()

        # Record multiple decisions
        d1 = recorder.create_decision(
            choice_type="skill_selection",
            candidates=["a", "b"],
            chosen="a",
            session_id="s1",
        )

        d2 = recorder.create_decision(
            choice_type="model_choice",
            candidates=["gpt-4", "claude"],
            chosen="claude",
            session_id="s1",
        )

        d3 = recorder.create_decision(
            choice_type="skill_selection",
            candidates=["c", "d"],
            chosen="c",
            session_id="s2",
        )

        # Emit all
        for d in [d1, d2, d3]:
            await emitter.emit_decision(
                decision_id=d.decision_id,
                choice_type=d.choice_type,
                candidates=d.candidates,
                chosen=d.chosen,
                session_id=d.session_id,
            )

        await emitter.flush()
        await emitter.stop()

        # Filter by skill_selection
        skill_decisions = await emitter.store.read_decisions(
            tenant_id="_default", choice_type="skill_selection"
        )
        assert len(skill_decisions) == 2

        # Filter by session
        s1_decisions = await emitter.store.read_decisions(tenant_id="_default", session_id="s1")
        assert len(s1_decisions) == 2
