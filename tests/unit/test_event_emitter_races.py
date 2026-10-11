"""EventEmitter lifecycle races, reproduced and pinned.

* An event accepted by ``emit()`` (it returned True) must be written even when ``stop()`` runs
  concurrently. The check ``if self._stopped`` and the ``put_nowait`` were two steps, so ``stop()``
  could queue its sentinel between them: the event landed BEHIND the sentinel, the worker had
  already exited, and the event was silently lost.
* ``stop()`` with a full queue and a stuck worker blocked forever in ``put(None)``.
* The observable counters stay exact under concurrent producers.
"""
from __future__ import annotations

import sys
import threading
import time
from unittest.mock import Mock

import pytest

from core.learning.event_emitter import EventEmitter
from core.learning.event_store import EventStore


class _Ev:
    def __init__(self, i):
        self.event_id = f"e{i}"
        self.tenant_id = "_default"


def test_an_event_accepted_while_stop_runs_is_still_written():
    written = []
    store = Mock(spec=EventStore)
    store.write_event = Mock(side_effect=lambda e: written.append(e.event_id))
    em = EventEmitter(store, queue_size=10)

    real_put = em._queue.put_nowait
    stopper = {}

    def racing_put(item):
        # stop() starts exactly between emit()'s "still accepting?" check and its put.
        t = threading.Thread(target=lambda: em.stop(timeout=3))
        t.start()
        stopper["t"] = t
        time.sleep(0.3)
        real_put(item)

    em._queue.put_nowait = racing_put
    assert em.emit(_Ev(99)) is True
    stopper["t"].join(timeout=5)
    time.sleep(0.2)
    assert "e99" in written, "emit() said True but the event was never written"


def test_emit_after_stop_is_rejected_not_queued():
    store = Mock(spec=EventStore)
    em = EventEmitter(store, queue_size=10)
    em.stop(timeout=2)
    assert em.emit(_Ev(1)) is False


def test_stop_does_not_hang_on_a_full_queue_with_a_stuck_worker():
    release = threading.Event()
    store = Mock(spec=EventStore)
    store.write_event = Mock(side_effect=lambda e: release.wait(10))
    em = EventEmitter(store, queue_size=1)
    em.emit(_Ev(0))          # the worker takes it and blocks inside write_event
    time.sleep(0.2)
    em.emit(_Ev(1))          # fills the single slot
    outcome = {}

    def stopper():
        try:
            em.stop(timeout=0.5)
        except RuntimeError as exc:
            outcome["error"] = str(exc)

    t = threading.Thread(target=stopper, daemon=True)
    t.start()
    t.join(timeout=4)
    release.set()
    assert not t.is_alive(), "stop() blocked forever on a full queue"
    assert "error" in outcome


def test_drop_counter_is_exact_under_concurrent_producers():
    old = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    release = threading.Event()
    inside = threading.Event()
    store = Mock(spec=EventStore)

    def blocked_write(e):
        inside.set()
        release.wait(10)

    store.write_event = Mock(side_effect=blocked_write)
    em = EventEmitter(store, queue_size=1)
    try:
        assert em.emit(_Ev(0)) is True
        assert inside.wait(5), "the worker never picked the first event up"
        assert em.emit(_Ev(1)) is True     # takes the one free slot: the queue is full from here on
        base = em.dropped
        n, threads = 5000, 8

        def spam():
            for i in range(n):
                em.emit(_Ev(i))

        ts = [threading.Thread(target=spam) for _ in range(threads)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        assert em.dropped - base == n * threads
    finally:
        sys.setswitchinterval(old)
        release.set()
        em.stop(timeout=3)
