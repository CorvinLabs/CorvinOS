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
    host = lh.LearningHost("_default")
    assert _run(host.tick()) == 1
    assert host.daemon.weight_learner.weights["datahub:aa"] < 0.5
    again = lh.LearningHost("_default")
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
