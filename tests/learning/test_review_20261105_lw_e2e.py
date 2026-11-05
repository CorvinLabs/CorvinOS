"""Adversarial review 2026-11-05, area LW (learning writer side, register L01-L04).

Every case drives the real primitive, the real erasure handler or the real route:

* LW-1  a partition whose last line is torn (a writer that died mid-append, a full disk) made
        ``event_store.EventStore.write_event`` glue the NEXT record onto the torn bytes: the chain
        record committed, the disk record was unparseable and dropped by every reader - audit-first
        with a lost disk side, on every write after a single crash.
* LW-2  ``event_persistence.EventStore`` (the ``L-learning`` GDPR Art. 17 handler's store, and the
        reader behind ``GET /v1/console/learning/patterns``) raised on that same torn line: the
        erasure layer reported FAILED and the user's events stayed.
* LW-3  ``feedback_sink.FeedbackValidator`` refused every hyphenated tenant (``acme-corp``), which the
        canonical ``validate_tenant_id`` accepts (the F21 class, again): video/scene feedback of
        such a tenant was never recorded.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "core" / "console" / "tests"))
for _p in (_REPO / "corvin_operator" / "bridges" / "shared",):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
from test_learning_loop_routes_e2e import _sandbox  # noqa: E402


def _events_dir(home: Path, tenant_id: str) -> Path:
    return home / "tenants" / tenant_id / "learning" / "events"


def _tear_partition(home: Path, tenant_id: str, date: str) -> Path:
    d = _events_dir(home, tenant_id)
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{date}.jsonl"
    with open(f, "ab") as fh:
        fh.write(b'{"event_id":"torn","event_type":"outcome","skill_id":"os.deleg')  # no newline
    return f


def test_lw1_write_after_a_torn_line_is_not_lost(tmp_path):
    with _sandbox(tmp_path, seed_execution=False) as (_client, home, tenant_id, emitter, _chain):
        from core.learning.learning_events import EventType, LearningEvent

        ev = LearningEvent.create(event_type=EventType.OUTCOME, skill_id="os.delegation_router",
                                  tenant_id=tenant_id, signal={"task_id": "t1", "success": True})
        _tear_partition(home, tenant_id, ev.timestamp.split("T")[0])
        ref = emitter.write_sync(ev)
        assert ref
        got = emitter.store.query_events(tenant_id, event_type=EventType.OUTCOME)
        assert [e.event_id for e in got] == [ev.event_id], "committed event lost behind a torn line"
        assert got[0].audit_ref == ref


def test_lw2_learning_erasure_survives_a_torn_line(tmp_path):
    with _sandbox(tmp_path, seed_execution=False) as (_client, home, tenant_id, _emitter, _chain):
        from core.learning.event_persistence import EventStore as PersistenceStore
        from core.learning.event_schema import LearningEvent as PLearningEvent, LearningEventType

        store = PersistenceStore(tenant_id)
        ev = PLearningEvent(event_type=LearningEventType("feedback.user_provided"), tenant_id=tenant_id,
                            instance_id="i1", user_id="user-42", skill_name="s", session_id="sess1",
                            payload={"x": 1}, timestamp_utc=datetime.utcnow())
        asyncio.run(store.write_event(ev, tenant_id))
        _tear_partition(home, tenant_id, ev.timestamp_utc.date().isoformat())

        # the reader behind GET /learning/patterns must not raise on it either
        assert len(asyncio.run(store.read_events(tenant_id=tenant_id))) == 1

        from erasure_handlers import LearningEventHandler, LayerStatus  # type: ignore

        res = LearningEventHandler(tenant_id=tenant_id).purge("user-42", "req-1")
        assert res.status != LayerStatus.FAILED, res.reason
        assert res.count == 1
        left = "".join(p.read_text() for p in _events_dir(home, tenant_id).glob("*.jsonl"))
        assert "user-42" not in left
        # the kept torn fragment did not swallow the counts-only tombstone
        recs = [json.loads(x) for x in left.splitlines() if x.startswith("{") and x.endswith("}")]
        assert [r["erased_count"] for r in recs if r.get("event_type") == "learning.tombstone"] == [1]
        # and a write after it is still readable
        ev2 = PLearningEvent(event_type=LearningEventType("feedback.user_provided"), tenant_id=tenant_id,
                             instance_id="i1", user_id="user-7", skill_name="s", session_id="sess1",
                             payload={}, timestamp_utc=datetime.utcnow())
        asyncio.run(store.write_event(ev2, tenant_id))
        assert [e.event_id for e in asyncio.run(store.read_events(tenant_id=tenant_id))] == [ev2.event_id]


def test_lw3_feedback_of_a_hyphenated_tenant_is_recorded(tmp_path, monkeypatch):
    """Through ``feedback_emitter_helper.emit_feedback_event`` - what the video job/scene
    feedback routes call - for a tenant the core accepts (``acme-corp``)."""
    from core.tenants.validation import validate_tenant_id

    tenant = "acme-corp"
    assert validate_tenant_id(tenant) == tenant  # a valid tenant everywhere else
    home = tmp_path / "corvin_home"
    (home / "tenants" / tenant / "global" / "forge").mkdir(parents=True)
    monkeypatch.setenv("CORVIN_HOME", str(home))
    monkeypatch.setenv("CORVIN_TENANT_ID", tenant)
    monkeypatch.setenv("VOICE_AUDIT_PATH", str(home / "voice_chain.jsonl"))

    from core.learning.event_store import EventStore
    from core.learning.learning_events import EventType, LearningEvent
    from core.paths.tenant import tenant_home

    store = EventStore(tenant_home(tenant), tenant_id=tenant)
    # the execution the ADR-0534 reality check needs (every real render records one)
    store.write_event(LearningEvent.create(event_type=EventType.SKILL_EXECUTED, skill_id="os.video_producer",
                                           tenant_id=tenant, signal={"output": {"ok": True}}))
    console = str(_REPO / "core" / "console")
    if console not in sys.path:
        sys.path.insert(0, console)
    from corvin_console.routes.feedback_emitter_helper import emit_feedback_event

    ref = asyncio.run(emit_feedback_event(skill_id="os.video_producer", task_id="job1", tenant_id=tenant,
                                          outcome_feedback="yes", quality_rating=5, confidence=0.9))
    assert ref, "feedback of a valid hyphenated tenant was not recorded"
    got = store.query_events(tenant, event_type=EventType.FEEDBACK)
    assert [e.audit_ref for e in got] == [ref]

    # and a tenant the canonical validator refuses stays refused by the sink's validator
    from core.learning.feedback_sink import FeedbackEvent, FeedbackValidator, OutcomeFeedbackType

    bad = FeedbackEvent.create(skill_id="os.video_producer", task_id="job1", tenant_id="Root",
                               outcome_feedback=OutcomeFeedbackType.YES)
    assert FeedbackValidator().validate(bad)[0] is False


def test_lw4_aware_timestamp_and_naive_since_round_trip(tmp_path):
    """``event_persistence`` wrote an AWARE ``timestamp_utc`` as ``...+00:00Z`` (unparseable by
    its own reader) and compared a naive ``since`` with the aware stored value (TypeError)."""
    from datetime import timedelta, timezone

    with _sandbox(tmp_path, seed_execution=False) as (_client, _home, tenant_id, _emitter, _chain):
        from core.learning.event_persistence import EventStore as PersistenceStore
        from core.learning.event_schema import LearningEvent as PLearningEvent, LearningEventType

        store = PersistenceStore(tenant_id)
        aware = PLearningEvent(event_type=LearningEventType("method.observation"), tenant_id=tenant_id,
                               instance_id="i1", skill_name="s", session_id="sess1", payload={},
                               timestamp_utc=datetime.now(timezone.utc))
        asyncio.run(store.write_event(aware, tenant_id))
        naive_since = datetime.utcnow() - timedelta(hours=1)
        got = asyncio.run(store.read_events(tenant_id=tenant_id, since=naive_since))
        assert [e.event_id for e in got] == [aware.event_id]
        assert asyncio.run(store.read_events(tenant_id=tenant_id,
                                             since=datetime.utcnow() + timedelta(hours=1))) == []


def test_lw5_identity_fields_survive_the_pii_scrub(tmp_path, monkeypatch):
    """``event_store.write_event`` ran its PII scrubber over the WHOLE record, so a valid tenant
    such as ``customer-0123456789`` was stored as ``customer-[PHONE]``: every reader filters on the
    tenant, and the outcome the optimizer's ground truth (``recent_outcomes``) needs was invisible.
    """
    tenant = "customer-0123456789"
    from core.tenants.validation import validate_tenant_id

    assert validate_tenant_id(tenant) == tenant
    home = tmp_path / "corvin_home"
    (home / "tenants" / tenant / "global" / "forge").mkdir(parents=True)
    monkeypatch.setenv("CORVIN_HOME", str(home))
    monkeypatch.setenv("CORVIN_TENANT_ID", tenant)
    monkeypatch.setenv("VOICE_AUDIT_PATH", str(home / "voice_chain.jsonl"))

    from core.learning.event_emitter import EventEmitter
    from core.learning.event_store import EventStore
    from core.learning.outcome_sink import emit_task_outcome, recent_outcomes
    from core.paths.tenant import tenant_home

    store = EventStore(tenant_home(tenant), tenant_id=tenant)
    em = EventEmitter(store)
    try:
        assert emit_task_outcome(tenant_id=tenant, task_id="t1", status="completed", emitter=em)
    finally:
        em.stop(timeout=10.0)
    assert em.write_failures == 0
    assert recent_outcomes(tenant, store=store) == (1, 1)
    # the payload is still scrubbed
    from core.learning.learning_events import EventType, LearningEvent

    store.write_event(LearningEvent.create(event_type=EventType.METRIC, skill_id="os.x", tenant_id=tenant,
                                           signal={"note": "call 555-123-4567"}))
    got = store.query_events(tenant, event_type=EventType.METRIC)
    assert got[0].signal == {"note": "call [PHONE]"}


def test_lw1b_count_after_a_torn_line_is_not_lost(tmp_path):
    """Refutation 2026-11-05: LW-1 put the record written after a torn tail on its own line,
    but ``event_store.EventStore.count_events`` (behind ``GET /learning/status`` event_counts,
    the maturity panel and features_phase2) aborted the WHOLE file on the first unparseable
    line - so that committed record was still invisible to every count, and the same route
    reported it in ``recent_outcomes`` while ``event_counts`` said 0."""
    with _sandbox(tmp_path, seed_execution=False) as (client, home, tenant_id, emitter, _chain):
        from core.learning.learning_events import EventType, LearningEvent

        ev = LearningEvent.create(event_type=EventType.OUTCOME, skill_id="os.delegation_router",
                                  tenant_id=tenant_id, signal={"task_id": "t1", "success": True})
        _tear_partition(home, tenant_id, ev.timestamp.split("T")[0])
        assert emitter.write_sync(ev)
        r = client.get("/v1/console/learning/status")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["recent_outcomes"]["total"] == 1
        assert body["event_counts"]["outcome"] == 1, "committed record behind a torn line not counted"


def test_lw2b_erasure_removes_a_fragment_torn_inside_the_subject_id(tmp_path):
    """Refutation 2026-11-05: LW-2 removed a torn fragment only when it carried the subject's
    COMPLETE quoted id. A writer that died inside the ``user_id`` value leaves
    ``..."user_id": "alice@exa`` - the subject's personal data, which survived a COMPLETED
    Art. 17 erasure. A fragment torn inside ANOTHER subject's id stays."""
    with _sandbox(tmp_path, seed_execution=False) as (_client, home, tenant_id, _emitter, _chain):
        from core.learning.event_persistence import EventStore as PersistenceStore
        from core.learning.event_schema import LearningEvent as PLearningEvent, LearningEventType

        store = PersistenceStore(tenant_id)
        subject = "alice@example.com"
        ev = PLearningEvent(event_type=LearningEventType("feedback.user_provided"), tenant_id=tenant_id,
                            instance_id="i1", user_id=subject, skill_name="s", session_id="sess1",
                            payload={"x": 1}, timestamp_utc=datetime.utcnow())
        asyncio.run(store.write_event(ev, tenant_id))
        part = _events_dir(home, tenant_id) / f"{ev.timestamp_utc.date().isoformat()}.jsonl"
        full = json.dumps(ev.to_audit_event())
        cut = full.index(json.dumps(subject)) + len('"alice@exa')
        other = full.replace(json.dumps(subject), json.dumps("bob@example.com"))
        cut_other = other.index('"bob@example.com"') + len('"bob@exa')
        with open(part, "a", encoding="utf-8") as fh:
            fh.write(other[:cut_other] + "\n" + full[:cut] + "\n")

        from erasure_handlers import LearningEventHandler, LayerStatus  # type: ignore

        res = LearningEventHandler(tenant_id=tenant_id).purge(subject, "req-1")
        assert res.status == LayerStatus.APPLIED, res.reason
        left = part.read_text(encoding="utf-8")
        assert "alice@exa" not in left, "subject's torn fragment survived the erasure"
        assert '"bob@exa' in left, "another subject's fragment was removed"


def test_lw2c_read_events_survives_legacy_and_co_resident_records(tmp_path):
    """Refutation 2026-11-05: ``event_persistence.read_events`` still RAISED on a parseable line
    it cannot reconstruct - (a) a record written before LW-4 with the old aware timestamp
    form ``...+00:00Z`` (one sits in a real install's 2026-09-03 partition), (b) a record of
    ``event_store.EventStore``, which writes into the SAME ``<tenant>/learning/events/``
    partitions (no ``instance_id``, event type ``outcome``). One such line aborted the whole
    read, i.e. the ``/learning/patterns`` reader LW-2 claimed to have made fragment-proof."""
    with _sandbox(tmp_path, seed_execution=False) as (_client, home, tenant_id, emitter, _chain):
        from core.learning.event_persistence import EventStore as PersistenceStore
        from core.learning.event_schema import LearningEvent as PLearningEvent, LearningEventType
        from core.learning.learning_events import EventType, LearningEvent

        store = PersistenceStore(tenant_id)
        ev = PLearningEvent(event_type=LearningEventType("feedback.user_provided"), tenant_id=tenant_id,
                            instance_id="i1", user_id="user-42", skill_name="s", session_id="sess1",
                            payload={"x": 1}, timestamp_utc=datetime.utcnow())
        asyncio.run(store.write_event(ev, tenant_id))
        # (b) the live store's record in the same partition
        assert emitter.write_sync(LearningEvent.create(event_type=EventType.OUTCOME,
                                                       skill_id="os.delegation_router", tenant_id=tenant_id,
                                                       signal={"task_id": "t1", "success": True}))
        # (a) a legacy aware-timestamp record
        legacy = dict(ev.to_audit_event(), event_id="legacy-1",
                      timestamp=ev.timestamp_utc.isoformat() + "+00:00Z")
        part = _events_dir(home, tenant_id) / f"{ev.timestamp_utc.date().isoformat()}.jsonl"
        with open(part, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(legacy) + "\n")

        got = asyncio.run(store.read_events(tenant_id=tenant_id))
        assert sorted(e.event_id for e in got) == sorted([ev.event_id, "legacy-1"])
