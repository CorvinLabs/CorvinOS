"""D-08 — SkillSystemIntegration uses the CURRENT EventEmitter API.

``EventEmitter(EventStore(tenant_home))`` + sync ``emit()``; events land in
``<tenant_home>/learning/events/YYYY-MM-DD.jsonl`` and are readable back
through the integration's tenant-scoped query.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from core.learning.learning_events import EventType, LearningEvent
from core.skills.integration import SkillSystemIntegration


def _integration(tenant_home):
    return SkillSystemIntegration(
        learning_manager=MagicMock(),
        grading_manager=MagicMock(),
        telemetry_manager=MagicMock(),
        tenant_home=tenant_home,
        tenant_id="test_tenant",
    )


def test_emit_and_read_back(tmp_path):
    tenant_home = tmp_path / "tenants" / "test_tenant" / "global"
    tenant_home.mkdir(parents=True)
    integ = _integration(tenant_home)
    try:
        assert integ.emit_confidence_score(
            skill_name="os.router", session_id="s1",
            relevance=0.9, reliability=0.8, combined=0.85, band="high",
        ) is True
        ev = LearningEvent.create(EventType.OUTCOME, skill_id="os.router",
                                  tenant_id="test_tenant", signal={"session_id": "s2", "ok": True})
        assert integ.emit_learning_event(ev) is True
    finally:
        integ.stop_event_emitter()  # flush + join before reading

    files = list((tenant_home / "learning" / "events").glob("*.jsonl"))
    assert files, "events must be persisted on disk"

    all_events = integ.read_learning_events()
    assert {e.event_type for e in all_events} >= {EventType.CONFIDENCE, EventType.OUTCOME}
    only_s1 = integ.read_learning_events(session_id="s1")
    assert len(only_s1) == 1 and only_s1[0].signal["band"] == "high"
    assert integ.read_learning_events(event_type=EventType.OUTCOME)[0].signal["ok"] is True


def test_without_tenant_home_is_disabled_not_broken():
    integ = SkillSystemIntegration(MagicMock(), MagicMock(), MagicMock())
    assert integ.event_emitter is None
    assert integ.emit_confidence_score("s", "x", 1, 1, 1, "b") is False
    assert integ.read_learning_events() == []
    assert integ.get_system_status()["event_emitter"]["enabled"] is False
