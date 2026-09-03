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
from core.skills.integration import SkillSystemIntegration
from core.skills.grader import GradingManager
from core.skills.learning_loop import SkillLearningManager
from core.skills.store import InMemorySkillStore
from core.skills.telemetry import MetricsCollector, NoOpPublisher
from core.skills.telemetry_manager import TelemetryManager
from core.skills.graders.heuristic import HeuristicGrader

TENANT = "_default"


@pytest.fixture
def temp_tenant_home():
    """Create a temporary tenant home directory."""
    with TemporaryDirectory() as tmpdir:
        tenant_home = Path(tmpdir) / "tenants" / TENANT
        tenant_home.mkdir(parents=True, exist_ok=True)
        yield tenant_home


def _system(tenant_home: Path) -> SkillSystemIntegration:
    store = InMemorySkillStore()
    learning = SkillLearningManager(store)
    grading = GradingManager(store, HeuristicGrader())
    collector = MetricsCollector("test", "1.0")
    telemetry = TelemetryManager(collector, NoOpPublisher())
    return SkillSystemIntegration(learning, grading, telemetry, tenant_home=tenant_home, tenant_id=TENANT)


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

    def test_emit_event_through_integration(self, temp_tenant_home):
        """Emit a learning event through SkillSystemIntegration; it lands on disk."""
        system = _system(temp_tenant_home)
        try:
            queued = system.emit_learning_event(
                _ev(EventType.CONFIDENCE, "ranking", relevance_score=0.95, reliability_score=0.87)
            )
            assert queued is True
        finally:
            system.stop_event_emitter()

        persisted = system.read_learning_events()
        assert len(persisted) == 1
        assert persisted[0].event_type == EventType.CONFIDENCE
        assert persisted[0].skill_id == "ranking"
        assert persisted[0].signal["relevance_score"] == 0.95

        disk = _on_disk(temp_tenant_home)
        assert len(disk) == 1
        assert disk[0]["event_type"] == "confidence"
        assert disk[0]["tenant_id"] == TENANT

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

    def test_emit_confidence_score_helper(self, temp_tenant_home):
        """ADR-0315 helper emits a CONFIDENCE event with the band + scores."""
        system = _system(temp_tenant_home)
        try:
            assert system.emit_confidence_score("ranking", "s9", 0.9, 0.8, 0.85, "high", "ok") is True
        finally:
            system.stop_event_emitter()
        (ev,) = system.read_learning_events(event_type=EventType.CONFIDENCE)
        assert ev.signal["band"] == "high"
        assert ev.signal["session_id"] == "s9"
        assert ev.lom

    def test_wrong_tenant_event_is_not_readable_as_this_tenant(self, temp_tenant_home):
        """The store filters by tenant on read (GDPR Art. 32)."""
        system = _system(temp_tenant_home)
        try:
            other = LearningEvent.create(EventType.FEEDBACK, "x", tenant_id="tenant_b", signal={})
            system.emit_learning_event(other)
        finally:
            system.stop_event_emitter()
        assert system.read_learning_events() == []

    def test_no_tenant_home_means_no_emitter(self):
        """Without a tenant_home the integration reports the emitter disabled and drops."""
        store = InMemorySkillStore()
        system = SkillSystemIntegration(
            SkillLearningManager(store), GradingManager(store, HeuristicGrader()),
            TelemetryManager(MetricsCollector("t", "1.0"), NoOpPublisher()),
        )
        assert system.emit_learning_event(_ev(EventType.FEEDBACK, "x")) is False
        assert system.get_system_status()["event_emitter"]["enabled"] is False
        assert system.read_learning_events() == []
