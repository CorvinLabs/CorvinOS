"""Regression tests for the learning-persistence hardening (2026-08-28).

Each test pins ONE defect that made ADR-0314's learning infrastructure
non-functional or non-compliant, and that no existing test caught because the
failure was swallowed by a fail-closed `except`:

* the events table used MySQL's inline `INDEX` clause, so the store could not
  be CONSTRUCTED at all
* `json.dumps(asdict(event))` raised on `datetime`, so not one event was ever
  written — and both emitters caught and dropped the exception silently
* a round-tripped event came back with `timestamp_utc` as a str and
  `event_type` as a str
* the user-profile directory override ignored `tenant_id`, so two tenants
  shared one file on disk (masked in-process by the cache)
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from core.learning.event_schema import LearningEvent, LearningEventType
from core.learning.event_store import EventStore
from core.learning.user_profile import UserProfileManager


def _event(tenant_id: str = "tenant_1", **kw) -> LearningEvent:
    base = dict(
        event_type=LearningEventType.CONFIDENCE_SCORE,
        tenant_id=tenant_id,
        instance_id="test",
        skill_name="json-parser",
        session_id="s1",
        timestamp_utc=datetime.now(),
        payload={"relevance": 0.9, "reliability": 0.8},
    )
    base.update(kw)
    return LearningEvent(**base)


@pytest.fixture
def store(tmp_path):
    return EventStore(str(tmp_path / "learning.db"))


# ── the store must exist at all ──────────────────────────────────────────


def test_store_constructs_and_creates_its_indexes(tmp_path):
    """`INDEX idx_tenant (tenant_id)` inside CREATE TABLE is MySQL syntax;
    SQLite raised `near "INDEX": syntax error` and __init__ blew up."""
    db = tmp_path / "learning.db"
    EventStore(str(db))

    with sqlite3.connect(db) as conn:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )}
    assert {"idx_events_tenant", "idx_events_type", "idx_events_sequence"} <= names


# ── an event must actually be persisted ──────────────────────────────────


def test_an_event_with_a_datetime_is_written(store):
    """`json.dumps(asdict(event))` raised "Object of type datetime is not JSON
    serializable" on EVERY write, and both emitters swallowed it — the store
    was wired, the call was made, and nothing was ever stored."""
    store.write_event(_event())

    assert store.get_event_count() == 1
    assert len(store.read_events_by_type(LearningEventType.CONFIDENCE_SCORE)) == 1


def test_round_trip_preserves_types(store):
    """`LearningEvent(**json.loads(...))` handed back `timestamp_utc` as a str
    and `event_type` as a str, so a read event was not the event written."""
    written = _event()
    store.write_event(written)

    read = store.read_events_by_type(LearningEventType.CONFIDENCE_SCORE)[0]

    assert isinstance(read.timestamp_utc, datetime)
    assert isinstance(read.event_type, LearningEventType)
    assert read.event_type is LearningEventType.CONFIDENCE_SCORE
    assert read.payload == {"relevance": 0.9, "reliability": 0.8}
    assert read.event_id == written.event_id


def test_hash_chain_verifies_over_real_events(store):
    """The chain is what makes these events an audit trail (GDPR Art. 30, 32);
    it was never exercised because no event could be written."""
    for i in range(5):
        store.write_event(_event(session_id=f"s{i}"))

    assert store.verify_chain() is True


def test_a_tampered_event_breaks_the_chain(store, tmp_path):
    store.write_event(_event())
    store.write_event(_event(session_id="s2"))

    with sqlite3.connect(store.db_path) as conn:
        row = conn.execute("SELECT event_id, event_json FROM events LIMIT 1").fetchone()
        doctored = json.loads(row[1])
        doctored["payload"]["relevance"] = 0.0
        conn.execute("UPDATE events SET event_json = ? WHERE event_id = ?",
                     (json.dumps(doctored, sort_keys=True, separators=(",", ":")),
                      row[0]))
        conn.commit()

    assert store.verify_chain() is False


def test_unknown_fields_do_not_make_history_unreadable(store):
    """Append-only audit store: an event written by another build must stay
    readable, not raise TypeError out of every read."""
    store.write_event(_event())
    with sqlite3.connect(store.db_path) as conn:
        row = conn.execute("SELECT event_id, event_json FROM events").fetchone()
        data = json.loads(row[1])
        data["a_field_from_the_future"] = 42
        conn.execute("UPDATE events SET event_json = ? WHERE event_id = ?",
                     (json.dumps(data), row[0]))
        conn.commit()

    events = store.read_events_by_type(LearningEventType.CONFIDENCE_SCORE)
    assert len(events) == 1
    assert events[0].skill_name == "json-parser"


def test_tenant_reads_are_isolated(store):
    store.write_event(_event(tenant_id="tenant_a"))
    store.write_event(_event(tenant_id="tenant_b"))

    assert len(store.read_events_by_tenant("tenant_a")) == 1
    assert len(store.read_events_by_tenant("tenant_b")) == 1
    assert store.read_events_by_tenant("tenant_a")[0].tenant_id == "tenant_a"


# ── tenant isolation on disk, not just in the cache ──────────────────────


def test_profiles_of_two_tenants_are_separate_files():
    """The directory override ignored tenant_id, so `user_1.json` was ONE file
    for every tenant: the second tenant's load read the first tenant's profile
    off disk and its save overwrote it. The in-process `(user_id, tenant_id)`
    cache hid this, which is why it read as correct."""
    with TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        mgr = UserProfileManager(profiles_dir=base)

        mgr.update_from_feedback("user_1", "tenant_a",
                                 {"skill_feedback": {"json-parser": 0.9}})
        mgr.update_from_feedback("user_1", "tenant_b",
                                 {"skill_feedback": {"json-parser": 0.3}})

        # A FRESH manager — no cache to mask a shared file.
        fresh = UserProfileManager(profiles_dir=base)
        a = fresh.get_profile("user_1", "tenant_a")
        b = fresh.get_profile("user_1", "tenant_b")

        assert a.tenant_id == "tenant_a"
        assert b.tenant_id == "tenant_b"
        assert a.skill_weights["json-parser"] == 0.9
        assert b.skill_weights["json-parser"] == 0.3
        assert (base / "tenant_a" / "user_1.json").exists()
        assert (base / "tenant_b" / "user_1.json").exists()
