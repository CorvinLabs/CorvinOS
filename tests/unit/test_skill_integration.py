"""Skill learning hooks (Phase 4) — through the REAL emitter/store pair.

Rewritten 2026-09-07: the previous version awaited ``EventEmitter.start()`` /
``flush()`` and read ``store.read_decisions()`` — none of which ever existed
(the emitter is a sync, thread-backed ``emit(LearningEvent)``; the store is
``query_events``). Every test here persists through ``EventStore.write_event``,
which is audit-FIRST: the core chain is redirected to the sandbox so the tests
never touch the operator's live chain.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.learning.event_emitter import EventEmitter
from core.learning.event_store import EventStore as _LearningEventStore
from core.learning.learning_events import EventType
from core.learning.outcome_feedback import OutcomeType
from core.learning.skill_integration import SkillLearningHooks


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
async def test_skill_selection_hook(sandbox):
    """Hook: skill selection persists a DECISION event with the chosen skill."""
    store, emitter = sandbox
    hooks = SkillLearningHooks("_default", emitter)

    decision_id = await hooks.on_skill_selection(
        candidates=["ranking", "summarizer", "code_review"],
        chosen="ranking",
        session_id="session-123",
        confidence_score=0.85,
        reasoning="High relevance",
    )
    assert decision_id
    emitter.stop(timeout=5.0)

    decisions = _events(store, EventType.DECISION)
    assert len(decisions) == 1
    signal = decisions[0].signal
    assert signal["chosen"] == "ranking"
    assert signal["decision_id"] == decision_id
    assert signal["session_id"] == "session-123"
    assert decisions[0].skill_id == "ranking"


@pytest.mark.asyncio
async def test_skill_executed_hook_records_latency_metric(sandbox):
    store, emitter = sandbox
    hooks = SkillLearningHooks("_default", emitter)

    await hooks.on_skill_executed(
        decision_id="d1", session_id="session-123", skill_name="ranking", latency_ms=250.0,
    )
    emitter.stop(timeout=5.0)

    metrics = _events(store, EventType.METRIC)
    assert len(metrics) == 1
    assert metrics[0].signal["metric_name"] == "latency"
    assert metrics[0].signal["value"] == 250.0
    assert metrics[0].signal["decision_id"] == "d1"


@pytest.mark.asyncio
async def test_full_lifecycle_links_decision_metric_outcome(sandbox):
    """select → execute → outcome, all joined by decision_id; content-free."""
    store, emitter = sandbox
    hooks = SkillLearningHooks("_default", emitter)

    decision_id = await hooks.on_skill_selection(
        candidates=["skill-a", "skill-b"], chosen="skill-a",
        session_id="session-456", confidence_score=0.9,
    )
    await hooks.on_skill_executed(
        decision_id=decision_id, session_id="session-456", skill_name="skill-a", latency_ms=120.0,
    )
    await hooks.on_skill_outcome(
        decision_id=decision_id, session_id="session-456",
        outcome=OutcomeType.SUCCESS, user_feedback="Excellent", rating=5,
    )
    await hooks.on_preference_changed(
        preference_type="decision_style", preference_value="pragmatic", session_id="session-456",
    )
    emitter.stop(timeout=5.0)

    assert len(_events(store, EventType.DECISION)) == 1
    assert len(_events(store, EventType.METRIC)) == 1
    outcomes = _events(store, EventType.OUTCOME)
    assert len(outcomes) == 1
    assert outcomes[0].signal["decision_id"] == decision_id
    assert outcomes[0].signal["outcome_type"] == "success"
    assert outcomes[0].signal["outcome_value"] == 5
    # the free-text feedback never reaches the learning store
    assert "Excellent" not in str(outcomes[0].signal)
    prefs = _events(store, EventType.PREFERENCE)
    assert len(prefs) == 1
    assert prefs[0].signal["preference_key"] == "decision_style"
    assert hooks.dropped_events == 0
