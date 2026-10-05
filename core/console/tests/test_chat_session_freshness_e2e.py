"""The chat socket's session object is as old as the connection (review R2,
2026-10-05). Over the real routes + WebSocket, with the engine turn replaced
by a stub that does the real end-of-turn bookkeeping (_append_turn + touch):

A. a rename made while the socket is open survives the next turn;
B. deleting a chat while its turn runs stops the turn and the chat stays
   deleted — no metadata, no turn log on disk (deletion is an erasure path);
C. two tabs on one new chat: the second tab's turn sees the first tab's turn
   (resume decision + turn count come from disk, not the socket's copy).
"""
from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch


class ChatSessionFreshnessE2E(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home, True)
        tid = "_default"
        th = self.home / "tenants" / tid
        for d in ("global/auth", "global/forge", "global/console/sessions"):
            (th / d).mkdir(parents=True, exist_ok=True)
        env = patch.dict(os.environ, {"CORVIN_HOME": str(self.home), "CORVIN_TENANT_ID": tid,
                                      "VOICE_AUDIT_PATH": str(self.home / "audit.jsonl")})
        env.start()
        self.addCleanup(env.stop)
        from corvin_console import auth as _auth, chat_runtime as cr
        import corvin_console.routes.chat as chat_routes
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        self.cr, self.tid = cr, tid
        rec = _auth.create_session(tenant_id=tid, token_fingerprint="fp")
        csrf = _auth.derive_csrf_token(rec.csrf_secret, rec.sid)
        app = FastAPI()
        app.include_router(chat_routes.router, prefix="/v1/console")
        self.c = TestClient(app)
        self.c.cookies.set("corvin_console_sid", rec.sid)
        self.c.headers.update({"X-CSRF-Token": csrf})
        self.seen: list[tuple[str, bool]] = []
        self.delay = 0.0

        async def fake(sess, prompt, **kw):
            self.seen.append((prompt, sess.turn_count > 0))
            await asyncio.sleep(self.delay)
            cr._append_turn(sess, "user", [{"kind": "text", "text": prompt}])
            cr._append_turn(sess, "assistant", [{"kind": "text", "text": "ok"}])
            cr.touch(sess, increment_turn=True)
            yield {"type": "done"}

        for target, value in (
            ("corvin_console.routes._compute_license_gate.enforce_chat_turns", lambda *a, **k: None),
        ):
            p = patch(target, value)
            p.start()
            self.addCleanup(p.stop)
        for name, value in (("get_engine_unavailable_message", lambda t: None), ("stream_turn", fake)):
            p = patch.object(cr, name, value)
            p.start()
            self.addCleanup(p.stop)

    def _titles(self, sid):
        return [s["title"] for s in self.c.get("/v1/console/chat/sessions").json()["sessions"] if s["sid"] == sid]

    def test_a_rename_survives_the_next_turn(self):
        sid = self.c.post("/v1/console/chat/sessions", json={"title": "Original"}).json()["session"]["sid"]
        with self.c.websocket_connect(f"/v1/console/chat/sessions/{sid}/stream") as ws:
            ws.receive_json()
            self.assertEqual(self.c.patch(f"/v1/console/chat/sessions/{sid}",
                                          json={"title": "Renamed"}).status_code, 200)
            ws.send_json({"type": "user", "text": "hello"})
            self.assertEqual(ws.receive_json()["type"], "done")
        self.assertEqual(self._titles(sid), ["Renamed"])

    def test_b_delete_mid_turn_stays_deleted(self):
        self.delay = 1.0
        sid = self.c.post("/v1/console/chat/sessions", json={"title": "ToDelete"}).json()["session"]["sid"]
        with self.c.websocket_connect(f"/v1/console/chat/sessions/{sid}/stream") as ws:
            ws.receive_json()
            ws.send_json({"type": "user", "text": "secret text"})
            time.sleep(0.2)
            self.assertEqual(self.c.delete(f"/v1/console/chat/sessions/{sid}").status_code, 200)
            types = []
            while "done" not in types:
                types.append(ws.receive_json()["type"])
        time.sleep(1.2)  # past the stub's end-of-turn writes, had it not been cancelled
        self.assertEqual(self._titles(sid), [])
        self.assertIsNone(self.cr.get_session(self.tid, sid))
        self.assertFalse(self.cr._turns_path(self.tid, sid).exists(),
                         "the deleted chat's turn log was recreated")

    def test_d_user_cancel_is_not_reported_as_a_deletion(self):
        """Review R3: the delete-cancel branch also fired for a user cancel,
        so every Stop showed "This chat was deleted." and a second done."""
        self.delay = 2.0
        sid = self.c.post("/v1/console/chat/sessions", json={"title": "Keep"}).json()["session"]["sid"]
        with self.c.websocket_connect(f"/v1/console/chat/sessions/{sid}/stream") as ws:
            ws.receive_json()
            ws.send_json({"type": "user", "text": "long"})
            time.sleep(0.2)
            ws.send_json({"type": "cancel"})
            self.assertEqual(ws.receive_json(), {"type": "done"})
            ws.send_json({"type": "ping"})
            self.assertEqual(ws.receive_json()["type"], "pong")
        self.assertEqual(self._titles(sid), ["Keep"])

    def test_e_cap_eviction_never_picks_a_chat_with_a_running_turn(self):
        with patch.object(self.cr, "_MAX_SESSIONS_PER_TENANT", 2):
            a = self.cr.create_session(self.tid, title="running")
            time.sleep(0.01)
            b = self.cr.create_session(self.tid, title="idle")
            # a is the oldest, but its turn is live
            task = object()
            self.cr.register_live_turn(self.tid, a.sid, None, task)
            try:
                self.cr.create_session(self.tid, title="new")
            finally:
                self.cr.unregister_live_turn(self.tid, a.sid, None, task)
            self.assertIsNotNone(self.cr.get_session(self.tid, a.sid))
            self.assertIsNone(self.cr.get_session(self.tid, b.sid))

    def test_c_two_tabs_share_the_chat_state(self):
        sid = self.c.post("/v1/console/chat/sessions", json={}).json()["session"]["sid"]
        with self.c.websocket_connect(f"/v1/console/chat/sessions/{sid}/stream") as a, \
                self.c.websocket_connect(f"/v1/console/chat/sessions/{sid}/stream") as b:
            a.receive_json(); b.receive_json()
            a.send_json({"type": "user", "text": "t1"}); a.receive_json()
            b.send_json({"type": "user", "text": "t2"}); b.receive_json()
        self.assertEqual(self.seen, [("t1", False), ("t2", True)])
        self.assertEqual(self.cr.get_session(self.tid, sid).turn_count, 2)


if __name__ == "__main__":
    unittest.main()
