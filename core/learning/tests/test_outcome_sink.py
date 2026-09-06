"""``core.learning.outcome_sink`` — the sink side of the ADR-0314 loop closure."""
from __future__ import annotations

import json
import time
from pathlib import Path

from core.learning.event_emitter import EventEmitter
from core.learning.event_store import EventStore
from core.learning.learning_events import EventType
from core.learning.outcome_sink import OUTCOME_SKILL_ID, emit_task_outcome, recent_outcomes

TENANT = "_default"


def _wait(pred, timeout=3.0):
    for _ in range(int(timeout / 0.05)):
        if pred():
            return True
        time.sleep(0.05)
    return pred()


class TestEmitTaskOutcome:
    def test_records_content_free_outcome_event(self, tmp_path: Path):
        store = EventStore(tmp_path / "t")
        emitter = EventEmitter(store)
        try:
            ok = emit_task_outcome(
                tenant_id=TENANT, task_id="task-1", status="completed", exit_code=0,
                duration_ms=123, engine="native", task_type="chat", emitter=emitter,
            )
            assert ok is True
        finally:
            emitter.stop(timeout=5.0)
        events = store.query_events(TENANT, event_type=EventType.OUTCOME)
        assert len(events) == 1
        ev = events[0]
        assert ev.skill_id == OUTCOME_SKILL_ID
        assert ev.signal["success"] is True
        assert ev.signal["engine"] == "native" and ev.signal["duration_ms"] == 123
        # content-free: no instruction/output/user fields exist in the signal
        assert not {"instruction", "output", "user_id", "prompt"} & set(ev.signal)

    def test_failed_task_is_not_success(self, tmp_path: Path):
        emitter = EventEmitter(EventStore(tmp_path / "t"))
        try:
            assert emit_task_outcome(tenant_id=TENANT, task_id="t", status="failed", exit_code=1, emitter=emitter)
        finally:
            emitter.stop(timeout=5.0)
        ev = EventStore(tmp_path / "t").query_events(TENANT, event_type=EventType.OUTCOME)[0]
        assert ev.signal["success"] is False

    def test_missing_tenant_or_bad_status_is_dropped(self, tmp_path: Path):
        emitter = EventEmitter(EventStore(tmp_path / "t"))
        try:
            assert emit_task_outcome(tenant_id=None, task_id="t", status="completed", emitter=emitter) is False
            assert emit_task_outcome(tenant_id="", task_id="t", status="completed", emitter=emitter) is False
            assert emit_task_outcome(tenant_id=TENANT, task_id="t", status="running", emitter=emitter) is False
        finally:
            emitter.stop(timeout=5.0)
        assert EventStore(tmp_path / "t").count_events(TENANT) == 0

    def test_no_booted_emitter_means_no_record_not_an_error(self, monkeypatch):
        from core.skills import skill_registry_phase1 as reg

        monkeypatch.setattr(reg, "_global_registry", None)
        assert emit_task_outcome(tenant_id=TENANT, task_id="t", status="completed") is False


class TestRecentOutcomes:
    def test_counts_over_the_tail_only(self, tmp_path: Path):
        store = EventStore(tmp_path / "t")
        emitter = EventEmitter(store)
        try:
            for i in range(12):
                emit_task_outcome(
                    tenant_id=TENANT, task_id=f"t{i}", status="completed" if i % 3 else "failed",
                    exit_code=0 if i % 3 else 1, emitter=emitter,
                )
        finally:
            emitter.stop(timeout=5.0)
        successes, total = recent_outcomes(TENANT, limit=10, store=store)
        assert total == 10
        # tail = i in 2..11 → failures at i = 3, 6, 9 → 7 successes
        assert successes == 7

    def test_empty_store_is_no_evidence(self, tmp_path: Path):
        assert recent_outcomes(TENANT, store=EventStore(tmp_path / "t")) == (0, 0)
        assert recent_outcomes(TENANT, store=None) in ((0, 0),)
