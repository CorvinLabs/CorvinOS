"""E2E tests for Outcome Feedback (ADR-0317)."""

import pytest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from core.learning.outcome_feedback import OutcomeRecorder, OutcomeType
from core.learning.event_emitter import EventEmitter


@pytest.fixture
def temp_tenant_home():
    """Create a temporary tenant home directory."""
    with TemporaryDirectory() as tmpdir:
        tenant_home = Path(tmpdir) / "tenants" / "_default"
        tenant_home.mkdir(parents=True, exist_ok=True)
        yield tenant_home


class TestOutcomeE2E:
    """End-to-end tests for outcome feedback."""

    @pytest.mark.asyncio
    async def test_record_and_emit_outcome(self, temp_tenant_home):
        """Record an outcome and emit as event."""
        recorder = OutcomeRecorder("_default")
        emitter = EventEmitter(temp_tenant_home, "_default")

        await emitter.start()

        # Record outcome
        outcome = recorder.record_outcome(
            decision_id="d1",
            session_id="session-123",
            outcome=OutcomeType.SUCCESS,
            feedback_text="User said it was correct",
            rating=5,
        )

        # Emit as event
        await emitter.emit_outcome(
            outcome_id=outcome.outcome_id,
            decision_id=outcome.decision_id,
            session_id=outcome.session_id,
            outcome=outcome.outcome.value,
            feedback_text=outcome.feedback_text,
            rating=outcome.rating,
        )

        await emitter.flush()
        await emitter.stop()

        # Read back
        outcomes = await emitter.store.read_outcomes(tenant_id="_default", session_id="session-123")
        assert len(outcomes) == 1
        assert outcomes[0]["outcome"] == "success"
        assert outcomes[0]["rating"] == 5

    @pytest.mark.asyncio
    async def test_decision_outcome_linkage(self, temp_tenant_home):
        """Link decisions to outcomes via decision_id."""
        from core.learning.decision_history import DecisionRecorder

        decision_recorder = DecisionRecorder("_default")
        outcome_recorder = OutcomeRecorder("_default")
        emitter = EventEmitter(temp_tenant_home, "_default")

        await emitter.start()

        # Create decision
        decision = decision_recorder.create_decision(
            choice_type="skill_selection",
            candidates=["a", "b"],
            chosen="a",
            session_id="session-456",
            confidence_score=0.8,
        )

        # Emit decision
        await emitter.emit_decision(
            decision_id=decision.decision_id,
            choice_type=decision.choice_type,
            candidates=decision.candidates,
            chosen=decision.chosen,
            session_id=decision.session_id,
            confidence_score=decision.confidence_score,
        )

        # Create and emit outcome for that decision
        outcome = outcome_recorder.record_outcome(
            decision_id=decision.decision_id,
            session_id=decision.session_id,
            outcome=OutcomeType.SUCCESS,
        )

        await emitter.emit_outcome(
            outcome_id=outcome.outcome_id,
            decision_id=outcome.decision_id,
            session_id=outcome.session_id,
            outcome=outcome.outcome.value,
        )

        await emitter.flush()
        await emitter.stop()

        # Verify linkage
        outcomes = await emitter.store.read_outcomes(
            "_default",
            decision_id=decision.decision_id
        )
        assert len(outcomes) == 1
        assert outcomes[0]["decision_id"] == decision.decision_id

    @pytest.mark.asyncio
    async def test_multiple_outcomes_filtering(self, temp_tenant_home):
        """Emit multiple outcomes and filter by session."""
        recorder = OutcomeRecorder("_default")
        emitter = EventEmitter(temp_tenant_home, "_default")

        await emitter.start()

        # Record multiple outcomes
        o1 = recorder.record_outcome(
            decision_id="d1",
            session_id="s1",
            outcome=OutcomeType.SUCCESS,
        )

        o2 = recorder.record_outcome(
            decision_id="d2",
            session_id="s1",
            outcome=OutcomeType.FAILURE,
        )

        o3 = recorder.record_outcome(
            decision_id="d3",
            session_id="s2",
            outcome=OutcomeType.PARTIAL,
        )

        # Emit all
        for o in [o1, o2, o3]:
            await emitter.emit_outcome(
                outcome_id=o.outcome_id,
                decision_id=o.decision_id,
                session_id=o.session_id,
                outcome=o.outcome.value,
            )

        await emitter.flush()
        await emitter.stop()

        # Filter by session
        s1_outcomes = await emitter.store.read_outcomes(tenant_id="_default", session_id="s1")
        assert len(s1_outcomes) == 2
        assert any(o["outcome"] == "success" for o in s1_outcomes)
        assert any(o["outcome"] == "failure" for o in s1_outcomes)
