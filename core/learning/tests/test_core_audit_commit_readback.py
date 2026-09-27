"""Regression: the learning chain write is read back where the writer puts it.

``core_audit_event`` proves a commit by finding its ``audit_ref`` in the chain
tail. The core writer nests caller details under ``details``; the read-back
looked for a TOP-LEVEL ``audit_ref`` on the final line only, so every learning
write since 2026-09-20 raised "core audit write did not commit" — every
``EventStore.write_event`` (outcomes, feedback, metrics, tool ranking) failed
closed and the learning loop recorded nothing.
"""
from __future__ import annotations

import json

import pytest


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    monkeypatch.delenv("VOICE_AUDIT_PATH", raising=False)
    monkeypatch.delenv("FORGE_ROOT", raising=False)
    monkeypatch.delenv("CORVIN_TENANT_ID", raising=False)
    return tmp_path


def test_core_audit_event_commits_and_returns_ref(home):
    from core.learning.event_persistence import core_audit_event
    from core.paths import tenant_audit_chain

    ref = core_audit_event("learning.outcome", tenant_id="_default", details={})
    tail = tenant_audit_chain("_default").read_text().splitlines()[-1]
    assert json.loads(tail)["details"]["audit_ref"] == ref


def test_readback_tolerates_a_concurrent_append_after_ours(tmp_path):
    from core.learning.event_persistence import _tail_contains

    chain = tmp_path / "audit.jsonl"
    ours = {"event_type": "learning.outcome", "details": {"audit_ref": "ref-ours"}}
    theirs = {"event_type": "other", "details": {"audit_ref": "ref-theirs"}}
    chain.write_text(json.dumps(ours) + "\n" + json.dumps(theirs) + "\n")
    assert _tail_contains(chain, "ref-ours") is True


def test_readback_is_exact_field_match_not_substring(tmp_path):
    from core.learning.event_persistence import _tail_contains

    chain = tmp_path / "audit.jsonl"
    junk = {"event_type": "x", "details": {"note": "ref-ours"}}  # needle in another field
    chain.write_text(json.dumps(junk) + "\n" + "not json ref-ours\n")
    assert _tail_contains(chain, "ref-ours") is False
    assert _tail_contains(tmp_path / "missing.jsonl", "ref-ours") is False


def test_event_store_write_lands_on_disk(home, tmp_path):
    from core.learning.event_store import EventStore
    from core.learning.learning_events import EventType, LearningEvent

    store = EventStore(tmp_path / "store")
    ev = LearningEvent.create(
        event_type=EventType.OUTCOME,
        skill_id="os.delegation_router",
        tenant_id="_default",
        signal={"success": True},
    )
    store.write_event(ev)
    got = store.query_events("_default", event_type=EventType.OUTCOME)
    assert [e.event_id for e in got] == [ev.event_id]
