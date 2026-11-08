"""Release review F1 / ADR-2315 plan E7 - the operator chooses where projects live, over real HTTP.

Before: `registry.set_projects_root` had no production caller, so every install put projects in ~/projects with no way to change it.
What must hold: the root is changed audit-first with CSRF; it is created if missing (0o700); a relative path, '..', '/', the CorvinOS home, a
protected component or a file is refused and nothing is written; projects already outside the new root are named, never moved or dropped;
another tenant's setting is untouched; the audit record carries no path.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import test_projects_api_e2e as base  # noqa: E402
from test_projects_ops_e2e import _Base  # noqa: E402

_URL = base._URL


@unittest.skipUnless(base._MARKET.is_file(), "needs the Marketplace checkout")
class ProjectsRoot(_Base):
    def test_change_the_root_and_the_refusals(self):
        with self._ctx() as c:
            pid, root = self._new(c, "Before the move")
            h = self._h(c)
            new = self.tmp / "elsewhere" / "my-projects"
            r = c.own.put(f"{_URL}/root", json={"path": str(new)}, headers=h)
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["projects_root"], str(new))
            self.assertTrue(new.is_dir())
            self.assertEqual(oct(new.stat().st_mode & 0o777), "0o700")
            self.assertEqual(r.json()["outside"], [pid], "a project left outside the new root is named")
            self.assertEqual(c.own.get(f"{_URL}/{pid}").json()["state"], "ok", "and keeps working")
            self.assertEqual(c.own.get(_URL).json()["projects_root"], str(new))
            self.assertEqual(c.other.get(_URL).json()["projects_root"], str(c.root), "another tenant is untouched")
            (self.tmp / "a-file").write_text("x")
            for bad in ("relative/dir", str(self.tmp / "a" / ".." / "b"), "/", str(c.home), str(c.home / "tenants"),
                        str(self.tmp / "x" / "session_ledger" / "p"), str(self.tmp / "a-file"), ""):
                rr = c.own.put(f"{_URL}/root", json={"path": bad}, headers=h)
                self.assertIn(rr.status_code, (400, 422), (bad, rr.text))
            self.assertEqual(c.own.get(_URL).json()["projects_root"], str(new), "a refusal changed nothing")
            self.assertIn(c.own.put(f"{_URL}/root", json={"path": str(new)}, headers={"X-CSRF-Token": "x"}).status_code, (401, 403))
            ev = [e for e in base._audit_events(c.home) if (e.get("event") or e.get("event_type")) == "project.root_changed"]
            self.assertTrue(ev)
            self.assertNotIn(str(new), json.dumps(ev), "the audit record must not carry the path")
            for e in ev:
                self.assertNotIn("_dropped_fields", e.get("details", e))
            # a project can be created under the new root
            plan = c.own.post(f"{_URL}/plan", json={"template": "demo", "title": "After the move"}, headers=h).json()
            self.assertTrue(plan["location"].startswith(str(new)), plan["location"])


if __name__ == "__main__":
    unittest.main()
