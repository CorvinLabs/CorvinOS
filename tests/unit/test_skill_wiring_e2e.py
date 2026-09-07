"""Skill system wiring (Phase 4): selector → executor → feedback over the REAL
emitter/store (rewritten 2026-09-07 against the actual sync ``EventEmitter``
API; the old version awaited ``start``/``flush``/``read_*`` that never existed).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from core.learning.event_emitter import EventEmitter
from core.learning.event_store import EventStore as _LearningEventStore
from core.learning.learning_events import EventType
from core.learning.skill_executor_integration import SkillExecutorWithLearning
from core.learning.skill_feedback_integration import SkillFeedbackWithLearning
from core.learning.skill_integration import SkillLearningHooks
from core.learning.skill_selector_integration import SkillSelectorWithLearning


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch):
    home = tmp_path / "corvin-home"
    tenant_home = home / "tenants" / "_default"
    tenant_home.mkdir(parents=True)
    monkeypatch.setenv("CORVIN_HOME", str(home))
    monkeypatch.setenv("VOICE_AUDIT_PATH", str(home / "audit.jsonl"))
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    store = _LearningEventStore(tenant_home)
    emitter = EventEmitter(store)
    yield store, emitter
    emitter.stop(timeout=5.0)


def _events(store, event_type):
    return store.query_events(tenant_id="_default", event_type=event_type)


@pytest.mark.asyncio
async def test_full_skill_lifecycle(sandbox):
    store, emitter = sandbox
    hooks = SkillLearningHooks("_default", emitter)

    selector = SkillSelectorWithLearning(hooks)
    chosen, decision_id = await selector.select_skill(
        candidates=["skill_ranking", "skill_summarize", "skill_review"],
        confidence_scores={"skill_ranking": 0.92, "skill_summarize": 0.78, "skill_review": 0.65},
        reasoning="Ranking has highest confidence",
        session_id="session-789",
    )
    assert chosen == "skill_ranking"
    assert decision_id

    executor = SkillExecutorWithLearning(hooks)

    async def mock_skill_fn():
        await asyncio.sleep(0.01)
        return {"result": "ranking complete"}

    result = await executor.execute_skill(
        skill_name=chosen, skill_fn=mock_skill_fn, decision_id=decision_id, session_id="session-789",
    )
    assert result["result"] == "ranking complete"

    feedback = SkillFeedbackWithLearning(hooks)
    await feedback.record_feedback(
        decision_id=decision_id, session_id="session-789", user_response="good", rating=5,
    )
    emitter.stop(timeout=5.0)

    decisions = _events(store, EventType.DECISION)
    assert len(decisions) == 1
    assert decisions[0].signal["chosen"] == "skill_ranking"
    assert decisions[0].signal["context"]["confidence_score"] == 0.92

    metrics = _events(store, EventType.METRIC)
    assert len(metrics) == 1
    assert metrics[0].signal["metric_name"] == "latency"
    assert metrics[0].signal["value"] >= 10.0

    outcomes = _events(store, EventType.OUTCOME)
    assert len(outcomes) == 1
    assert outcomes[0].signal["outcome_type"] == "success"
    assert outcomes[0].signal["outcome_value"] == 5
    assert outcomes[0].signal["decision_id"] == decision_id


@pytest.mark.asyncio
async def test_executor_measures_latency_even_when_the_skill_raises(sandbox):
    store, emitter = sandbox
    hooks = SkillLearningHooks("_default", emitter)
    executor = SkillExecutorWithLearning(hooks)

    async def boom():
        await asyncio.sleep(0.005)
        raise RuntimeError("skill failed")

    with pytest.raises(RuntimeError):
        await executor.execute_skill(
            skill_name="skill_x", skill_fn=boom, decision_id="d-x", session_id="s-x",
        )
    emitter.stop(timeout=5.0)

    metrics = _events(store, EventType.METRIC)
    assert len(metrics) == 1
    assert metrics[0].signal["value"] >= 5.0
