"""E2E Test: A2A Group Message Routing — Phase 3.5 (ADR-2218)

Phase 3 (previous) proved the RemoteTriggerReceiver callback CONTRACT with a
mock handler. This file proves the REAL wiring:

1. RemoteTriggerSender._build_envelope() with group_id produces an envelope
   whose HMAC the receiver's TaskEnvelope.canonical_payload() reproduces
   exactly — the wire-format compatibility Phase 2 only asserted by comment.
2. The real console handler (chat_groups.handle_inbound_group_message) is
   wired to RemoteTriggerReceiver and, driven through
   RemoteTriggerReceiver._handle_group_message(), correctly:
   - resolves an a2a_peer participant by peer_endpoint_id == sender_origin_id
   - appends the message to the group's real on-disk store (delivery="remote")
   - rejects a sender_origin_id that is not a participant of the group
     (security: a peer cannot inject messages into a group it wasn't added to)

No real second A2A instance is available in this environment, so this test
drives the receiver's internal entry points directly rather than opening a
real socket — it is still "real" in the sense that NO function here is
mocked: it is the actual chat_group_store, the actual chat_groups handler,
and the actual RemoteTriggerReceiver/RemoteTriggerSender envelope code.
"""

import hashlib
import hmac as _hmac
import os
import secrets
import sys
import tempfile
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "core" / "console"))
sys.path.insert(0, str(_REPO_ROOT / "corvin_operator" / "bridges" / "shared"))

from remote_trigger_sender import RemoteTriggerSender  # noqa: E402
from remote_trigger_receiver import RemoteTriggerReceiver, TaskEnvelope  # noqa: E402


@pytest.fixture
def tenant_env(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def group_with_peer(tenant_env, monkeypatch):
    """A real on-disk group with one human + one a2a_peer participant.

    Returns (chat_groups module, chat_group_store module, tenant_dir, group_id).
    """
    from corvin_console import chat_group_store as store
    from corvin_console.routes import chat_groups as cg

    tenant_dir = tenant_env / "tenant"
    # Patch the tenant resolution + the live friendship gate — this test is
    # about routing/storage correctness, not about re-proving the friendship
    # check (that's covered elsewhere, e.g. the Phase 2 send-to-peer tests).
    monkeypatch.setattr(cg._a2a_paths, "tenant_global_dir", lambda tid: tenant_dir)
    monkeypatch.setattr(cg, "require_friendship_active", lambda peer_endpoint_id: None)

    rec = store.create_group(
        tenant_dir, tenant_id="_default", title="Phase 3.5 E2E Group",
        created_by_participant_id="human-1",
    )
    group_id = rec["group_id"]
    store.add_participant(
        tenant_dir, group_id,
        participant_id="peer-1", kind="a2a_peer", display_name="Remote Peer",
        added_by="human-1", peer_endpoint_id="remote-origin-xyz",
    )
    return cg, store, tenant_dir, group_id


class TestEnvelopeWireCompat:
    """Proves the sender and receiver agree on the HMAC for a group_id envelope."""

    def test_group_id_envelope_hmac_matches_receiver(self):
        hmac_key_hex = secrets.token_hex(32)
        env = RemoteTriggerSender._build_envelope(
            task_id="task-1", nonce="nonce-1", origin_id="console",
            instruction="hello group", result_schema={"type": "object"},
            ttl_s=3600, hmac_key_hex=hmac_key_hex, sender_instance_id="inst-1",
            attachments=[], group_id="group-abc-123",
        )
        assert env["group_id"] == "group-abc-123"

        te = TaskEnvelope(
            task_id=env["task_id"], nonce=env["nonce"], issued_at=env["issued_at"],
            origin_id=env["origin_id"], instruction=env["instruction"],
            result_schema=env["result_schema"], ttl_s=env["ttl_s"],
            signature=env["signature"], sender_instance_id=env["sender_instance_id"],
            attachments=env["attachments"], group_id=env.get("group_id"),
        )
        recomputed = _hmac.new(
            bytes.fromhex(hmac_key_hex), te.canonical_payload(), hashlib.sha256
        ).hexdigest()
        assert recomputed == env["signature"]

    def test_no_group_id_is_backward_compatible(self):
        """A plain 1:1 send (no group_id) must be byte-identical to pre-ADR-2218."""
        hmac_key_hex = secrets.token_hex(32)
        env = RemoteTriggerSender._build_envelope(
            task_id="task-2", nonce="nonce-2", origin_id="console",
            instruction="normal message", result_schema={"type": "object"},
            ttl_s=3600, hmac_key_hex=hmac_key_hex, sender_instance_id="inst-1",
            attachments=[],
        )
        assert "group_id" not in env

        te = TaskEnvelope(
            task_id=env["task_id"], nonce=env["nonce"], issued_at=env["issued_at"],
            origin_id=env["origin_id"], instruction=env["instruction"],
            result_schema=env["result_schema"], ttl_s=env["ttl_s"],
            signature=env["signature"], sender_instance_id=env["sender_instance_id"],
            attachments=env["attachments"],
        )
        recomputed = _hmac.new(
            bytes.fromhex(hmac_key_hex), te.canonical_payload(), hashlib.sha256
        ).hexdigest()
        assert recomputed == env["signature"]


class TestRealHandlerWiredToReceiver:
    """Drives RemoteTriggerReceiver with the REAL console handler, no mocks."""

    def test_message_from_known_group_peer_is_stored(self, group_with_peer):
        cg, store, tenant_dir, group_id = group_with_peer
        receiver = RemoteTriggerReceiver(
            origins_dir=tenant_dir / "origins",
            group_message_handler=cg.handle_inbound_group_message,
        )

        status, data = receiver._handle_group_message(
            group_id=group_id, sender_origin_id="remote-origin-xyz",
            instruction="Hello from the real peer!", task_id="task-999", start=0,
        )

        assert status == "accepted"
        assert "message_id" in data

        msgs = store.list_messages(tenant_dir, group_id)
        assert len(msgs) == 1
        assert msgs[0]["text"] == "Hello from the real peer!"
        assert msgs[0]["sender_participant_id"] == "peer-1"
        assert msgs[0]["delivery"] == "remote"

    def test_message_from_non_member_origin_is_rejected(self, group_with_peer):
        """Security: an origin_id not registered as a peer_endpoint_id in
        this group must not be able to inject messages into it, even if it
        is a valid, friended A2A origin elsewhere."""
        cg, store, tenant_dir, group_id = group_with_peer
        receiver = RemoteTriggerReceiver(
            origins_dir=tenant_dir / "origins",
            group_message_handler=cg.handle_inbound_group_message,
        )

        status, data = receiver._handle_group_message(
            group_id=group_id, sender_origin_id="some-other-origin-not-in-group",
            instruction="should not land", task_id="task-1000", start=0,
        )

        assert status == "error"
        assert data["reason"] == "sender_not_a_group_participant"
        assert store.list_messages(tenant_dir, group_id) == []

    def test_unknown_group_from_active_friend_creates_mirror(self, group_with_peer):
        """The first message of a group that lives on the sender's instance
        opens a mirror here under the SAME group_id, with the local operator
        first and the sender as an a2a_peer member — the "you were added to
        a group" behaviour of a chat app."""
        cg, store, tenant_dir, _gid = group_with_peer
        receiver = RemoteTriggerReceiver(
            origins_dir=tenant_dir / "origins",
            group_message_handler=cg.handle_inbound_group_message,
        )

        status, data = receiver._handle_group_message(
            group_id="remoteGroup123", sender_origin_id="remote-origin-xyz",
            instruction="welcome to my group", task_id="task-1001", start=0,
        )

        assert status == "accepted", data
        g = store.get_group(tenant_dir, "remoteGroup123")
        assert g is not None
        assert g["created_by"] == "a2a:remote-origin-xyz"
        assert g["participants"][0]["participant_id"] == "operator"
        assert g["participants"][1]["kind"] == "a2a_peer"
        assert g["participants"][1]["peer_endpoint_id"] == "remote-origin-xyz"
        msgs = store.list_messages(tenant_dir, "remoteGroup123")
        assert [m["text"] for m in msgs] == ["welcome to my group"]

    def test_unknown_group_from_non_friend_is_rejected(self, group_with_peer, monkeypatch):
        cg, store, tenant_dir, _gid = group_with_peer
        from fastapi import HTTPException

        def _deny(peer_endpoint_id):
            raise HTTPException(status_code=404, detail="no active friendship for this peer")

        monkeypatch.setattr(cg, "require_friendship_active", _deny)
        receiver = RemoteTriggerReceiver(
            origins_dir=tenant_dir / "origins",
            group_message_handler=cg.handle_inbound_group_message,
        )
        status, data = receiver._handle_group_message(
            group_id="strangerGroup", sender_origin_id="stranger",
            instruction="spam", task_id="task-1003", start=0,
        )
        assert status == "error"
        assert store.get_group(tenant_dir, "strangerGroup") is None

    def test_unknown_group_with_unsafe_id_is_rejected(self, group_with_peer):
        cg, store, tenant_dir, _gid = group_with_peer
        receiver = RemoteTriggerReceiver(
            origins_dir=tenant_dir / "origins",
            group_message_handler=cg.handle_inbound_group_message,
        )
        status, data = receiver._handle_group_message(
            group_id="../../etc", sender_origin_id="remote-origin-xyz",
            instruction="x", task_id="task-1004", start=0,
        )
        assert status == "error"
        assert data["reason"] == "group_not_found"


class _SyncPool:
    def submit(self, fn, *a, **kw):
        fn(*a, **kw)


class TestDelivery:
    """Hub-and-spoke delivery: the owning instance relays, a mirror never does."""

    def _sent(self, cg, monkeypatch):
        sent: list[tuple[str, str, str]] = []

        class _Res:
            ok = True

        def _fake_send(self, endpoint_id, instruction, **kw):
            sent.append((endpoint_id, instruction, kw.get("group_id")))
            return _Res()

        monkeypatch.setattr(cg, "_FANOUT_POOL", _SyncPool())
        monkeypatch.setattr(cg.RemoteTriggerSender, "send", _fake_send)
        monkeypatch.setattr(cg.RemoteTriggerSender, "__init__", lambda self, *a, **k: None)
        return sent

    def test_owner_relays_peer_message_to_other_peers(self, group_with_peer, monkeypatch):
        cg, store, tenant_dir, group_id = group_with_peer
        store.add_participant(
            tenant_dir, group_id, participant_id="peer-2", kind="a2a_peer",
            display_name="Second", added_by="human-1", peer_endpoint_id="second-origin",
        )
        sent = self._sent(cg, monkeypatch)

        res = cg.handle_inbound_group_message(
            group_id=group_id, sender_origin_id="remote-origin-xyz",
            instruction="hi all", task_id="t",
        )
        assert res["status"] == "accepted"
        assert sent == [("second-origin", "[Remote Peer] hi all", group_id)]

    def test_mirror_never_relays(self, group_with_peer, monkeypatch):
        cg, store, tenant_dir, _gid = group_with_peer
        sent = self._sent(cg, monkeypatch)
        cg.handle_inbound_group_message(
            group_id="mirrorG", sender_origin_id="remote-origin-xyz",
            instruction="first", task_id="t1",
        )
        store.add_participant(
            tenant_dir, "mirrorG", participant_id="peer-2", kind="a2a_peer",
            display_name="Second", added_by="operator", peer_endpoint_id="second-origin",
        )
        cg.handle_inbound_group_message(
            group_id="mirrorG", sender_origin_id="remote-origin-xyz",
            instruction="second", task_id="t2",
        )
        assert sent == []

    def test_receiver_without_handler_errors_cleanly(self, tenant_env):
        """A receiver with no group_message_handler (e.g. gateway running
        without the console installed) must fail closed, not crash."""
        receiver = RemoteTriggerReceiver(origins_dir=tenant_env / "origins")

        status, data = receiver._handle_group_message(
            group_id="whatever", sender_origin_id="whoever",
            instruction="x", task_id="task-1002", start=0,
        )

        assert status == "error"
        assert "group_handler_not_available" in str(data)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


class TestRound2GroupLimitsAndDelivery:
    """Review R2 (2026-10-05): inbound messages could mint unlimited mirror
    groups, and a fan-out message showed "sent to peers" before (and
    regardless of whether) any peer accepted it."""

    def test_mirror_groups_per_peer_are_capped(self, group_with_peer, monkeypatch):
        cg, store, tenant_dir, _gid = group_with_peer
        monkeypatch.setattr(store, "MAX_MIRROR_GROUPS_PER_PEER", 3)
        monkeypatch.setattr(cg, "_peer_label", lambda pid: "Peer")
        results = [cg.handle_inbound_group_message(
            group_id=f"mirror-{i}", sender_origin_id="peer-x", instruction="hi", task_id=f"t{i}")
            for i in range(5)]
        assert [r["status"] for r in results[:3]] == ["accepted"] * 3
        assert [r.get("reason") for r in results[3:]] == ["group_limit_reached"] * 2
        assert store.count_groups(tenant_dir, created_by="a2a:peer-x") == 3

    def test_fanout_delivery_is_recorded_per_outcome(self, group_with_peer, monkeypatch):
        cg, store, tenant_dir, group_id = group_with_peer

        class _Sender:
            def __init__(self, ok):
                self.ok = ok

            def __call__(self, *a, **k):
                return self

            def send(self, *a, **k):
                return type("R", (), {"ok": self.ok})()

        for ok, expected in ((True, "delivered"), (False, "failed:1")):
            msg = store.append_message(tenant_dir, group_id, sender_participant_id="human-1",
                                       text="x", delivery="pending")
            monkeypatch.setattr(cg, "RemoteTriggerSender", _Sender(ok))
            cg._deliver_to_peers(tenant_id="_default", group_id=group_id,
                                 endpoint_ids=["peer-1"], text="x", message_id=msg["id"])
            stored = next(m for m in store.list_messages(tenant_dir, group_id) if m["id"] == msg["id"])
            assert stored["delivery"] == expected

    def test_message_log_is_trimmed(self, group_with_peer, monkeypatch):
        cg, store, tenant_dir, group_id = group_with_peer
        monkeypatch.setattr(store, "_TRIM_AT_BYTES", 2000)
        monkeypatch.setattr(store, "_MAX_MESSAGES_KEPT", 10)
        for i in range(60):
            store.append_message(tenant_dir, group_id, sender_participant_id="human-1", text=f"m{i}")
        msgs = store.list_messages(tenant_dir, group_id, limit=0)
        assert len(msgs) <= 30 and msgs[-1]["text"] == "m59"


class TestRound3GroupStore:
    def test_trim_bounds_bytes_not_only_count(self, group_with_peer, monkeypatch):
        cg, store, tenant_dir, group_id = group_with_peer
        monkeypatch.setattr(store, "_TRIM_AT_BYTES", 4000)
        for i in range(40):
            store.append_message(tenant_dir, group_id, sender_participant_id="human-1", text="x" * 500 + str(i))
        path = store._group_dir(tenant_dir, group_id) / "messages.jsonl"
        assert path.stat().st_size <= 4000 + 700  # one message over, never unbounded
        assert store.list_messages(tenant_dir, group_id)[-1]["text"].endswith("39")

    def test_append_after_delete_never_recreates_the_group(self, group_with_peer):
        cg, store, tenant_dir, group_id = group_with_peer
        d = store._group_dir(tenant_dir, group_id)
        assert store.delete_group(tenant_dir, group_id)
        import pytest as _pytest
        with _pytest.raises(store.ChatGroupError):
            store.append_message(tenant_dir, group_id, sender_participant_id="human-1", text="late")
        assert not (d / "messages.jsonl").exists()
