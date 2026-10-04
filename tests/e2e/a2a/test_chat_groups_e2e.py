"""E2E: group chat with foreign A2A agent participants (ADR-2216).

Real FastAPI router + TestClient, real on-disk store (no mocked storage
layer) — matches the call-site-test discipline already applied to
test_a2a_send_mcp_e2e.py / test_a2a_friendship_token_chat_e2e.py (see
memory feedback-dead-mechanism-needs-call-site-test).

Covers the two-layer authorization model from ADR-2216:
- a group's own participant list gates "who sees this conversation"
- the A2A origin/endpoint state gates "may this peer talk to us at all" —
  re-checked LIVE on every admit/send, never cached

Run: python3 -m pytest tests/e2e/a2a/test_chat_groups_e2e.py -q
"""
from __future__ import annotations

import dataclasses
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_REPO = Path(__file__).resolve().parents[3]
for _p in (
    _REPO / "corvin_operator" / "bridges" / "shared",
    _REPO / "core" / "console",
):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

_KEYS = ("CORVIN_HOME", "CORVIN_TENANT_ID", "FORGE_ROOT", "VOICE_AUDIT_PATH",
         "CORVIN_AUDIT_ANCHOR_KEY", "XDG_CONFIG_HOME",
         "REMOTE_ORIGINS_DIR", "REMOTE_ENDPOINTS_DIR")


def _fake_record(tenant_id: str = "_default", sid_fingerprint: str = "fp-human-1"):
    from corvin_console import auth as session_auth  # noqa: PLC0415
    values: dict[str, object] = {}
    for f in dataclasses.fields(session_auth.SessionRecord):
        if f.default is not dataclasses.MISSING:
            continue
        ann = str(f.type)
        if "float" in ann:
            values[f.name] = 1_000_000.0 + (3600 if f.name == "expires_at" else 0)
        elif "bool" in ann:
            values[f.name] = False
        elif f.name == "tier":
            tier = getattr(session_auth, "Tier", None)
            values[f.name] = next(iter(tier)) if tier else "owner"
        elif f.name == "tenant_id":
            values[f.name] = tenant_id
        elif f.name == "sid_fingerprint":
            values[f.name] = sid_fingerprint
        else:
            values[f.name] = f"test-{f.name}"
    return session_auth.SessionRecord(**values)  # type: ignore[arg-type]


class ChatGroupsE2ETests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory(prefix="chat-groups-e2e-")
        self.addCleanup(self._td.cleanup)
        self.tmp = Path(self._td.name)
        self._saved = {k: os.environ.get(k) for k in _KEYS}
        self.addCleanup(lambda: [
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
            for k, v in self._saved.items()
        ])
        for k in ("FORGE_ROOT", "VOICE_AUDIT_PATH"):
            os.environ.pop(k, None)
        self.home = self.tmp / "home"
        self.home.mkdir()
        os.environ["CORVIN_HOME"] = str(self.home)
        os.environ["CORVIN_TENANT_ID"] = "_default"
        os.environ["XDG_CONFIG_HOME"] = str(self.tmp / "xdg")
        os.environ["CORVIN_AUDIT_ANCHOR_KEY"] = str(self.tmp / "anchor.key")
        (self.home / "origins").mkdir()
        (self.home / "endpoints").mkdir()
        os.environ["REMOTE_ORIGINS_DIR"] = str(self.home / "origins")
        os.environ["REMOTE_ENDPOINTS_DIR"] = str(self.home / "endpoints")

        from corvin_console.routes import chat_groups as R  # noqa: PLC0415
        self.R = R
        self._audit = patch.object(R.console_audit, "action_performed")
        self._audit.start()
        self.addCleanup(self._audit.stop)

    def _client(self, tenant: str | None = "_default", sid_fingerprint: str = "fp-human-1"):
        from fastapi import FastAPI  # noqa: PLC0415
        from fastapi.testclient import TestClient  # noqa: PLC0415

        app = FastAPI()
        app.include_router(self.R.router, prefix="/v1/console")
        if tenant is not None:
            app.dependency_overrides[self.R.require_session_csrf_on_mutation] = (
                lambda: _fake_record(tenant, sid_fingerprint)
            )
        return TestClient(app)

    def _enable_peer_endpoint(self, peer_id: str) -> None:
        """An ACTIVE friendship — the same shape a2a_feed.py's _peers()
        reads from endpoint files."""
        (self.home / "endpoints" / f"{peer_id}.json").write_text(json.dumps({
            "endpoint_id": peer_id, "url": "http://10.9.9.9:9999",
            "hmac_key": "k", "enabled": True,
        }))

    def test_create_group_without_session_is_401(self):
        r = self._client(tenant=None).post("/v1/console/chat/groups", json={"title": "x"})
        self.assertEqual(r.status_code, 401)

    def test_create_group_returns_creator_as_first_participant(self):
        r = self._client().post("/v1/console/chat/groups", json={"title": "Launch Team"})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["title"], "Launch Team")
        self.assertEqual(len(body["participants"]), 1)
        self.assertEqual(body["participants"][0]["participant_id"], "fp-human-1")
        self.assertEqual(body["participants"][0]["kind"], "human")

    def test_add_a2a_peer_participant_without_active_friendship_is_404(self):
        client = self._client()
        group = client.post("/v1/console/chat/groups", json={"title": "G"}).json()
        r = client.post(
            f"/v1/console/chat/groups/{group['group_id']}/participants",
            json={"participant_id": "stranger-peer", "kind": "a2a_peer",
                  "peer_endpoint_id": "stranger-peer"},
        )
        self.assertEqual(r.status_code, 404, r.text)
        self.assertIn("no active friendship", r.text)

    def test_add_a2a_peer_participant_with_active_friendship_succeeds(self):
        self._enable_peer_endpoint("friend-peer")
        client = self._client()
        group = client.post("/v1/console/chat/groups", json={"title": "G"}).json()
        r = client.post(
            f"/v1/console/chat/groups/{group['group_id']}/participants",
            json={"participant_id": "friend-peer", "kind": "a2a_peer",
                  "display_name": "Friend Agent", "peer_endpoint_id": "friend-peer"},
        )
        self.assertEqual(r.status_code, 200, r.text)
        participants = r.json()["participants"]
        self.assertEqual(len(participants), 2)
        peer_p = next(p for p in participants if p["kind"] == "a2a_peer")
        self.assertEqual(peer_p["participant_id"], "friend-peer")
        self.assertEqual(peer_p["peer_endpoint_id"], "friend-peer")

    def test_add_duplicate_participant_is_409(self):
        client = self._client()
        group = client.post("/v1/console/chat/groups", json={"title": "G"}).json()
        r = client.post(
            f"/v1/console/chat/groups/{group['group_id']}/participants",
            json={"participant_id": "fp-human-1", "kind": "human"},
        )
        self.assertEqual(r.status_code, 409)

    def test_remove_participant(self):
        self._enable_peer_endpoint("friend-peer")
        client = self._client()
        group = client.post("/v1/console/chat/groups", json={"title": "G"}).json()
        client.post(
            f"/v1/console/chat/groups/{group['group_id']}/participants",
            json={"participant_id": "friend-peer", "kind": "a2a_peer", "peer_endpoint_id": "friend-peer"},
        )
        r = client.delete(f"/v1/console/chat/groups/{group['group_id']}/participants/friend-peer")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(len(r.json()["participants"]), 1)

    def test_send_message_from_non_participant_is_403(self):
        client = self._client()
        group = client.post("/v1/console/chat/groups", json={"title": "G"}).json()
        r = client.post(
            f"/v1/console/chat/groups/{group['group_id']}/messages",
            json={"text": "hi", "sender_participant_id": "not-a-member"},
        )
        self.assertEqual(r.status_code, 403)

    def test_send_and_list_message_round_trip(self):
        client = self._client()
        group = client.post("/v1/console/chat/groups", json={"title": "G"}).json()
        r = client.post(
            f"/v1/console/chat/groups/{group['group_id']}/messages",
            json={"text": "hello group", "sender_participant_id": "fp-human-1"},
        )
        self.assertEqual(r.status_code, 200, r.text)
        msgs = client.get(f"/v1/console/chat/groups/{group['group_id']}/messages").json()
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["text"], "hello group")

    def test_send_message_blocked_when_peer_friendship_revoked_after_join(self):
        """Live-gate proof: the peer was admitted while active, then its
        endpoint was disabled (friendship revoked) — the NEXT message must
        be refused, proving the check is live, not cached at join time."""
        self._enable_peer_endpoint("friend-peer")
        client = self._client()
        group = client.post("/v1/console/chat/groups", json={"title": "G"}).json()
        client.post(
            f"/v1/console/chat/groups/{group['group_id']}/participants",
            json={"participant_id": "friend-peer", "kind": "a2a_peer", "peer_endpoint_id": "friend-peer"},
        )
        # Revoke: disable the endpoint (same state a2a_pair.py's disable flips).
        ep_path = self.home / "endpoints" / "friend-peer.json"
        data = json.loads(ep_path.read_text())
        data["enabled"] = False
        ep_path.write_text(json.dumps(data))

        r = client.post(
            f"/v1/console/chat/groups/{group['group_id']}/messages",
            json={"text": "still trying", "sender_participant_id": "fp-human-1"},
        )
        self.assertEqual(r.status_code, 404, r.text)
        self.assertIn("no active friendship", r.text)

    def test_send_to_peer_rejects_non_a2a_peer(self):
        """ADR-2218 Phase 2 route, re-verified: a peer_id that isn't an
        a2a_peer participant of this group is refused."""
        client = self._client()
        group = client.post("/v1/console/chat/groups", json={"title": "G"}).json()
        r = client.post(
            f"/v1/console/chat/groups/{group['group_id']}/send-to-peer",
            json={"text": "hi", "sender_participant_id": "fp-human-1",
                  "peer_id": "does-not-exist"},
        )
        self.assertEqual(r.status_code, 404, r.text)
        self.assertIn("not an a2a_peer", r.text)

    def test_send_to_peer_constructs_real_envelope_with_group_id(self):
        """ADR-2218 Phase 3.5: send-to-peer must call the REAL
        RemoteTriggerSender.send() (not the Phase 2 stub that only staged
        the envelope and never transmitted it), passing the message text
        as ``instruction`` and the group's id as ``group_id`` so the peer's
        receiver can route the reply back into the same group."""
        self._enable_peer_endpoint("friend-peer")
        client = self._client()
        group = client.post("/v1/console/chat/groups", json={"title": "G"}).json()
        client.post(
            f"/v1/console/chat/groups/{group['group_id']}/participants",
            json={"participant_id": "friend-peer", "kind": "a2a_peer",
                  "peer_endpoint_id": "friend-peer"},
        )

        captured: dict = {}
        from remote_trigger_sender import SendResult  # noqa: PLC0415

        def _fake_send(self, endpoint_id, instruction, **kwargs):
            captured["endpoint_id"] = endpoint_id
            captured["instruction"] = instruction
            captured["group_id"] = kwargs.get("group_id")
            captured["purpose_id"] = kwargs.get("purpose_id")
            return SendResult(
                ok=True, status="ok", task_id="t-1", instance_id="peer-inst",
                instance_id_match=True, data={}, attachments=[], duration_ms=5,
                error_category=None, error_detail=None,
            )

        with patch.object(self.R.RemoteTriggerSender, "send", _fake_send):
            r = client.post(
                f"/v1/console/chat/groups/{group['group_id']}/send-to-peer",
                json={"text": "hello remote peer", "sender_participant_id": "fp-human-1",
                      "peer_id": "friend-peer"},
            )
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["status"], "sent_pending")

        # The actual wire call carried the real message text as the
        # instruction (Phase 2 had hardcoded a command string here) and the
        # group's id (so the receiver routes it, not a 1:1 fallback).
        self.assertEqual(captured["endpoint_id"], "friend-peer")
        self.assertEqual(captured["instruction"], "hello remote peer")
        self.assertEqual(captured["group_id"], group["group_id"])
        self.assertEqual(captured["purpose_id"], "group_message")

        # Staged locally too, with delivery="remote".
        msgs = client.get(f"/v1/console/chat/groups/{group['group_id']}/messages").json()
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["text"], "hello remote peer")
        self.assertEqual(msgs[0]["delivery"], "remote")

    def test_send_to_peer_transport_failure_reports_error_without_crashing(self):
        """A failed send (peer unreachable, auth failure, etc.) must come
        back as a clean {"status": "error"} — the real sender.send() never
        raises on transport failure, and the route must not turn that into
        an uncaught 500."""
        self._enable_peer_endpoint("friend-peer")
        client = self._client()
        group = client.post("/v1/console/chat/groups", json={"title": "G"}).json()
        client.post(
            f"/v1/console/chat/groups/{group['group_id']}/participants",
            json={"participant_id": "friend-peer", "kind": "a2a_peer",
                  "peer_endpoint_id": "friend-peer"},
        )

        from remote_trigger_sender import SendResult  # noqa: PLC0415

        def _fake_send_fail(self, endpoint_id, instruction, **kwargs):
            return SendResult(
                ok=False, status="error", task_id="t-2", instance_id="",
                instance_id_match=False, data={}, attachments=[], duration_ms=5,
                error_category="unreachable", error_detail="peer unreachable",
            )

        with patch.object(self.R.RemoteTriggerSender, "send", _fake_send_fail):
            r = client.post(
                f"/v1/console/chat/groups/{group['group_id']}/send-to-peer",
                json={"text": "will not arrive", "sender_participant_id": "fp-human-1",
                      "peer_id": "friend-peer"},
            )
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["status"], "error")
        self.assertIn("unreachable", body["detail"])

    def test_get_group_cross_tenant_is_404(self):
        client_a = self._client(tenant="tenant-a")
        group = client_a.post("/v1/console/chat/groups", json={"title": "G"}).json()
        client_b = self._client(tenant="tenant-b")
        r = client_b.get(f"/v1/console/chat/groups/{group['group_id']}")
        self.assertEqual(r.status_code, 404)


if __name__ == "__main__":
    unittest.main()
