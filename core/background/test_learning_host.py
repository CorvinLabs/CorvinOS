"""LearningHost supervision, persistence and idle behaviour (PLAN-0948 D3)."""
from __future__ import annotations

import asyncio
import threading
import time

import pytest

from core.background import learning_host as lh
from core.background import learning_inbox as li


@pytest.fixture(autouse=True)
def _own_home(tmp_path, monkeypatch):
    """Each test gets its own CORVIN_HOME (the harness sandbox is per session, and the inbox accumulates)."""
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    monkeypatch.delenv("CORVIN_LEARNING_HOST_IN_TESTS", raising=False)
    yield
    lh.stop("_default", timeout=1.0)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_an_event_in_the_inbox_is_consumed_once_and_survives_a_restart():
    li.emit("_default", "user_feedback", {"skill_id": "s1", "signal": -0.8, "data_sources": ["datahub:aa"]})
    from core.skills.os_skills.datahub_unified.regenerate import SourceRef

    live = lambda t: [SourceRef("datahub:aa", "s1", "aa", "", "")]   # noqa: E731 - the artifact exists
    host = lh.LearningHost("_default", sources=live)
    assert _run(host.tick()) == 1
    assert host.daemon.weight_learner.weights["datahub:aa"] < 0.5
    again = lh.LearningHost("_default", sources=live)
    assert again._restored and _run(again.tick()) == 0
    assert again.daemon.weight_learner.weights["datahub:aa"] == host.daemon.weight_learner.weights["datahub:aa"]


def test_a_partial_trailing_line_is_left_for_the_next_tick():
    li.emit("_default", "user_feedback", {"skill_id": "s1", "signal": 0.5, "data_sources": ["datahub:bb"]})
    path = li.day_files("_default")[-1]
    with open(path, "ab") as f:
        f.write(b'{"event_type":"user_feedback","tenant_id":"_default","payload":{"skill_id":"s2"')  # no newline
    events, cursor = li.read_from("_default", None)
    assert len(events) == 1
    with open(path, "ab") as f:
        f.write(b'}}\n')
    more, _ = li.read_from("_default", cursor)
    assert len(more) == 1 and more[0]["payload"]["skill_id"] == "s2"


def test_the_inbox_drops_everything_outside_the_closed_vocabulary():
    assert li.emit("_default", "user_feedback", {"skill_id": "s", "signal": 9, "user_text": "SECRET",
                                                  "data_sources": ["datahub:cc", 5, ""]})
    events, _ = li.read_from("_default", None)
    payload = events[0]["payload"]
    assert payload == {"skill_id": "s", "signal": 1.0, "data_sources": ["datahub:cc"]}
    assert not li.emit("_default", "bogus_type", {"skill_id": "s"})
    assert not li.emit("_default", "skill_executed", {"success": True})          # no skill id


def test_another_tenants_events_are_ignored():
    li.emit("_default", "user_feedback", {"skill_id": "s", "signal": 1, "data_sources": ["datahub:dd"]})
    path = li.day_files("_default")[-1]
    with open(path, "ab") as f:
        f.write(b'{"event_type":"user_feedback","tenant_id":"other","payload":{"skill_id":"x"}}\n')
    events, _ = li.read_from("_default", None)
    assert [e["tenant_id"] for e in events] == ["_default"]


def test_a_failing_tick_is_counted_and_the_loop_survives(monkeypatch):
    host = lh.LearningHost("_default")
    calls = {"n": 0}
    real = host.tick

    async def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return await real(*a, **k)

    monkeypatch.setattr(host, "tick", flaky)
    monkeypatch.setattr(lh, "MIN_INTERVAL_S", 0.01)
    monkeypatch.setattr(lh, "MAX_INTERVAL_S", 0.02)
    stop = threading.Event()
    t = threading.Thread(target=lambda: asyncio.run(host.run(stop)), daemon=True)
    t.start()
    deadline = time.time() + 5
    while host.ticks < 3 and time.time() < deadline:
        time.sleep(0.01)
    stop.set()
    t.join(5)
    assert host.errors == 1 and host.last_error == "RuntimeError" and host.ticks >= 3


def test_start_is_idempotent_and_stop_persists(monkeypatch):
    monkeypatch.setenv("CORVIN_LEARNING_HOST_IN_TESTS", "1")
    assert lh.start("_default") is True
    assert lh.start("_default") is False
    assert lh.status("_default")["alive"] is True
    lh.stop("_default")
    assert lh.status("_default") is None
    assert lh.state_path("_default").is_file()


def test_the_host_does_not_start_under_pytest_by_default():
    assert lh.start("_default") is False


def test_state_of_a_deleted_artifact_is_evicted_but_an_unreadable_listing_evicts_nothing():
    from core.skills.os_skills.datahub_unified.regenerate import SourceRef

    li.emit("_default", "user_feedback", {"skill_id": "assistant.datahub_aa", "signal": -0.5,
                                          "data_sources": ["datahub:aa"]})
    def broken(_t):
        raise OSError("listing failed")
    host = lh.LearningHost("_default", sources=broken)
    _run(host.tick())
    assert "datahub:aa" in host.daemon.weight_learner.weights      # could not look: keep
    host._sources = lambda t: []                                     # looked: artifact is gone
    host._last_source_check = 0
    _run(host.tick())
    assert "datahub:aa" not in host.daemon.weight_learner.weights


def test_a_second_host_on_the_same_tenant_stands_by(monkeypatch):
    first = lh.LearningHost("_default")
    assert first.lease.try_acquire()
    second = lh.LearningHost("_default")
    assert second.lease.try_acquire() is False       # flock is per open file description
    first.lease.release()
    assert second.lease.try_acquire() is True
    second.lease.release()


def test_a_line_finished_in_yesterdays_file_after_the_reader_moved_on_is_not_lost():
    d = li.daemon_dir("_default")
    d.mkdir(parents=True)
    (d / "inbox-20261010.jsonl").write_bytes(
        b'{"event_type":"user_feedback","tenant_id":"_default","payload":{"skill_id":"a1"}}\n'
        b'{"event_type":"user_feedback","tenant_id":"_default","payload":{"skill_id":"a2"')
    (d / "inbox-20261011.jsonl").write_bytes(
        b'{"event_type":"user_feedback","tenant_id":"_default","payload":{"skill_id":"b1"}}\n')
    ev, cur = li.read_from("_default", None)
    assert [e["payload"]["skill_id"] for e in ev] == ["a1", "b1"]
    with open(d / "inbox-20261010.jsonl", "ab") as f:
        f.write(b'}}\n')
    ev2, _ = li.read_from("_default", cur)
    assert [e["payload"]["skill_id"] for e in ev2] == ["a2"]


def test_a_failed_regeneration_is_retried_not_counted_as_done():
    host = lh.LearningHost("_default", regenerate=lambda t, s, reason: type(
        "O", (), {"outcome": "failed", "detail": "x", "generation": 0})())
    _run(host._regenerate_one("assistant.datahub_ab"))
    sched = host.daemon.regeneration_scheduler
    assert "assistant.datahub_ab" in sched.deferred
    from datetime import datetime, timedelta
    sched.last_regenerated["assistant.datahub_ab"] = (datetime.utcnow() - timedelta(days=2)).isoformat()
    sched.batch_window_seconds = 0
    assert _run(sched.get_ready_batch()) == ["assistant.datahub_ab"]


def test_a_refused_state_file_is_recorded_and_kept_aside():
    path = lh.state_path("_default")
    path.parent.mkdir(parents=True)
    path.write_text('{"daemon": {"version": 99, "tenant_id": "_default"}}')
    host = lh.LearningHost("_default")
    assert host._restored is False
    assert not path.exists()
    assert list(path.parent.glob("state.json.refused-*"))


def test_an_event_for_another_tenant_is_not_written(monkeypatch):
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    assert li.emit("acme", "user_feedback", {"skill_id": "s", "signal": 1}) is False
    assert li.day_files("acme") == []
