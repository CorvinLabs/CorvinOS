"""EventEmitter latency regression (ADR-0314).

``emit()`` is a synchronous, non-blocking enqueue; the disk write happens on
the daemon worker. Goal: the caller-side cost of emit() stays in the
sub-millisecond range and does not scale with store write latency.
"""

from __future__ import annotations

import time
from pathlib import Path
from statistics import mean

import pytest

from core.learning.event_emitter import EventEmitter
from core.learning.event_store import EventStore
from core.learning.learning_events import EventType, LearningEvent
from core.learning.operator_feedback import OperatorFeedbackHandler


def _ev(i: int) -> LearningEvent:
    return LearningEvent.create(EventType.METRIC, skill_id="os.latency", tenant_id="_default", signal={"i": i})


class _SlowStore:
    """A store whose write takes 20 ms — emit() must not pay for it."""

    def __init__(self):
        self.written = 0

    def write_event(self, event):
        time.sleep(0.02)
        self.written += 1


@pytest.fixture
def event_store(tmp_path: Path) -> EventStore:
    return EventStore(tmp_path / "tenants" / "_default")


def _measure(fn, n: int) -> list[float]:
    out = []
    for i in range(n):
        t0 = time.perf_counter()
        fn(i)
        out.append((time.perf_counter() - t0) * 1000.0)
    return out


class TestEmitLatency:
    def test_emit_is_sub_millisecond_on_average(self, event_store: EventStore):
        emitter = EventEmitter(event_store, queue_size=10_000)
        try:
            samples = _measure(lambda i: emitter.emit(_ev(i)), 200)
        finally:
            emitter.stop(timeout=10.0)
        assert mean(samples) < 1.0, f"mean emit() latency {mean(samples):.3f} ms"
        assert event_store.count_events("_default") == 200

    def test_emit_cost_independent_of_store_latency(self):
        slow = _SlowStore()
        emitter = EventEmitter(slow, queue_size=10_000)
        try:
            samples = _measure(lambda i: emitter.emit(_ev(i)), 50)
        finally:
            emitter.stop(timeout=10.0)
        assert max(samples) < 5.0, f"emit() blocked on the store write: max {max(samples):.3f} ms"
        assert slow.written == 50

    def test_handler_record_latency_with_emitter_vs_direct(self, event_store: EventStore):
        emitter = EventEmitter(event_store, queue_size=10_000)
        via_emitter = OperatorFeedbackHandler(event_store=event_store, event_emitter=emitter)
        direct = OperatorFeedbackHandler(event_store=event_store)
        try:
            s_emit = _measure(lambda i: via_emitter.record_tool_rating("t", "T", 5), 50)
            s_direct = _measure(lambda i: direct.record_tool_rating("t", "T", 5), 50)
        finally:
            emitter.stop(timeout=10.0)
        # both paths persist everything
        assert event_store.count_events("_default") == 100
        # emitter path never slower than 5 ms per call on average
        assert mean(s_emit) < 5.0, f"mean {mean(s_emit):.3f} ms"
        assert mean(s_direct) < 50.0
