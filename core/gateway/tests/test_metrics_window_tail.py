"""Windowed ``/metrics?since=`` scrapes read the chain tail, never the whole file.

Review R3-CON-7 (2026-11-19): with the default 15 s TTL and the 60 s window
grid, a scraper asking ``?since=5m`` every 10/15/30 s caused 30/40/20 full
chain walks in ten minutes. Sharing a cache key (the earlier test) does not
bound that - these tests count the walks and the lines actually parsed.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "core" / "gateway"))
sys.path.insert(0, str(_REPO / "corvin_operator" / "forge"))

from corvin_gateway import audit_metrics as m  # noqa: E402

NOW = 1_800_000_000.0
OLD_RECORDS = 20_000
RECENT_RECORDS = 40


def _line(ts: float, i: int) -> str:
    return json.dumps({
        "ts": ts, "event_type": "tool.created", "run_id": f"r{i}",
        "details": {"persona": "coder", "pad": "x" * 160},
    }) + "\n"


@pytest.fixture
def chain(tmp_path, monkeypatch):
    """A chain with many old records and a few inside the last five minutes."""
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    path = m._audit_path("acme")
    path.parent.mkdir(parents=True)
    with path.open("w", encoding="utf-8") as fh:
        for i in range(OLD_RECORDS):                     # two days .. one hour ago
            fh.write(_line(NOW - 172_800 + i * (169_200 / OLD_RECORDS), i))
        for i in range(RECENT_RECORDS):                  # the last 200 s
            fh.write(_line(NOW - 200 + i * 5, OLD_RECORDS + i))
    m.clear_cache()
    monkeypatch.setattr(m, "_TAIL_STEP_BYTES", 16 * 1024)
    yield path
    m.clear_cache()


def _count(body: str) -> int:
    for line in body.splitlines():
        if line.startswith('corvin_forge_tools_created_total{persona="coder"}'):
            return int(line.rsplit(" ", 1)[1])
    return 0


def test_positive_control_the_full_walker_sees_every_record(chain):
    assert sum(1 for _ in m._iter_events(chain)) == OLD_RECORDS + RECENT_RECORDS


def test_window_reader_matches_the_full_walk_and_parses_only_the_tail(chain):
    since = NOW - 300
    state: dict = {}
    tail = [r["run_id"] for r in m._iter_window_events(chain, since=since, state=state)]
    full = [r["run_id"] for r in m._iter_events(chain, since=since)]
    assert tail == full and len(tail) == RECENT_RECORDS
    assert state["truncated"] is False
    assert state["lines_parsed"] < OLD_RECORDS // 20, state
    assert state["bytes_read"] < chain.stat().st_size // 20, state


@pytest.mark.parametrize("window", [60, 300, 3600, 86_400, 30 * 86_400])
def test_window_reader_equals_the_full_walk_for_every_window(chain, window):
    since = NOW - window
    tail = [r["run_id"] for r in m._iter_window_events(chain, since=since)]
    assert tail == [r["run_id"] for r in m._iter_events(chain, since=since)]


def test_slightly_out_of_order_records_are_not_lost(chain):
    with chain.open("a", encoding="utf-8") as fh:
        fh.write(_line(NOW - 330, 90_001))               # older than the window
        fh.write(_line(NOW - 10, 90_002))
    got = {r["run_id"] for r in m._iter_window_events(chain, since=NOW - 300)}
    assert "r90002" in got and "r90001" not in got
    assert len(got) == RECENT_RECORDS + 1


def test_missing_and_empty_chain(tmp_path):
    assert list(m._iter_window_events(tmp_path / "none.jsonl", since=0.0)) == []
    empty = tmp_path / "empty.jsonl"
    empty.write_text("")
    assert list(m._iter_window_events(empty, since=0.0)) == []


def test_a_window_longer_than_the_byte_bound_is_marked_truncated(chain, monkeypatch):
    monkeypatch.setenv("CORVIN_METRICS_WINDOW_MAX_BYTES", str(64 * 1024))
    state: dict = {}
    got = list(m._iter_window_events(chain, since=NOW - 30 * 86_400, state=state))
    assert state["truncated"] is True
    assert 0 < len(got) < OLD_RECORDS
    assert state["bytes_read"] <= 64 * 1024
    body = m.render_prometheus(m.aggregate("acme", since=NOW - 30 * 86_400))
    assert "corvin_metrics_window_truncated 1" in body
    # A short window inside the bound is complete and says so.
    body = m.render_prometheus(m.aggregate("acme", since=NOW - 300))
    assert "corvin_metrics_window_truncated 0" in body
    assert _count(body) == RECENT_RECORDS


@pytest.mark.parametrize("interval", [10, 15, 30, 60])
def test_ten_minutes_of_scrapes_cause_no_full_walk(chain, monkeypatch, interval):
    """The reviewer's schedule: ``?since=5m`` every N seconds for ten minutes."""
    walks: list = []
    real_walk = m._iter_events

    def counting_walk(path, *, since=None):
        walks.append(since)
        return real_walk(path, since=since)

    parsed: list = []
    real_tail = m._iter_window_events

    def counting_tail(path, *, since, max_bytes=None, state=None):
        info = state if state is not None else {}
        yield from real_tail(path, since=since, max_bytes=max_bytes, state=info)
        parsed.append(info["lines_parsed"])

    clock = [NOW]
    monkeypatch.setattr(m, "_iter_events", counting_walk)
    monkeypatch.setattr(m, "_iter_window_events", counting_tail)
    monkeypatch.setattr(m.time, "time", lambda: clock[0])

    scrapes = 0
    while clock[0] < NOW + 600:
        body = m.render("acme", since=m.quantize_since(clock[0] - 300))
        scrapes += 1
        clock[0] += interval
    assert scrapes == 600 // interval
    assert walks == [], f"{len(walks)} full chain walks in {scrapes} scrapes"
    assert parsed, "positive control: the tail reader served the scrapes"
    assert len(parsed) <= scrapes
    # Each pass parsed the window, not the chain.
    assert max(parsed) < OLD_RECORDS // 20, max(parsed)
    assert "corvin_forge_tools_created_total" in body


def test_all_time_scrape_does_not_use_the_full_walker_either(chain, monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("full chain walk on a scrape path")

    monkeypatch.setattr(m, "_iter_events", boom)
    assert _count(m.render("acme", since=None)) == OLD_RECORDS + RECENT_RECORDS


def test_concurrent_scrapes_of_one_key_share_one_pass(chain, monkeypatch):
    calls: list = []
    real = m.aggregate
    gate = threading.Event()

    def slow(tenant_id, *, since=None):
        calls.append(since)
        gate.wait(10)
        return real(tenant_id, since=since)

    monkeypatch.setattr(m, "aggregate", slow)
    since = m.quantize_since(NOW - 300)
    bodies: list = []
    threads = [
        threading.Thread(target=lambda: bodies.append(m.render("acme", since=since)))
        for _ in range(8)
    ]
    for t in threads:
        t.start()
    deadline = time.monotonic() + 10
    while not calls and time.monotonic() < deadline:
        time.sleep(0.01)
    time.sleep(0.2)                                      # let the others queue up
    gate.set()
    for t in threads:
        t.join(20)
    assert len(calls) == 1, f"{len(calls)} passes for one key"
    assert len(bodies) == 8 and len(set(bodies)) == 1
    assert m._inflight == {}


def test_a_failed_pass_does_not_block_the_key(chain, monkeypatch):
    real = m.aggregate
    state = {"fail": True}

    def flaky(tenant_id, *, since=None):
        if state.pop("fail", False):
            raise RuntimeError("boom")
        return real(tenant_id, since=since)

    monkeypatch.setattr(m, "aggregate", flaky)
    with pytest.raises(RuntimeError):
        m.render("acme", since=NOW - 300)
    assert m._inflight == {}
    assert _count(m.render("acme", since=NOW - 300)) == RECENT_RECORDS
