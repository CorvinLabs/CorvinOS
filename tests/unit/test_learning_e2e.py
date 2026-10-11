"""E2E tests for Learning Infrastructure (ADR-0314) through SkillSystemIntegration.

Real contract (adversarial review D-08 / N-01): ``SkillSystemIntegration``
wraps ``EventEmitter(EventStore(tenant_home))`` — synchronous, non-blocking
``emit_learning_event(learning_events.LearningEvent) -> bool``,
``stop_event_emitter()`` (flush + join) and ``read_learning_events()`` over
the tenant's ``learning/events/*.jsonl``. The previous version used the
pre-``df125e48`` API (``await start_event_emitter()/flush_events()``,
``event_schema`` events) that no longer exists.
"""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from core.learning.learning_events import EventType, LearningEvent
from core.skills.learning_loop import SkillLearningManager
from core.skills.store import InMemorySkillStore

TENANT = "_default"


@pytest.fixture
def temp_tenant_home():
    """Create a temporary tenant home directory."""
    with TemporaryDirectory() as tmpdir:
        tenant_home = Path(tmpdir) / "tenants" / TENANT
        tenant_home.mkdir(parents=True, exist_ok=True)
        yield tenant_home




def _ev(event_type: EventType, skill_id: str, session_id: str = "session_1", **signal) -> LearningEvent:
    return LearningEvent.create(
        event_type=event_type, skill_id=skill_id, tenant_id=TENANT,
        signal={"session_id": session_id, **signal},
        lom="tests/unit/test_learning_e2e.py",
    )


def _on_disk(tenant_home: Path) -> list[dict]:
    out: list[dict] = []
    for f in sorted((tenant_home / "learning" / "events").glob("*.jsonl")):
        out.extend(json.loads(l) for l in f.read_text().splitlines() if l.strip())
    return out


class TestLearningE2E:
    """End-to-end tests for learning infrastructure."""


    def test_multiple_event_types(self, temp_tenant_home):
        """Emit multiple event types and verify all persist."""
        system = _system(temp_tenant_home)
        try:
            for ev in (
                _ev(EventType.CONFIDENCE, "ranking", relevance_score=0.95),
                _ev(EventType.FEEDBACK, "os.feedback", feedback_type="rating", feedback_value=4),
                _ev(EventType.OUTCOME, "ranking", decision_id="d1", outcome_type="latency",
                    outcome_value=1250.5, window_seconds=300),
            ):
                assert system.emit_learning_event(ev) is True
        finally:
            system.stop_event_emitter()

        persisted = system.read_learning_events()
        assert len(persisted) == 3
        assert {e.event_type for e in persisted} == {EventType.CONFIDENCE, EventType.FEEDBACK, EventType.OUTCOME}

    def test_event_filtering(self, temp_tenant_home):
        """Filter events by skill name, type and session."""
        system = _system(temp_tenant_home)
        try:
            system.emit_learning_event(_ev(EventType.CONFIDENCE, "ranking"))
            system.emit_learning_event(_ev(EventType.CONFIDENCE, "code_review", session_id="session_2"))
            system.emit_learning_event(_ev(EventType.FEEDBACK, "ranking"))
        finally:
            system.stop_event_emitter()

        ranking_events = system.read_learning_events(skill_name="ranking")
        assert len(ranking_events) == 2
        assert all(e.skill_id == "ranking" for e in ranking_events)

        conf = system.read_learning_events(event_type=EventType.CONFIDENCE)
        assert {e.skill_id for e in conf} == {"ranking", "code_review"}

        s2 = system.read_learning_events(session_id="session_2")
        assert [e.skill_id for e in s2] == ["code_review"]



