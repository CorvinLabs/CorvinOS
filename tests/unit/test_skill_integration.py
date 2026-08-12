"""Tests for Skill System Integration (Phase 4)."""

import pytest
from pathlib import Path
from tempfile import TemporaryDirectory
import asyncio

from core.learning.skill_integration import SkillLearningHooks
from core.learning.event_emitter import EventEmitter
from core.learning.outcome_feedback import OutcomeType


@pytest.mark.asyncio
async def test_skill_selection_hook():
    """Hook: skill selection emits decision event."""
    with TemporaryDirectory() as tmpdir:
        tenant_home = Path(tmpdir) / "tenants" / "_default"
        tenant_home.mkdir(parents=True, exist_ok=True)

        emitter = EventEmitter(tenant_home, "_default")
        await emitter.start()
        hooks = SkillLearningHooks("_default", emitter)

        decision_id = await hooks.on_skill_selection(
            candidates=["ranking", "summarizer", "code_review"],
            chosen="ranking",
            session_id="session-123",
            confidence_score=0.85,
            reasoning="High relevance",
        )

        assert decision_id is not None
        await hooks.emitter.flush()

        decisions = await hooks.emitter.store.read_decisions(
            tenant_id="_default",
            session_id="session-123"
        )
        assert len(decisions) == 1
        assert decisions[0]["chosen"] == "ranking"

        await emitter.stop()


@pytest.mark.asyncio
async def test_skill_executed_hook():
    """Hook: skill execution emits latency metric."""
    with TemporaryDirectory() as tmpdir:
        tenant_home = Path(tmpdir) / "tenants" / "_default"
        tenant_home.mkdir(parents=True, exist_ok=True)

        emitter = EventEmitter(tenant_home, "_default")
        await emitter.start()
        hooks = SkillLearningHooks("_default", emitter)

        await hooks.on_skill_executed(
            decision_id="d1",
            session_id="session-123",
            skill_name="ranking",
            latency_ms=250.0,
        )

        await hooks.emitter.flush()

        metrics = await hooks.emitter.store.read_metrics(
            tenant_id="_default",
            skill_name="ranking"
        )
        assert len(metrics) == 1
        assert metrics[0]["metric_type"] == "latency"
        assert metrics[0]["value"] == 250.0

        await emitter.stop()


@pytest.mark.asyncio
async def test_skill_outcome_hook():
    """Hook: user feedback emits outcome event."""
    with TemporaryDirectory() as tmpdir:
        tenant_home = Path(tmpdir) / "tenants" / "_default"
        tenant_home.mkdir(parents=True, exist_ok=True)

        emitter = EventEmitter(tenant_home, "_default")
        await emitter.start()
        hooks = SkillLearningHooks("_default", emitter)

        await hooks.on_skill_outcome(
            decision_id="d1",
            session_id="session-123",
            outcome=OutcomeType.SUCCESS,
            user_feedback="Correct result",
            rating=5,
        )

        await hooks.emitter.flush()

        outcomes = await hooks.emitter.store.read_outcomes(
            tenant_id="_default",
            session_id="session-123"
        )
        assert len(outcomes) == 1
        assert outcomes[0]["outcome"] == "success"
        assert outcomes[0]["rating"] == 5

        await emitter.stop()


@pytest.mark.asyncio
async def test_preference_changed_hook():
    """Hook: preference change emits preference event."""
    with TemporaryDirectory() as tmpdir:
        tenant_home = Path(tmpdir) / "tenants" / "_default"
        tenant_home.mkdir(parents=True, exist_ok=True)

        emitter = EventEmitter(tenant_home, "_default")
        await emitter.start()
        hooks = SkillLearningHooks("_default", emitter)

        await hooks.on_preference_changed(
            preference_type="decision_style",
            preference_value="pragmatic",
            session_id="session-123",
        )

        await hooks.emitter.flush()

        prefs = await hooks.emitter.store.read_preferences(
            tenant_id="_default"
        )
        assert len(prefs) == 1
        assert prefs[0]["preference_type"] == "decision_style"
        assert prefs[0]["preference_value"] == "pragmatic"

        await emitter.stop()


@pytest.mark.asyncio
async def test_full_lifecycle():
    """Full skill lifecycle: select → execute → outcome."""
    with TemporaryDirectory() as tmpdir:
        tenant_home = Path(tmpdir) / "tenants" / "_default"
        tenant_home.mkdir(parents=True, exist_ok=True)

        emitter = EventEmitter(tenant_home, "_default")
        await emitter.start()
        hooks = SkillLearningHooks("_default", emitter)

        # 1. Select skill
        decision_id = await hooks.on_skill_selection(
            candidates=["skill-a", "skill-b"],
            chosen="skill-a",
            session_id="session-456",
            confidence_score=0.9,
        )

        # 2. Execute skill (measure latency)
        await hooks.on_skill_executed(
            decision_id=decision_id,
            session_id="session-456",
            skill_name="skill-a",
            latency_ms=120.0,
        )

        # 3. User provides outcome feedback
        await hooks.on_skill_outcome(
            decision_id=decision_id,
            session_id="session-456",
            outcome=OutcomeType.SUCCESS,
            user_feedback="Excellent",
            rating=5,
        )

        await hooks.emitter.flush()

        # Verify full lifecycle
        decisions = await hooks.emitter.store.read_decisions(
            tenant_id="_default",
            session_id="session-456"
        )
        assert len(decisions) == 1

        metrics = await hooks.emitter.store.read_metrics(
            tenant_id="_default",
            session_id="session-456"
        )
        assert len(metrics) == 1

        outcomes = await hooks.emitter.store.read_outcomes(
            tenant_id="_default",
            session_id="session-456"
        )
        assert len(outcomes) == 1

        # All linked via decision_id
        assert decisions[0]["decision_id"] == decision_id
        assert outcomes[0]["decision_id"] == decision_id

        await emitter.stop()
