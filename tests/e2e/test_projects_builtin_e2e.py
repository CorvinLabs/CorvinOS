"""The built-in "Corvin" project (CONCEPT-0129): Corvin itself is a project on every install, in the list, in the chat menu, in the
explorer and the change view. Real HTTP, a real bound knowledge base (a git repo), real sessions for two tenants.

What must hold: it is first in the list, marked built in, never removable, renameable, movable, settable or repairable (409 builtin) and the
id can never be taken by a user project; its structure, changes and activity read the tenant's OWN knowledge base; another tenant sees ITS
own knowledge base and never ours; attaching it to a chat is read-only and goes through the same gates; the board and graph scopes accept it.
"""
from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import test_projects_api_e2e as base  # noqa: E402
from test_projects_ops_e2e import _Base  # noqa: E402

_URL = base._URL


@unittest.skipUnless(base._MARKET.is_file(), "needs the Marketplace checkout")
class BuiltinCorvin(_Base):
    def test_it_is_first_marked_built_in_and_protected_from_every_mutation(self):
        with self._ctx() as c:
            self._new(c, "A user project")
            lst = c.own.get(_URL).json()["projects"]
            self.assertEqual([p["id"] for p in lst][0], "corvin")
            corvin = lst[0]
            self.assertTrue(corvin["builtin"])
            self.assertEqual((corvin["state"], corvin["title"], corvin["classification"], corvin["assistant_access"]),
                             ("ok", "Corvin", "internal", "read"))
            self.assertEqual(len(lst), 2)
            h = self._h(c)
            for method, url, body in (("delete", f"{_URL}/corvin", None), ("put", f"{_URL}/corvin/title", {"title": "x"}),
                                      ("post", f"{_URL}/corvin/move", {"location": str(c.root / "elsewhere")}),
                                      ("put", f"{_URL}/corvin/settings", {"assistant_access": "write", "confirm": True}),
                                      ("put", f"{_URL}/corvin/features", {"set": {"acs": True}, "accept_dependencies": True}),
                                      ("post", f"{_URL}/corvin/repair", {}), ("post", f"{_URL}/corvin/videos", {"name": "abc"})):
                r = getattr(c.own, method)(url, headers=h, **({"json": body} if body is not None else {}))
                self.assertEqual(r.status_code, 409, (method, url, r.text))
                self.assertEqual(r.json()["detail"]["reason_class"], "builtin", (method, url))
            # the id is reserved for users: plan and adopt cannot take it
            p = c.own.post(f"{_URL}/plan", json={"template": "demo", "title": "Corvin"}, headers=h).json()
            self.assertNotEqual(p["project_id"], "corvin")
            self.assertEqual(c.own.post(f"{_URL}/plan", json={"template": "demo", "title": "x", "project_id": "corvin"}, headers=h).status_code, 400)

    def test_it_reads_the_tenants_own_knowledge_base_and_nobody_elses(self):
        with self._ctx() as c:
            d = c.own.get(f"{_URL}/corvin").json()
            self.assertEqual(Path(d["path"]).resolve(), self.bound.resolve())
            self.assertIn("kb", d["active"])
            names = [e["path"] for e in c.own.get(f"{_URL}/corvin/structure").json()["entries"]]
            self.assertIn("kb", names)
            (self.bound / "notes").mkdir(exist_ok=True)
            (self.bound / "notes" / "NOTE-9999-x.md").write_text("hello\n")
            ch = c.own.get(f"{_URL}/corvin/changes").json()
            hit = [f for f in ch["files"] if f["path"] == "notes/NOTE-9999-x.md"]
            self.assertTrue(hit and hit[0]["links"] == [{"id": "NOTE-9999", "kind": "knowledge"}], ch["files"])
            self.assertIn("hello", c.own.get(f"{_URL}/corvin/changes/file", params={"path": "notes/NOTE-9999-x.md"}).json()["text"])
            self.assertTrue(c.own.get(f"{_URL}/corvin/activity").status_code == 200)
            # the other tenant has no bound knowledge base of the operator: its Corvin tile must not point at ours
            o = c.other.get(f"{_URL}/corvin")
            if o.status_code == 200:
                self.assertNotEqual(Path(o.json()["path"]).resolve(), self.bound.resolve(), "another tenant was shown our knowledge base")
            self.assertEqual(c.other.get(f"{_URL}/corvin/changes/file", params={"path": "notes/NOTE-9999-x.md"}).status_code in (404, 400, 409), True)

    def test_attaching_it_to_a_chat_is_read_only_and_goes_through_the_same_gates(self):
        from core.projects import attachment
        with self._ctx() as c:
            h = self._h(c)
            r0 = c.own.post("/v1/console/chat/sessions", json={"title": "x"}, headers=h)
            sid = (r0.json().get("session") or {}).get("sid") or r0.json().get("sid") or r0.json().get("session_id")
            r = c.own.post(f"{_URL}/corvin/attach", json={"session_id": sid}, headers=h)
            self.assertEqual(r.status_code, 200, r.text)
            parts = attachment.spawn_parts("_default", sid, class_allows=lambda x: True)
            self.assertEqual((parts["project_id"], parts["access"]), ("corvin", "read"))
            self.assertTrue(any(d.startswith("Write(//") and d.endswith("/**)") for d in parts["deny"]), parts["deny"])
            self.assertIsNone(parts["mcp_config"])
            self.assertEqual(c.own.get(f"{_URL}/chat-attachment", params={"session_id": sid}).json()["project_id"], "corvin")
            self.assertIsNone(attachment.spawn_parts("_default", sid, class_allows=lambda x: False), "the data-flow gate still decides")

    def test_the_board_and_graph_scopes_accept_it(self):
        with self._ctx() as c:
            r = c.own.get("/v1/console/task-tracking/items", params={"scope": "project:corvin"})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(c.own.get("/v1/console/task-tracking/items", params={"scope": "project:nope"}).status_code, 404)


if __name__ == "__main__":
    unittest.main()
