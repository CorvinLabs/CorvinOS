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
        self.assertTrue(s["unlocked"])          # no login: the tenant's default space is simply open
        self.assertTrue(s["default"])
        self.assertFalse(s["has_passphrase"])
        body = self._create()
        self.assertTrue(body["recovery_code"])
        mine = self.a.get("/personal/status").json()
        self.assertTrue(mine["unlocked"])
        self.assertFalse(mine["default"])       # a passphrase space made through the API stays a separate space
        self.assertTrue(mine["has_passphrase"])
        self.assertEqual(self.a.post("/personal/lock").status_code, 200)
        after = self.a.get("/personal/status").json()
        self.assertTrue(after["unlocked"])
        self.assertTrue(after["default"])       # locking the passphrase space falls back to the default one
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

    # ---- the default space: no login ---------------------------------------------------------------

    def test_default_space_needs_no_login_and_is_one_space(self):
        from core.personal_kb import owner, paths
        s = self.a.get("/personal/status").json()
        self.assertTrue(s["unlocked"])
        self.assertEqual((s["default"], s["has_passphrase"], s["entries"], s["auto_capture"]), (True, False, 0, False))
        self.assertEqual(self.a.post("/personal/entities", json={"type": "note", "title": "no login needed"}).status_code, 201)
        # a second console session of the same tenant sees the same space, and no second one is created
        r = self.b.get("/personal/entities")
        self.assertEqual([e["title"] for e in r.json()["entities"]], ["no login needed"])
        oid = owner.find_default("_default")
        self.assertEqual(len([o for o in owner.list_owner_ids("_default")]), 1)
        # locking it only drops the session: the next request opens the same space again
        self.assertEqual(self.a.post("/personal/lock").status_code, 200)
        self.assertEqual(len(self.a.get("/personal/entities").json()["entities"]), 1)
        self.assertEqual(owner.find_default("_default"), oid)
        # on disk: a device wrap only (no passphrase, no recovery code) and no plaintext
        info = owner.owner_info("_default", oid)
        self.assertEqual((info["has_passphrase"], info["has_device"], info["default"]), (False, True, True))
        for f in paths.owner_dir("_default", oid).rglob("*"):
            if f.is_file():
                self.assertNotIn(b"no login needed", f.read_bytes())

    def test_default_space_learning_starts_off_and_switching_it_on_records_consent(self):
        self.assertFalse(self.a.get("/personal/status").json()["auto_capture"])
        self.assertEqual(self.a.post("/personal/settings", json={"auto_capture": True}).status_code, 200)
        self.assertTrue(self.a.get("/personal/status").json()["auto_capture"])
        from corvin_console.routes import personal_kb as routes
        s = next(iter(routes._stores.values()))
        self.assertEqual(s.meta["consent"]["version"], "pkb-consent-1")

    def test_default_space_has_no_passphrase_to_change_and_erases_without_one(self):
        from core.personal_kb import owner, paths
        self.a.post("/personal/entities", json={"type": "note", "title": "bye"})
        oid = owner.find_default("_default")
        self.assertEqual(self.a.post("/personal/passphrase", json={"old": "x" * 12, "new": "y" * 14}).status_code, 400)
        r = self.a.post("/personal/erase", json={})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["entries"], 1)
        self.assertFalse(paths.owner_dir("_default", oid).exists())
        self.assertFalse(paths.device_key_files("_default", oid))
        # always on: the next visit finds a fresh, empty default space
        s = self.a.get("/personal/status").json()
        self.assertEqual((s["unlocked"], s["default"], s["entries"]), (True, True, 0))
        self.assertNotEqual(owner.find_default("_default"), oid)

    def test_tasks_and_graph_open_the_default_space_without_visiting_the_panel_first(self):
        # a brand-new console session whose first personal request is NOT /personal/status
        tasks = self.a.get("/task-tracking/items?scope=personal").json()
        self.assertEqual((tasks["unlocked"], tasks["items"]), (True, []))
        made = self.a.post("/personal/entities", json={"type": "task", "title": "from the default space"})
        self.assertEqual(made.status_code, 201, made.text)
        graph = self.b.get("/plugins/corvin-knowledge/graph?scope=personal").json()
        self.assertTrue(graph["unlocked"])
        self.assertIn(made.json()["uid"], [x["id"] for x in graph["entities"]])
        self.assertEqual([i["title"] for i in self.b.get("/task-tracking/items?scope=personal").json()["items"]],
                         ["from the default space"])

    def test_csrf_required_for_changes(self):
        self._create()
        r = self.a.post("/personal/entities", json={"type": "note", "title": "x"}, headers={"x-csrf-token": "0" * 64})
        self.assertEqual(r.status_code, 403)

    def test_stolen_sid_and_token_reuse_open_nothing(self):
        self._create()
        self.a.post("/personal/entities", json={"type": "note", "title": "anna only"})
        token = self.a.cookies.get("corvin_personal")
        self.assertTrue(token)
        from fastapi.testclient import TestClient
        thief = TestClient(self.app, base_url=BASE, raise_server_exceptions=False)
        thief.cookies.set("corvin_console_sid", self.a.rec.sid)  # read from the sessions directory
        # The thief is an authenticated console session, so it gets the tenant's default space (no login) —
        # never the passphrase space whose token it stole.
        r = thief.get("/personal/entities")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["entities"], [])
        self.b.cookies.set("corvin_personal", token)  # the token with another session id
        r = self.b.get("/personal/entities")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["entities"], [])
        self.assertEqual([e["title"] for e in self.a.get("/personal/entities").json()["entities"]], ["anna only"])

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
        other = self.b.get("/task-tracking/items?scope=personal").json()   # another session: the default space, not anna's
        self.assertTrue(other["unlocked"])
        self.assertEqual(other["items"], [])
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
        og = self.b.get("/plugins/corvin-knowledge/graph?scope=personal").json()
        self.assertTrue(og["unlocked"])
        self.assertNotIn(e["uid"], [x["id"] for x in og["entities"]])
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

    # ---- ADR-2329: the decoder keeps itself consistent -----------------------------------------

    def _fake_engine(self, plan_tasks=None, shape=None):
        from core.personal_kb import decoder

        good = {"shape": shape or {"goal": "g", "boundaries": ["b"], "why_now": "w", "success": "s"},
                "choices": {"meaning": "m", "reach": "r", "doing": "d", "decisions": [], "no_decision_needed": "simple"},
                "plan": {"tasks": plan_tasks or [{"title": "Buy pots", "dod": "pots at home", "depends_on": []},
                                                 {"title": "Plant herbs", "dod": "herbs planted", "depends_on": [0]},
                                                 {"title": "Water daily", "dod": "alive after two weeks", "depends_on": [1]}]},
                "adversarial": {"objections": [{"category": "risk", "text": "frost"}, {"category": "gap", "text": "light"}]}}
        orig = decoder.default_generate
        decoder.default_generate = lambda tenant_id: (lambda stage, prompt: json.loads(json.dumps(good[stage])))
        self.addCleanup(lambda: setattr(decoder, "default_generate", orig))

    def _planned_idea(self, title="Balcony herbs"):
        idea = self.a.post("/personal/entities", json={"type": "idea", "title": title}).json()
        for stage in ("shape", "choices", "plan"):
            r = self.a.post(f"/personal/ideas/{idea['uid']}/unfold", json={})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["stage"], stage)
        st = self.a.get(f"/personal/ideas/{idea['uid']}/decoder").json()
        return idea, st["decoder"]

    def _task(self, uid, *steps, reason=None):
        for to in steps:
            body = {"to": to}
            if to == "cancelled":
                body["reason"] = reason or "not needed"
            if to == "done":
                body["dod"] = "checked"
            r = self.a.post(f"/personal/tasks/{uid}/transition", json=body)
            self.assertEqual(r.status_code, 200, r.text)

    def test_v2_plan_stores_dependencies_ranks_objections_and_closes_with_a_cancelled_task(self):
        self._fake_engine()
        idea, st = self._planned_idea()
        tasks = [self.a.get(f"/personal/entities/{u}").json() for u in st["children"]["plan"]]
        blocks = {(t["uid"], ln["to"]) for t in tasks for ln in t["links"] if ln["rel"] == "blocks"}
        self.assertEqual(blocks, {(tasks[0]["uid"], tasks[1]["uid"]), (tasks[1]["uid"], tasks[2]["uid"])})
        self.assertEqual([o["rank"] for o in st["objections"] if o["stage"] == "plan"], [1, 2])
        self.assertEqual(st["loss"], 0.0)                       # objections are advice, not loss
        self._task(tasks[0]["uid"], "cancelled")
        self._task(tasks[1]["uid"], "in_progress", "done")
        self.assertEqual(self.a.get(f"/personal/entities/{idea['uid']}").json()["status"], "exploring")
        self._task(tasks[2]["uid"], "in_progress", "done")
        self.assertEqual(self.a.get(f"/personal/entities/{idea['uid']}").json()["status"], "realized")

    def test_v2_forgetting_the_plan_lowers_the_cursor_and_the_decoder_continues(self):
        self._fake_engine()
        idea, st = self._planned_idea()
        for u in st["children"]["plan"]:
            self.assertEqual(self.a.delete(f"/personal/entities/{u}").status_code, 200)
        now = self.a.get(f"/personal/ideas/{idea['uid']}/decoder").json()
        self.assertEqual((now["decoder"]["stage"], now["decoder"]["children"]["plan"], now["next_stage"]), ("choices", [], "plan"))
        r = self.a.post(f"/personal/ideas/{idea['uid']}/unfold", json={})
        self.assertEqual((r.status_code, r.json()["stage"]), (200, "plan"))
        after = self.a.get(f"/personal/ideas/{idea['uid']}/decoder").json()["decoder"]
        self.assertEqual(after["iterations"], 1)                # a re-run, whatever the cursor said

    def test_v2_review_items_dismiss_and_acknowledge_over_http(self):
        self._fake_engine()
        a = self.a.post("/personal/entities", json={"type": "idea", "title": "Build a bird house for the garden"}).json()
        b = self.a.post("/personal/entities", json={"type": "idea", "title": "Build a bird house for the garden too"}).json()
        items = self.a.get("/personal/consistency").json()["items"]
        self.assertIn("DUPLICATE_IDEAS", [i["code"] for i in items])
        r = self.a.post("/personal/consistency/acknowledge", json={"a": b["uid"], "b": a["uid"]})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertNotIn("DUPLICATE_IDEAS", [i["code"] for i in self.a.get("/personal/consistency").json()["items"]])
        idea, st = self._planned_idea("Repaint the fence")
        plan = st["children"]["plan"]
        for u in plan[:-1]:
            self._task(u, "in_progress", "done")
        self.assertEqual(self.a.delete(f"/personal/entities/{plan[-1]}").status_code, 200)   # the last open task, forgotten
        self.assertEqual(self.a.get(f"/personal/entities/{idea['uid']}").json()["status"], "exploring")
        codes = [(i["code"], i["uid"]) for i in self.a.get("/personal/consistency").json()["items"]]
        self.assertIn(("STUCK_REALIZATION", idea["uid"]), codes)                         # a nudge, never an automatic close
        self.assertEqual(self.a.post(f"/personal/ideas/{idea['uid']}/decoder/dismiss", json={"code": "STUCK_REALIZATION"}).status_code, 200)
        self.assertEqual(self.a.post(f"/personal/ideas/{idea['uid']}/decoder/dismiss", json={"code": "DANGLING_LINK"}).status_code, 400)
        self.assertNotIn(("STUCK_REALIZATION", idea["uid"]), [(i["code"], i["uid"]) for i in self.a.get("/personal/consistency").json()["items"]])
        self.assertEqual(self.a.get(f"/personal/entities/{idea['uid']}").json()["status"], "exploring")   # the owner's call stands

    def test_v2_a_new_session_sweeps_what_was_left_stale(self):
        self._fake_engine()
        idea, st = self._planned_idea()
        from core.personal_kb import owner
        from core.personal_kb.store import PersonalStore
        oid, keys = owner.unlock_default("_default")
        s = PersonalStore.open("_default", oid, keys)
        s.forget(st["children"]["plan"][0])                         # behind the console's back: no sweep ran
        self.assertIn(st["children"]["plan"][0], self.a.get(f"/personal/ideas/{idea['uid']}/decoder").json()["decoder"]["children"]["plan"])
        from fastapi.testclient import TestClient
        c = self._client(TestClient)                                # opening the space runs the full sweep
        self.assertTrue(c.get("/personal/status").json()["unlocked"])
        plan = c.get(f"/personal/ideas/{idea['uid']}/decoder").json()["decoder"]["children"]["plan"]
        self.assertNotIn(st["children"]["plan"][0], plan)
        self.assertEqual(len(plan), 2)
        chain = (self.root / "tenants" / "_default" / "global" / "forge" / "audit.jsonl").read_text(encoding="utf-8")
        self.assertIn("personal_kb.decoder_swept", chain)
        self.assertNotIn("Balcony herbs", chain)

    def test_v2_review_fixes_over_http(self):
        self._fake_engine()
        idea, st = self._planned_idea("Clean the attic")
        # close is refused while the current plan is open, allowed once it is finished (checked again server-side)
        r = self.a.post(f"/personal/ideas/{idea['uid']}/decoder/close")
        self.assertEqual((r.status_code, r.headers.get("cache-control")), (409, "no-store"))
        # acknowledge: 400 for the same uid or a non-idea, idempotent in either direction
        a = self.a.post("/personal/entities", json={"type": "idea", "title": "Plant tulips by the gate"}).json()
        b = self.a.post("/personal/entities", json={"type": "idea", "title": "Plant tulips by the gate again"}).json()
        self.assertEqual(self.a.post("/personal/consistency/acknowledge", json={"a": a["uid"], "b": a["uid"]}).status_code, 400)
        self.assertEqual(self.a.post("/personal/consistency/acknowledge", json={"a": a["uid"], "b": st["children"]["plan"][0]}).status_code, 400)
        self.assertEqual(self.a.post("/personal/consistency/acknowledge", json={"a": a["uid"], "b": b["uid"]}).status_code, 200)
        v = self.a.get(f"/personal/entities/{b['uid']}").json()["version"]
        self.assertEqual(self.a.post("/personal/consistency/acknowledge", json={"a": b["uid"], "b": a["uid"]}).status_code, 200)
        self.assertEqual(self.a.get(f"/personal/entities/{b['uid']}").json()["version"], v)       # no second, reciprocal link
        # dismiss twice: one write
        v0 = self.a.get(f"/personal/entities/{idea['uid']}").json()["version"]
        for _ in range(2):
            self.assertEqual(self.a.post(f"/personal/ideas/{idea['uid']}/decoder/dismiss", json={"code": "STUCK_REALIZATION"}).status_code, 200)
        self.assertEqual(self.a.get(f"/personal/entities/{idea['uid']}").json()["version"], v0 + 1)
        # a remark is resolved only with the text the owner saw
        r = self.a.post(f"/personal/ideas/{idea['uid']}/decoder/objection", json={"index": 0, "action": "dismiss", "reason": "x", "expect": "not this"})
        self.assertEqual(r.status_code, 400)
        # forgetting the idea names the untouched drafts that went with it
        r = self.a.delete(f"/personal/entities/{idea['uid']}")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["drafts_removed"], 5)                                           # shape, choices, 3 tasks
        self.assertEqual([e for e in self.a.get("/personal/entities").json()["entities"] if e["title"].startswith(("Shape:", "Choices:"))], [])

    def test_v2_knob_changes_are_proposed_not_applied(self):
        bad = {"goal": "", "boundaries": [], "why_now": "", "success": ""}
        self._fake_engine(shape=bad)
        for i in range(3):
            idea = self.a.post("/personal/entities", json={"type": "idea", "title": f"Idea number {i} about the shed"}).json()
            r = self.a.post(f"/personal/ideas/{idea['uid']}/unfold", json={})
            self.assertEqual((r.status_code, r.json()["gate_ok"]), (200, False))
        pol = self.a.get("/personal/policy").json()
        self.assertEqual(pol["current"]["decoder_template_variant"], "t1")
        self.assertEqual(pol["pending"]["target"]["decoder_template_variant"], "t0_strict")     # kept, not overwritten
        self.assertEqual(pol["pending"]["target"]["decoder_adversarial_rounds"], 2)              # joined by the second suggestion
        self.assertEqual(pol["current"]["decoder_adversarial_rounds"], 1)
        chain = (self.root / "tenants" / "_default" / "global" / "forge" / "audit.jsonl").read_text(encoding="utf-8")
        self.assertIn("personal_kb.decoder_proposal", chain)

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
        after = self.a.get("/personal/status").json()
        self.assertTrue(after["default"])        # an erased passphrase space leaves the default space, empty
        self.assertEqual(after["entries"], 0)
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
        self.a.post("/personal/entities", json={"type": "note", "title": "anna only"})
        r = self.a.get("/task-tracking/items?scope=personal")
        self.assertEqual(r.headers.get("cache-control"), "no-store")
        token = self.a.cookies.get("corvin_personal")
        self.assertEqual(self.a.post("/auth/logout").status_code, 204)
        from core.personal_kb import session
        self.assertIsNone(session.get_console(token, tenant_id="_default", sid=self.a.rec.sid))
        from fastapi.testclient import TestClient
        c = self._client(TestClient)
        c.cookies.set("corvin_personal", token)
        r = c.get("/personal/entities")          # a new console session opens the default space, not anna's
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["entities"], [])

    def test_locked_store_is_evicted_from_the_route_cache(self):
        self._create()
        self.assertEqual(self.a.get("/personal/entities").status_code, 200)
        from corvin_console.routes import personal_kb as routes
        self.assertEqual(len(routes._stores), 1)
        self.assertEqual(self.a.post("/personal/lock").status_code, 200)
        self.b.get("/personal/status")
        self._create(c=self.b, handle="bert")
        self.b.get("/personal/entities")
        from core.personal_kb import owner
        anna = owner.find_by_handle("_default", "anna")
        self.assertNotIn(anna, [s.owner_id for s in routes._stores.values()])   # anna's decrypted store is gone

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
