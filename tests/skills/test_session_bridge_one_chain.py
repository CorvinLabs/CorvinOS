"""Regression: session_bridge_producer / session_recovery (ADR-0541 amendment).

Before the fix:
* ``core.infinite_session.session_bridge_producer`` did not import at all
  (non-default dataclass field after a default one) — chat_runtime's envelope
  block swallowed the ImportError on every turn;
* ``emit_bridge_event`` assigned to a frozen dataclass (FrozenInstanceError);
* ``content_hash`` never matched ``compute_content_hash()``;
* the bridge record and the "context restored" record were appended as raw,
  un-chained JSON to a hand-composed ``Path.home()/.corvin/.../forge/audit.jsonl``
  (ignoring CORVIN_HOME) — the tenant's hash chain would no longer verify;
* the key file was looked up under ``Path.home()/.corvin`` regardless of CORVIN_HOME.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "fake_home"))
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "corvin"))
    monkeypatch.delenv("VOICE_AUDIT_PATH", raising=False)
    monkeypatch.delenv("FORGE_ROOT", raising=False)
    monkeypatch.delenv("CORVIN_TENANT_ID", raising=False)
    monkeypatch.delenv("CORVIN_SNAPSHOT_KEY", raising=False)
    return tmp_path


def _snapshot(producer, **kw):
    base = dict(
        tenant_id="_default", task_id="task_1", session_id="sid-secret-123",
        last_message_hash="", conversation_turn_count=3, worktree_path="",
        base_commit="", phase_name="",
    )
    base.update(kw)
    return producer.create_snapshot(**base)


def test_bridge_event_lands_in_tenant_chain_and_verifies(home, monkeypatch):
    # The persisted snapshot is HMAC-signed; without a key emit fails closed.
    monkeypatch.setenv("CORVIN_SNAPSHOT_KEY", "test-snapshot-key-not-default")
    from core.infinite_session.session_bridge_producer import SessionBridgeProducer
    from core.paths import tenant_audit_chain

    producer = SessionBridgeProducer()
    snap = _snapshot(producer)
    assert snap.content_hash == snap.compute_content_hash()

    ev = producer.emit_bridge_event(snap, source_session_id="sid-secret-123")
    assert ev.hash

    chain = Path(tenant_audit_chain("_default"))
    recs = [json.loads(l) for l in chain.read_text().splitlines() if l.strip()]
    bridge = [r for r in recs if r["event_type"] == "infinite_session.bridge_created"]
    assert len(bridge) == 1
    assert bridge[0]["details"]["task_id"] == "task_1"
    assert "sid-secret-123" not in chain.read_text()  # fingerprinted only

    from forge import security_events

    ok, errors = security_events.verify_chain(chain)[:2]
    assert ok, errors

    # snapshot persisted under CORVIN_HOME, nothing under the real-home path
    assert (Path(home) / "corvin" / "tenants" / "_default" / "infinite_session"
            / "snapshots" / "task_1" / "latest.json").exists()
    assert not (Path(home) / "fake_home" / ".corvin").exists()


def test_bridge_rejects_traversal_task_id(home):
    from core.infinite_session.session_bridge_producer import SessionBridgeProducer

    producer = SessionBridgeProducer()
    snap = _snapshot(producer, task_id="../../escape")
    with pytest.raises(RuntimeError):
        producer.emit_bridge_event(snap, source_session_id="s")


def test_snapshot_key_file_honours_corvin_home(home):
    from core.infinite_session.key_management import KeyManagementConfig

    keyfile = Path(home) / "corvin" / "keys" / "snapshot.key"
    keyfile.parent.mkdir(parents=True)
    keyfile.write_text("k-from-corvin-home\n")
    assert KeyManagementConfig.get_snapshot_key() == "k-from-corvin-home"


def test_recovery_restored_event_goes_through_chain_writer(home):
    from core.infinite_session.session_recovery import SessionRecoveryManager
    from core.paths import tenant_audit_chain

    mgr = SessionRecoveryManager()
    mgr._emit_context_restored_event(task_id="task_1", tenant_id="_default", snapshot_hash="abc")
    chain = Path(tenant_audit_chain("_default"))
    recs = [json.loads(l) for l in chain.read_text().splitlines() if l.strip()]
    assert [r["event_type"] for r in recs][-1] == "infinite_session.context_restored"
    from forge import security_events

    assert security_events.verify_chain(chain)[0]
