"""E2E tests for Skill System Wiring (Phase 4 full lifecycle)."""

import pytest
from pathlib import Path
from tempfile import TemporaryDirectory

from core.learning.skill_integration import SkillLearningHooks
from core.learning.event_store import EventStore as _LearningEventStore
from core.learning.skill_selector_integration import SkillSelectorWithLearning
from core.learning.skill_executor_integration import SkillExecutorWithLearning
from core.learning.skill_feedback_integration import SkillFeedbackWithLearning
from core.learning.event_emitter import EventEmitter


@pytest.mark.asyncio
async def test_full_skill_lifecycle():
    """E2E: Select → Execute → Feedback full cycle with learning capture."""
    with TemporaryDirectory() as tmpdir:
        tenant_home = Path(tmpdir) / "tenants" / "_default"
        tenant_home.mkdir(parents=True, exist_ok=True)

        emitter = EventEmitter(_LearningEventStore(tenant_home))
        await emitter.start()
        hooks = SkillLearningHooks("_default", emitter)

        # 1. SELECTOR: Choose a skill
        selector = SkillSelectorWithLearning(hooks)
        candidates = ["skill_ranking", "skill_summarize", "skill_review"]
        confidence_scores = {
            "skill_ranking": 0.92,
            "skill_summarize": 0.78,
            "skill_review": 0.65,
        }

        chosen, decision_id = await selector.select_skill(
            candidates=candidates,
            confidence_scores=confidence_scores,
            reasoning="Ranking has highest confidence",
            session_id="session-789",
        )

        assert chosen == "skill_ranking"
        assert decision_id is not None

        # 2. EXECUTOR: Run the skill and measure latency
        executor = SkillExecutorWithLearning(hooks)

        async def mock_skill_fn():
            """Mock skill execution."""
            import asyncio
            await asyncio.sleep(0.01)  # Simulate 10ms latency
            return {"result": "ranking complete"}

        result = await executor.execute_skill(
            skill_name=chosen,
            skill_fn=mock_skill_fn,
            decision_id=decision_id,
            session_id="session-789",
        )

        assert result["result"] == "ranking complete"

        # 3. FEEDBACK: User confirms outcome
        feedback = SkillFeedbackWithLearning(hooks)
        await feedback.record_feedback(
            decision_id=decision_id,
            session_id="session-789",
            user_response="good",
            rating=5,
        )

        await emitter.flush()

        # Verify full cycle captured
        decisions = await emitter.store.read_decisions(
            tenant_id="_default",
            session_id="session-789"
        )
        assert len(decisions) == 1
        assert decisions[0]["chosen"] == "skill_ranking"
        assert decisions[0]["confidence_score"] == 0.92

        metrics = await emitter.store.read_metrics(
            tenant_id="_default",
            skill_name="skill_ranking"
        )
        assert len(metrics) == 1
        assert metrics[0]["metric_type"] == "latency"
        assert metrics[0]["value"] >= 10.0  # At least 10ms

        outcomes = await emitter.store.read_outcomes(
            tenant_id="_default",
            session_id="session-789"
        )
        assert len(outcomes) == 1
        assert outcomes[0]["outcome"] == "success"
        assert outcomes[0]["rating"] == 5

        # Verify linkage
        assert outcomes[0]["decision_id"] == decision_id

        await emitter.stop()


@pytest.mark.asyncio
async def test_selector_with_confidence():
    """Test: Skill selection captures confidence scores."""
    with TemporaryDirectory() as tmpdir:
        tenant_home = Path(tmpdir) / "tenants" / "_default"
        tenant_home.mkdir(parents=True, exist_ok=True)

        emitter = EventEmitter(_LearningEventStore(tenant_home))
        await emitter.start()
        hooks = SkillLearningHooks("_default", emitter)
        selector = SkillSelectorWithLearning(hooks)

        chosen, decision_id = await selector.select_skill(
            candidates=["a", "b", "c"],
            confidence_scores={"a": 0.85, "b": 0.70, "c": 0.60},
            session_id="s1",
        )

        await emitter.flush()

        decisions = await emitter.store.read_decisions(
            tenant_id="_default",
            session_id="s1"
        )
        assert decisions[0]["confidence_score"] == 0.85

        await emitter.stop()


@pytest.mark.asyncio
async def test_executor_latency_measurement():
    """Test: Skill execution measures latency accurately."""
    with TemporaryDirectory() as tmpdir:
        tenant_home = Path(tmpdir) / "tenants" / "_default"
        tenant_home.mkdir(parents=True, exist_ok=True)

        emitter = EventEmitter(_LearningEventStore(tenant_home))
        await emitter.start()
        hooks = SkillLearningHooks("_default", emitter)
        executor = SkillExecutorWithLearning(hooks)

        async def slow_skill():
            import asyncio
            await asyncio.sleep(0.05)  # 50ms
            return "done"

        await executor.execute_skill(
            skill_name="slow_skill",
            skill_fn=slow_skill,
            decision_id="d1",
            session_id="s2",
        )

        await emitter.flush()

        metrics = await emitter.store.read_metrics(
            tenant_id="_default",
            session_id="s2"
        )
        assert len(metrics) == 1
        assert metrics[0]["value"] >= 50.0  # At least 50ms

        await emitter.stop()


@pytest.mark.asyncio
async def test_feedback_outcome_mapping():
    """Test: User feedback maps to outcome types correctly."""
    with TemporaryDirectory() as tmpdir:
        tenant_home = Path(tmpdir) / "tenants" / "_default"
        tenant_home.mkdir(parents=True, exist_ok=True)

        emitter = EventEmitter(_LearningEventStore(tenant_home))
        await emitter.start()
        hooks = SkillLearningHooks("_default", emitter)
        feedback = SkillFeedbackWithLearning(hooks)

        # Test different feedback types
        feedbacks = [
            ("good", "success"),
            ("bad", "failure"),
            ("partial", "partial"),
        ]

        for i, (user_response, expected_outcome) in enumerate(feedbacks):
            await feedback.record_feedback(
                decision_id=f"d{i}",
                session_id="s3",
                user_response=user_response,
                rating=3,
            )

        await emitter.flush()

        outcomes = await emitter.store.read_outcomes(
            tenant_id="_default",
            session_id="s3"
        )
        assert len(outcomes) == 3

        for i, (_, expected_outcome) in enumerate(feedbacks):
            found = [o for o in outcomes if o["decision_id"] == f"d{i}"]
            assert found[0]["outcome"] == expected_outcome

        await emitter.stop()
