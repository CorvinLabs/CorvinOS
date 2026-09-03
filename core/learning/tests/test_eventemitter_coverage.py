"""EventEmitter contract + wiring audit (ADR-0314).

The emitter's real contract (adversarial review L-07/L-08/L-16, 2026-09-03):

    EventEmitter(event_store, queue_size=1000)   # wraps event_store.EventStore
    emit(learning_events.LearningEvent) -> bool  # sync; False == dropped
    stop(timeout) -> None                        # idempotent, flushes
    .dropped / .write_failures                   # observable counters
    daemon worker + atexit flush                 # never pins the process

The former tests asserted an ``EventEmitter(tenant_home, tenant_id)`` +
``await start()/stop()/flush()`` API that never existed — which is how every
production construction site shipped broken.
"""

from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

from core.learning.event_emitter import EventEmitter
from core.learning.event_store import EventStore
from core.learning.learning_events import EventType, LearningEvent
from core.learning.operator_feedback import OperatorFeedbackHandler, tool_subject_id


@pytest.fixture
def tenant_home(tmp_path: Path) -> Path:
    home = tmp_path / "corvin" / "tenants" / "_default"
    home.mkdir(parents=True, exist_ok=True)
    return home


@pytest.fixture
def event_store(tenant_home: Path) -> EventStore:
    return EventStore(tenant_home)


@pytest.fixture
def event_emitter(event_store: EventStore):
    emitter = EventEmitter(event_store)
    yield emitter
    emitter.stop()


def _ev(tenant_id: str = "_default", skill_id: str = "os.test", **signal) -> LearningEvent:
    return LearningEvent.create(EventType.METRIC, skill_id=skill_id, tenant_id=tenant_id, signal=signal)


def _wait_for(pred, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.01)
    return pred()


class TestEventEmitterContract:
    def test_constructor_validates_arguments(self, tenant_home: Path, event_store: EventStore):
        with pytest.raises(TypeError):
            EventEmitter(tenant_home, "_default")  # the (tenant_home, tenant_id) form never existed
        with pytest.raises(TypeError):
            EventEmitter()  # type: ignore[call-arg]
        with pytest.raises(TypeError):
            EventEmitter(event_store, queue_size="_default")  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            EventEmitter(event_store, queue_size=0)
        em = EventEmitter(event_store, queue_size=1)
        assert em.store is event_store
        assert em.dropped == 0 and em.write_failures == 0
        em.stop()

    def test_no_async_start_flush_api(self, event_emitter: EventEmitter):
        assert not hasattr(event_emitter, "start")
        assert not hasattr(event_emitter, "flush")
        result = event_emitter.emit(_ev())
        assert result is True, "emit() is synchronous and returns a bool"

    def test_emit_persists_through_worker(self, event_emitter: EventEmitter, event_store: EventStore):
        ev = _ev(x=1)
        assert event_emitter.emit(ev) is True
        assert _wait_for(lambda: event_store.count_events("_default") == 1)
        stored = event_store.query_events("_default")
        assert stored[0].event_id == ev.event_id
        assert stored[0].signal == {"x": 1}

    def test_stop_flushes_and_is_idempotent(self, event_store: EventStore):
        emitter = EventEmitter(event_store)
        for i in range(20):
            assert emitter.emit(_ev(i=i))
        emitter.stop()
        assert event_store.count_events("_default") == 20
        emitter.stop()  # second call is a no-op
        assert emitter.emit(_ev()) is False, "after stop(), events are rejected (not silently lost)"

    def test_tenant_validation_on_emit(self, event_emitter: EventEmitter, event_store: EventStore):
        with pytest.raises(ValueError):
            LearningEvent.create(EventType.METRIC, skill_id="s", tenant_id="")
        good = _ev(tenant_id="tenant_a")
        assert event_emitter.emit(good) is True
        assert _wait_for(lambda: event_store.count_events("tenant_a") == 1)
        assert event_store.count_events("_default") == 0

    def test_queue_full_drop_is_counted_and_reported(self, event_store: EventStore):
        class _Slow:
            def __init__(self):
                self.written = []

            def write_event(self, event):
                time.sleep(0.2)
                self.written.append(event)

        slow = _Slow()
        emitter = EventEmitter(slow, queue_size=1)
        results = [emitter.emit(_ev(i=i)) for i in range(5)]
        assert results[0] is True
        assert False in results, "queue of 1 with a slow writer must drop"
        assert emitter.dropped == results.count(False)
        emitter.stop(timeout=5.0)

    def test_write_failure_is_counted(self):
        class _Broken:
            def write_event(self, event):
                raise IOError("disk gone")

        emitter = EventEmitter(_Broken())
        assert emitter.emit(_ev()) is True
        assert _wait_for(lambda: emitter.write_failures == 1)
        emitter.stop()

    def test_emitter_never_pins_the_process(self, tmp_path: Path):
        """L-07: constructing an emitter and exiting must terminate promptly."""
        script = (
            "import sys; from pathlib import Path\n"
            "from core.learning.event_emitter import EventEmitter\n"
            "from core.learning.event_store import EventStore\n"
            "from core.learning.learning_events import LearningEvent, EventType\n"
            f"em = EventEmitter(EventStore(Path({str(tmp_path)!r})))\n"
            "em.emit(LearningEvent.create(EventType.METRIC, skill_id='s', tenant_id='_default'))\n"
            "sys.exit(0)\n"
        )
        repo = Path(__file__).resolve().parents[3]
        proc = subprocess.run(
            [sys.executable, "-c", script], cwd=str(repo), timeout=20,
            capture_output=True, text=True,
        )
        assert proc.returncode == 0, proc.stderr
        # the atexit flush wrote the queued event before exit
        assert EventStore(tmp_path).count_events("_default") == 1


class TestEmitterWiring:
    def test_operator_feedback_handler_uses_emitter(self, event_emitter: EventEmitter, event_store: EventStore):
        handler = OperatorFeedbackHandler(event_store=event_store, event_emitter=event_emitter)
        handler.record_tool_rating(tool_id="tool_1", tool_name="T", rating=5)
        assert _wait_for(lambda: event_store.count_events("_default") == 1)
        events = event_store.query_events("_default", event_type=EventType.FEEDBACK,
                                          skill_id=tool_subject_id("tool_1"))
        assert len(events) == 1 and events[0].signal["rating"] == 5

    def test_production_construction_sites_use_the_real_signature(self):
        """Every ``EventEmitter(`` construction in production code must wrap a
        store, never pass (tenant_home, tenant_id) or nothing (L-08)."""
        repo = Path(__file__).resolve().parents[3]
        sites = [
            repo / "core/orchestration/subsystems/tool_forge_subsystem.py",
            repo / "core/console/corvin_console/standalone.py",
            repo / "core/learning/token_measurement_hook.py",
            repo / "core/console/corvin_console/routes/vibe_metrics_api.py",
        ]
        bad = re.compile(r"EventEmitter\(\s*(?:\)|Path\(|corvin_home|tenant_home|_tenant_dir\b|tenant_dir\b)")
        for site in sites:
            # code only — comments legitimately quote the legacy form
            src = "\n".join(l for l in site.read_text().splitlines() if not l.lstrip().startswith("#"))
            assert "EventEmitter(" in src, site
            assert not bad.search(src), f"{site}: legacy EventEmitter construction"
            assert re.search(r"EventEmitter\(\s*_?LearningEventStore\(", src), \
                f"{site}: must construct EventEmitter(EventStore(tenant_home))"

    def test_no_await_on_sync_emit_in_learning_modules(self):
        """emit()/stop() are synchronous — ``await emitter.emit(...)`` is a TypeError."""
        learning_dir = Path(__file__).resolve().parents[1]
        pattern = re.compile(r"await\s+(?:self\.)?(?:event_)?emitter\.(?:emit|stop|start|flush)\(")
        offenders = []
        for f in sorted(learning_dir.glob("*.py")):
            if pattern.search(f.read_text()):
                offenders.append(f.name)
        # STRICT (N-05): every emit path in core/learning is on the sync contract.
        assert not offenders, f"sync-emit misuse (await on EventEmitter.emit/stop): {offenders}"

    def test_no_event_schema_record_handed_to_the_emitter(self):
        """The emitter persists ONLY ``learning_events.LearningEvent`` (N-05).

        A module that imports ``LearningEvent`` from ``event_schema`` AND calls
        ``emitter.emit(`` hands the worker a record without ``.timestamp`` —
        ``AttributeError`` in the worker, event lost.
        """
        learning_dir = Path(__file__).resolve().parents[1]
        emit_call = re.compile(r"(?:self\.)?(?:event_)?emitter\.emit\(")
        schema_import = re.compile(r"from\s+(?:\.|core\.learning\.)event_schema\s+import\s+[^\n]*\bLearningEvent\b")
        wire_import = re.compile(r"from\s+(?:\.|core\.learning\.)learning_events\s+import")
        offenders = []
        for f in sorted(learning_dir.glob("*.py")):
            src = f.read_text()
            if emit_call.search(src) and schema_import.search(src) and not wire_import.search(src):
                offenders.append(f.name)
        assert not offenders, f"event_schema record handed to the learning_events emitter: {offenders}"
