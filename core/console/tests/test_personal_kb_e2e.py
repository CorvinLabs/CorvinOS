"""Personal knowledge base over the REAL console app (ADR-2325/2326/2327, PLAN-0973).

Real ``create_app()``, real console sessions (``auth.create_session``) and real CSRF tokens; the
only stand-in is the decoder's generation function (no engine in CI). Sandbox: CORVIN_HOME,
XDG_CONFIG_HOME, HOME and the anchor key in a temp dir, FORGE_ROOT unset.

Covers AC-S4, S6, S8, AC-O3, O5, O6, AC-D1, AC-U* API contracts.
"""
from __future__ import annotations

import io
import json
import os
import secrets
import sqlite3
import sys
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
for _p in ("core/console", "corvin_operator/bridges/shared", "corvin_operator/forge"):
    sys.path.insert(0, str(_REPO / _p))
sys.path.insert(0, str(_REPO))
warnings.simplefilter("ignore")

PW = "Lantern-Orchard-77-blue"
BASE = "http://127.0.0.1:8765"


class PersonalKbHttpE2E(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        keys = ("CORVIN_HOME", "XDG_CONFIG_HOME", "HOME", "USERPROFILE", "CORVIN_TENANT_ID", "CORVIN_AUDIT_ANCHOR_KEY",
                "FORGE_ROOT", "CORVIN_CONSOLE_NO_AUTOBUILD", "CORVIN_KB_REPO")
        saved = {k: os.environ.get(k) for k in keys}

        def _restore():
            for k, v in saved.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        self.addCleanup(_restore)
        os.environ.update({"CORVIN_HOME": str(root / "corvin"), "XDG_CONFIG_HOME": str(root / "xdg"),
                           "HOME": str(root / "home"), "USERPROFILE": str(root / "home"),
                           "CORVIN_TENANT_ID": "_default", "CORVIN_AUDIT_ANCHOR_KEY": str(root / "anchor.key"),
                           "CORVIN_CONSOLE_NO_AUTOBUILD": "1"})
        os.environ.pop("FORGE_ROOT", None)
        os.environ.pop("CORVIN_KB_REPO", None)
        self.root = root / "corvin"
        from core.personal_kb import capabilities, crypto, limits, session

        self._kdf = dict(crypto.KDF_PARAMS)
        crypto.KDF_PARAMS.update({"n": 2 ** 10})
        self.addCleanup(lambda: crypto.KDF_PARAMS.update(self._kdf))
        limits._reset_for_tests()
        session._reset_for_tests()
        capabilities._set_for_tests(None)
        from fastapi.testclient import TestClient
        from corvin_console import app as console_app

        self.app = console_app.create_app()
        self.a = self._client(TestClient)
        self.b = self._client(TestClient)

    def _client(self, TestClient):
        from corvin_console import auth

        rec = auth.create_session(tenant_id="_default")
        c = TestClient(self.app, base_url=BASE, raise_server_exceptions=False)
        c.cookies.set("corvin_console_sid", rec.sid)
        c.headers["x-csrf-token"] = auth.derive_csrf_token(rec.csrf_secret, rec.sid)
        c.rec = rec  # type: ignore[attr-defined]
        return c

    def _create(self, c=None, handle="anna", mode="convenient"):
        c = c or self.a
        r = c.post("/personal/owner", json={"handle": handle, "passphrase": PW, "consent": True, "unlock_mode": mode})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.headers.get("cache-control"), "no-store")
        return r.json()

    # ---------------------------------------------------------------------------------------
    def test_create_status_lock_unlock_and_identical_failures(self):
        s = self.a.get("/personal/status").json()
        self.assertFalse(s["unlocked"])
        body = self._create()
        self.assertTrue(body["recovery_code"])
        self.assertTrue(self.a.get("/personal/status").json()["unlocked"])
        self.assertEqual(self.a.post("/personal/lock").status_code, 200)
        self.assertFalse(self.a.get("/personal/status").json()["unlocked"])
        from fastapi.testclient import TestClient
        r1 = self.b.post("/personal/unlock", json={"handle": "ghost", "passphrase": PW})
        r2 = self._client(TestClient).post("/personal/unlock", json={"handle": "anna",
                                                                     "passphrase": "Wrong-Passphrase-1234"})
        self.assertEqual((r1.status_code, r1.json()), (r2.status_code, r2.json()))
        self.assertEqual(r1.status_code, 403)
        c = self._client(TestClient)
        self.assertEqual(c.post("/personal/unlock", json={"handle": "anna", "passphrase": PW}).status_code, 429)
        import time
        time.sleep(1.2)  # bounded per-handle delay after a failure; never a lockout
        self.assertEqual(c.post("/personal/unlock", json={"handle": "anna", "passphrase": PW}).status_code, 200)
        self.assertTrue(c.get("/personal/status").json()["unlocked"])

    def test_csrf_required_for_changes(self):
        self._create()
        r = self.a.post("/personal/entities", json={"type": "note", "title": "x"}, headers={"x-csrf-token": "0" * 64})
        self.assertEqual(r.status_code, 403)

    def test_stolen_sid_and_token_reuse_open_nothing(self):
        self._create()
        token = self.a.cookies.get("corvin_personal")
        self.assertTrue(token)
        from fastapi.testclient import TestClient
        thief = TestClient(self.app, base_url=BASE, raise_server_exceptions=False)
        thief.cookies.set("corvin_console_sid", self.a.rec.sid)  # read from the sessions directory
        self.assertEqual(thief.get("/personal/entities").status_code, 423)
        self.b.cookies.set("corvin_personal", token)  # the token with another session id
        self.assertEqual(self.b.get("/personal/entities").status_code, 423)
        self.assertEqual(self.a.get("/personal/entities").status_code, 200)

    def test_entities_tasks_scope_and_shared_store_untouched(self):
        self._create()
        marker = "MARK" + secrets.token_hex(5)
        e = self.a.post("/personal/entities", json={"type": "task", "title": f"Task {marker}"}).json()
        self.assertEqual(e["display_id"], "me/T-0001")
        r = self.a.post(f"/personal/tasks/{e['uid']}/transition", json={"to": "in_progress"})
        self.assertEqual(r.status_code, 200, r.text)
        r = self.a.post(f"/personal/tasks/{e['uid']}/transition", json={"to": "done"})
        self.assertEqual(r.status_code, 400)  # done needs a definition of done
        scoped = self.a.get("/task-tracking/items?scope=personal").json()
        self.assertEqual([i["title"] for i in scoped["items"]], [f"Task {marker}"])
        self.assertEqual(scoped["items"][0]["status"], "in_progress")
        self.assertFalse(self.b.get("/task-tracking/items?scope=personal").json()["unlocked"])
        unscoped = self.a.get("/task-tracking/items")
        self.assertNotIn(marker, unscoped.text)
        # positive control: a normal board item's title IS found in tasks.db by the same scan
        control = "CTRL" + secrets.token_hex(5)
        made = self.a.post("/task-tracking/items", json={"kind": "task", "title": f"Board {control}"})
        self.assertIn(made.status_code, (200, 201), made.text)
        self.assertIn(control, self.a.get("/task-tracking/items").text)
        dbs = list(self.root.rglob("tasks.db"))
        self.assertTrue(dbs, "positive control: the shared task store exists")
        blob = b"".join(p.read_bytes() for d in dbs for p in [d, *d.parent.glob("tasks.db-*")])
        self.assertIn(control.encode(), blob)
        self.assertNotIn(marker.encode(), blob)
        g = self.a.get("/plugins/corvin-knowledge/graph?scope=personal").json()
        self.assertTrue(g["unlocked"])
        self.assertIn(e["uid"], [x["id"] for x in g["entities"]])
        self.assertFalse(self.b.get("/plugins/corvin-knowledge/graph?scope=personal").json()["unlocked"])
        self.assertEqual(self.a.get("/plugins/corvin-knowledge/graph?scope=personal&project=x").status_code, 422)

    def test_owner_isolation_by_uid(self):
        self._create()
        e = self.a.post("/personal/entities", json={"type": "note", "title": "anna's note"}).json()
        self._create(self.b, handle="bert")
        self.assertEqual(self.b.get(f"/personal/entities/{e['uid']}").status_code, 404)
        self.assertEqual(self.b.delete(f"/personal/entities/{e['uid']}").status_code, 404)
        self.assertEqual(self.a.get(f"/personal/entities/{e['uid']}").status_code, 200)

    def test_patch_versions_restore_forget(self):
        self._create()
        e = self.a.post("/personal/entities", json={"type": "note", "title": "v1"}).json()
        e2 = self.a.patch(f"/personal/entities/{e['uid']}", json={"title": "v2"}).json()
        self.assertEqual(e2["version"], 2)
        self.assertEqual(self.a.get(f"/personal/entities/{e['uid']}").json()["versions"], [1])
        r = self.a.post(f"/personal/entities/{e['uid']}/restore", json={"version": 1}).json()
        self.assertEqual(r["title"], "v1")
        self.assertEqual(self.a.patch(f"/personal/entities/{e['uid']}", json={"title": "x", "expected_version": 1}
                                      ).status_code, 409)
        self.assertEqual(self.a.delete(f"/personal/entities/{e['uid']}").status_code, 200)
        self.assertEqual(self.a.get(f"/personal/entities/{e['uid']}").status_code, 404)

    def test_export_in_memory(self):
        self._create()
        self.a.post("/personal/entities", json={"type": "idea", "title": "exportable idea"})
        before = {p for p in self.root.rglob("*")}
        r = self.a.get("/personal/export")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["cache-control"], "no-store")
        names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
        self.assertEqual(names, ["idea/IDEA-0001.md"])
        after = {p for p in self.root.rglob("*")}
        self.assertFalse({p for p in after - before if "personal_kb" in str(p)})

    def test_policy_routes(self):
        self._create()
        p = self.a.get("/personal/policy").json()
        self.assertEqual(p["current"]["daily_cap"], 20)
        self.assertEqual(self.a.put("/personal/policy", json={"changes": {"daily_cap": 5}}).json()["outcome"], "applied")
        self.assertEqual(self.a.put("/personal/policy", json={"changes": {"encryption": False}}).status_code, 400)
        self.assertEqual(self.a.post("/personal/policy/rollback", json={"to_version": 1}).status_code, 200)

    def test_pairing_via_console_and_bridge(self):
        self._create()
        code = self.a.post("/personal/pair").json()["code"]
        from core.personal_kb import bridge
        r = bridge.handle_command("_default", f"/me link {code}", channel="discord", uid="123456789",
                                  display="Anna", chat_type="private", chat_key="dm-x")
        self.assertIn("confirm", r.reply)
        pend = self.a.get("/personal/pair/pending").json()["pending"]
        self.assertEqual(pend[0]["masked"], "…6789")
        out = self.a.post("/personal/pair/confirm", json={"pair_id": pend[0]["pair_id"]}).json()
        self.assertEqual(out["linked"]["channel"], "discord")
        self.assertNotIn("_chat_key", json.dumps(out))
        st = self.a.get("/personal/status").json()
        self.assertEqual(st["bindings"][0]["channel"], "discord")
        self.assertEqual(self.a.post("/personal/identity/unbind", json={"index": 0}).json()["unbound"], True)

    def test_decoder_over_http(self):
        from core.personal_kb import decoder

        good = {"shape": {"goal": "g", "boundaries": ["b"], "why_now": "w", "success": "s"},
                "adversarial": {"objections": [{"category": "risk", "text": "frost"}]}}
        orig = decoder.default_generate
        decoder.default_generate = lambda tenant_id: (lambda stage, prompt: json.loads(json.dumps(good[stage])))
        self.addCleanup(lambda: setattr(decoder, "default_generate", orig))
        self._create()
        idea = self.a.post("/personal/entities", json={"type": "idea", "title": "Balcony herbs"}).json()
        r = self.a.post(f"/personal/ideas/{idea['uid']}/unfold", json={})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["stage"], "shape")
        self.assertEqual(len(r.json()["objections"]), 1)
        st = self.a.get(f"/personal/ideas/{idea['uid']}/decoder").json()
        self.assertEqual((st["decoder"]["stage"], st["next_stage"]), ("shape", "choices"))
        d = self.a.post(f"/personal/ideas/{idea['uid']}/decoder/objection",
                        json={"index": 0, "action": "dismiss", "reason": "covered"}).json()
        self.assertTrue(d["dismissed"])

    def test_promote_without_kb_fails_cleanly(self):
        self._create()
        idea = self.a.post("/personal/entities", json={"type": "idea", "title": "Promote me"}).json()
        r = self.a.post(f"/personal/entities/{idea['uid']}/promote", json={"summary": "A shared tool"})
        self.assertEqual(r.status_code, 400, r.text)
        self.assertEqual(self.a.get(f"/personal/entities/{idea['uid']}").json()["status"], "inbox")

    def test_erase_requires_passphrase_and_removes_everything(self):
        self._create()
        self.a.post("/personal/entities", json={"type": "note", "title": "bye"})
        from core.personal_kb import owner, paths
        oid = owner.find_by_handle("_default", "anna")
        self.assertEqual(self.a.post("/personal/erase", json={"passphrase": "Wrong-Passphrase-1234"}).status_code, 403)
        self.assertTrue(paths.owner_dir("_default", oid).exists())
        import time
        time.sleep(1.2)  # the per-session and per-handle delay after one failure is 1 s (no lockout)
        r = self.a.post("/personal/erase", json={"passphrase": PW, "also_chat_history": True})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["entries"], 1)       # the space is erased once, with its real count
        self.assertFalse(paths.owner_dir("_default", oid).exists())
        self.assertFalse(self.a.get("/personal/status").json()["unlocked"])
        chain = self.root / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
        text = chain.read_text(encoding="utf-8")
        self.assertIn("erasure.personal_kb_deleted", text)
        self.assertNotIn(oid, text)
        self.assertNotIn('"anna"', text)
        self.assertEqual(text.count("erasure.personal_kb_deleted"), 1)

    def test_unreadable_entries_can_be_removed(self):
        self._create()
        e = self.a.post("/personal/entities", json={"type": "note", "title": "lost"}).json()
        self.a.post("/personal/entities", json={"type": "note", "title": "kept"})
        from corvin_console.routes import personal_kb as routes
        from core.personal_kb import paths
        s = next(iter(routes._stores.values()))
        paths.object_file("_default", s.owner_id, s.manifest["objects"][e["uid"]]["oid"]).unlink()
        routes._stores.clear()
        self.assertEqual(self.a.get("/personal/status").json()["problems"], 1)
        r = self.a.post("/personal/repair")
        self.assertEqual((r.status_code, r.json()), (200, {"removed": 1}))
        self.assertEqual(self.a.get("/personal/status").json()["problems"], 0)
        self.assertEqual([x["title"] for x in self.a.get("/personal/entities").json()["entities"]], ["kept"])

    def test_logout_ends_the_unlock_and_personal_tasks_are_not_cached(self):
        self._create()
        r = self.a.get("/task-tracking/items?scope=personal")
        self.assertEqual(r.headers.get("cache-control"), "no-store")
        token = self.a.cookies.get("corvin_personal")
        self.assertEqual(self.a.post("/auth/logout").status_code, 204)
        from core.personal_kb import session
        self.assertIsNone(session.get_console(token, tenant_id="_default", sid=self.a.rec.sid))
        from fastapi.testclient import TestClient
        c = self._client(TestClient)
        c.cookies.set("corvin_personal", token)
        self.assertEqual(c.get("/personal/entities").status_code, 423)

    def test_locked_store_is_evicted_from_the_route_cache(self):
        self._create()
        self.assertEqual(self.a.get("/personal/entities").status_code, 200)
        from corvin_console.routes import personal_kb as routes
        self.assertEqual(len(routes._stores), 1)
        self.assertEqual(self.a.post("/personal/lock").status_code, 200)
        self.b.get("/personal/status")
        self._create(c=self.b, handle="bert")
        self.b.get("/personal/entities")
        self.assertEqual(len(routes._stores), 1)   # only bert's; anna's decrypted store is gone

    def test_concurrent_promote_creates_one_project_entry(self):
        import threading
        import time as _t
        from corvin_console import kb_projection
        self._create()
        idea = self.a.post("/personal/entities", json={"type": "idea", "title": "Promote me once"}).json()
        calls = []

        def slow_create(tenant_id, target, summary, body):
            calls.append(summary)
            _t.sleep(1.0)
            return {"uid": "01JPROMOTEDUID0000000000000", "id": "IDEA-0099"}
        orig = kb_projection.create_entity
        kb_projection.create_entity = slow_create
        self.addCleanup(lambda: setattr(kb_projection, "create_entity", orig))
        out = []
        url = f"/personal/entities/{idea['uid']}/promote"
        th = [threading.Thread(target=lambda: out.append(self.a.post(url, json={"summary": "Shared"}).status_code))
              for _ in range(2)]
        th[0].start()
        _t.sleep(0.3)
        th[1].start()
        for x in th:
            x.join()
        self.assertEqual(sorted(out), [200, 409])
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.a.get(f"/personal/entities/{idea['uid']}").json()["status"], "promoted")


if __name__ == "__main__":
    unittest.main()
