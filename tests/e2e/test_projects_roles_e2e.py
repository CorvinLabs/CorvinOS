"""T-0368..T-0370 / ADR-2319 - roles, classification, assistant access and the change view, over real HTTP.

Real: sessions for two tenants, the projects API, real git repositories (the project's and the nested docs one),
the real L34 gate (``spawn_gates.check_l34``: with no tenant policy a ``secret`` class is still refused - T-0254).
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
from _projects_fixtures import write_template  # noqa: E402
from test_projects_ops_e2e import _Base, _all_hashes  # noqa: E402

_URL = base._URL


def _git(root: Path, *a: str) -> str:
    return subprocess.run(["git", "-C", str(root), "-c", "user.email=a@b", "-c", "user.name=a", *a], capture_output=True, text=True,
                          check=True).stdout


@unittest.skipUnless(base._MARKET.is_file(), "needs the Marketplace checkout")
class RolesAndSettings(_Base):
    def test_templates_carry_role_metadata_and_the_plan_uses_them(self):
        write_template(self.src, "ds", ["core", "kb"], role="data_science", classification="confidential",
                       assistant_access="write", outcome="experiments, models, reports")
        with self._ctx() as c:
            t = {x["id"]: x for x in c.own.get(f"{_URL}/templates").json()["templates"]}
            self.assertEqual((t["ds"]["role"], t["ds"]["classification"], t["ds"]["assistant_access"]),
                             ("data_science", "confidential", "write"))
            self.assertEqual(t["ds"]["outcome"], "experiments, models, reports")
            self.assertEqual((t["demo"]["role"], t["demo"]["classification"], t["demo"]["assistant_access"]), ("", "internal", "read"))
            plan = c.own.post(f"{_URL}/plan", json={"template": "ds", "title": "Churn model"}, headers=self._h(c)).json()
            self.assertEqual((plan["role"], plan["classification"], plan["assistant_access"]), ("data_science", "confidential", "write"))
            r = c.own.post(_URL, json={"plan_id": plan["plan_id"]}, headers=self._h(c))
            self.assertEqual(r.status_code, 201, r.text)
            got = c.own.get(f"{_URL}/churn-model").json()
            self.assertEqual((got["role"], got["classification"], got["assistant_access"]), ("data_science", "confidential", "write"))
            # the user's override beats the template; an invalid value is refused before anything is written
            p2 = c.own.post(f"{_URL}/plan", json={"template": "ds", "title": "Other", "classification": "internal",
                                                 "assistant_access": "read"}, headers=self._h(c)).json()
            self.assertEqual((p2["classification"], p2["assistant_access"]), ("internal", "read"))
            bad = c.own.post(f"{_URL}/plan", json={"template": "ds", "title": "Bad", "classification": "top-secret"}, headers=self._h(c))
            self.assertEqual(bad.status_code, 400, bad.text)

    def test_a_template_with_a_value_outside_the_closed_lists_is_refused(self):
        write_template(self.src, "weird", ["core"], role="astronaut")
        with self._ctx() as c:
            r = c.own.get(f"{_URL}/templates")
            self.assertEqual(r.json()["available"], False)

    def test_settings_apply_at_once_unless_they_lower_protection(self):
        with self._ctx() as c:
            pid, root = self._new(c)
            h = self._h(c)
            before = _all_hashes(root)
            g = c.own.get(f"{_URL}/{pid}").json()
            self.assertEqual((g["classification"], g["assistant_access"]), ("internal", "read"))
            # raising the class is free
            r = c.own.put(f"{_URL}/{pid}/settings", json={"classification": "confidential"}, headers=h)
            self.assertEqual((r.status_code, r.json()["classification"]), (200, "confidential"), r.text)
            # lowering it, and raising access to write, need the confirming click
            for body in ({"classification": "internal"}, {"assistant_access": "write"}):
                r = c.own.put(f"{_URL}/{pid}/settings", json=body, headers=h)
                self.assertEqual(r.status_code, 409, r.text)
                self.assertEqual(r.json()["detail"]["reason_class"], "confirmation_required")
            self.assertEqual(c.own.get(f"{_URL}/{pid}").json()["assistant_access"], "read")
            r = c.own.put(f"{_URL}/{pid}/settings", json={"assistant_access": "write", "confirm": True}, headers=h)
            self.assertEqual((r.status_code, r.json()["assistant_access"]), (200, "write"), r.text)
            self.assertEqual(c.own.put(f"{_URL}/{pid}/settings", json={"classification": "nope"}, headers=h).status_code, 400)
            self.assertIn(c.own.put(f"{_URL}/{pid}/settings", json={"classification": "secret"}, headers={"X-CSRF-Token": "x"}).status_code, (401, 403))
            self.assertEqual(c.other.put(f"{_URL}/{pid}/settings", json={"classification": "secret"},
                                         headers={"X-CSRF-Token": c.other_csrf}).status_code, 404)
            after = _all_hashes(root)
            self.assertEqual({k for k in after if before.get(k) != after[k]}, {"project.yaml"})
            ev = base._audit_events(c.home)
            changed = [e for e in ev if (e.get("event") or e.get("event_type")) == "project.settings_changed"]
            self.assertGreaterEqual(len(changed), 2)
            for e in changed:
                self.assertNotIn("_dropped_fields", e.get("details", e))

    def test_attach_is_refused_by_the_real_l34_gate_for_a_secret_project_and_write_access_lifts_only_its_own_deny_rules(self):
        with self._ctx() as c:
            pid, root = self._new(c)
            h = self._h(c)
            r0 = c.own.post("/v1/console/chat/sessions", json={"title": "x"}, headers=h)
            sid = (r0.json().get("session") or {}).get("sid") or r0.json().get("sid") or r0.json().get("session_id")
            c.own.put(f"{_URL}/{pid}/settings", json={"classification": "secret"}, headers=h)
            r = c.own.post(f"{_URL}/{pid}/attach", json={"session_id": sid}, headers=h)
            self.assertEqual(r.status_code, 409, r.text)
            self.assertEqual(r.json()["detail"]["reason_class"], "classification")
            self.assertIn("data-flow", r.json()["detail"]["message"])
            c.own.put(f"{_URL}/{pid}/settings", json={"classification": "internal", "confirm": True}, headers=h)
            self.assertEqual(c.own.post(f"{_URL}/{pid}/attach", json={"session_id": sid}, headers=h).status_code, 200)
            from core.projects import attachment
            ok = lambda cls: True  # noqa: E731
            parts = attachment.spawn_parts(c.svc._tenant if hasattr(c.svc, "_tenant") else "_default", sid, class_allows=ok)
            self.assertTrue(parts and parts["deny"] and parts["access"] == "read", parts)
            c.own.put(f"{_URL}/{pid}/settings", json={"assistant_access": "write", "confirm": True}, headers=h)
            parts = attachment.spawn_parts("_default", sid, class_allows=ok)
            self.assertEqual((parts["deny"], parts["access"]), ([], "write"))
            self.assertIsNone(attachment.spawn_parts("_default", sid, class_allows=lambda cls: False), "a class the gate refuses withholds the attachment")


@unittest.skipUnless(base._MARKET.is_file(), "needs the Marketplace checkout")
class ChangeView(_Base):
    def test_changes_of_the_project_and_the_nested_docs_repo_with_diff_links_and_refusals(self):
        with self._ctx() as c:
            pid, root = self._new(c)
            _git(root, "add", "-A")
            _git(root, "commit", "--allow-empty", "-qm", "baseline T-0007 setup")
            h = {}
            clean = c.own.get(f"{_URL}/{pid}/changes").json()
            self.assertEqual(clean["files"], [])
            self.assertTrue(clean["has_repository"])
            (root / "README.md").write_text((root / "README.md").read_text() + "\nA new line written by the assistant.\n")
            (root / "notes").mkdir(exist_ok=True)
            (root / "notes" / "new.txt").write_text("one\ntwo\n")
            (root / "blob.bin").write_bytes(b"\x00\x01\x02")
            (root / "docs" / "notes").mkdir(parents=True, exist_ok=True)
            (root / "docs" / "notes" / "T-0001-idea.md").write_text("# idea\n")
            ch = c.own.get(f"{_URL}/{pid}/changes").json()
            by = {f["path"]: f for f in ch["files"]}
            self.assertEqual(by["README.md"]["status"], "modified")
            self.assertEqual((by["README.md"]["additions"], by["README.md"]["deletions"]), (2, 0))
            self.assertEqual((by["notes/new.txt"]["status"], by["notes/new.txt"]["additions"]), ("untracked", 2))
            self.assertEqual(by["docs/notes/T-0001-idea.md"]["area"], "docs")
            self.assertEqual(by["docs/notes/T-0001-idea.md"]["links"], [{"id": "T-0001", "kind": "task"}])
            self.assertEqual(by["README.md"]["area"], "code")
            # the diff of a modified file, the text of a new one, a binary is not rendered
            d = c.own.get(f"{_URL}/{pid}/changes/file", params={"path": "README.md"}).json()
            self.assertIn("+A new line written by the assistant.", d["diff"])
            n = c.own.get(f"{_URL}/{pid}/changes/file", params={"path": "notes/new.txt"}).json()
            self.assertEqual(n["text"], "one\ntwo\n")
            self.assertTrue(c.own.get(f"{_URL}/{pid}/changes/file", params={"path": "blob.bin"}).json()["binary"])
            self.assertIn("# idea", c.own.get(f"{_URL}/{pid}/changes/file", params={"path": "docs/notes/T-0001-idea.md"}).json()["text"])
            # recent commits, with the id in the subject turned into a link, and the files of one commit
            cm = [x for x in ch["commits"] if "T-0007" in x["subject"]]
            self.assertTrue(cm and cm[0]["links"] == [{"id": "T-0007", "kind": "task"}], ch["commits"])
            self.assertEqual(c.own.get(f"{_URL}/{pid}/changes", params={"commit": cm[0]["sha"]}).json()["files"], [])   # an empty commit
            seen = [c.own.get(f"{_URL}/{pid}/changes", params={"commit": x["sha"]}).json()["files"] for x in ch["commits"] if x["repo"] == "project"]
            self.assertTrue(any(f["path"] == "README.md" for fl in seen for f in fl), "no commit lists the README it created")
            # refusals: escape, absolute, hidden git dir, unlisted file, symlink, bad commit, other tenant
            for bad in ("../etc/passwd", "/etc/passwd", ".git/config", "project.yaml", "docs/../x", "a\\b"):
                r = c.own.get(f"{_URL}/{pid}/changes/file", params={"path": bad})
                self.assertEqual(r.status_code, 400, (bad, r.text))
            (root / "link.txt").symlink_to("/etc/hostname")
            self.assertEqual(c.own.get(f"{_URL}/{pid}/changes/file", params={"path": "link.txt"}).status_code, 400)
            self.assertEqual(c.own.get(f"{_URL}/{pid}/changes", params={"commit": "zzzz"}).status_code, 400)
            self.assertEqual(c.other.get(f"{_URL}/{pid}/changes").status_code, 404)
            self.assertEqual(c.other.get(f"{_URL}/{pid}/changes/file", params={"path": "README.md"}).status_code, 404)

    def test_a_rename_is_one_entry_and_its_old_path_is_not_a_phantom_change(self):
        """Review finding (2026-11-08): `git status -z` prints a rename as `R  new\\0old\\0`; the old path must be consumed with its entry."""
        with self._ctx() as c:
            pid, root = self._new(c)
            (root / "src_old.py").write_text("print(1)\n")
            _git(root, "add", "-A")
            _git(root, "commit", "-qm", "add src_old")
            _git(root, "mv", "src_old.py", "new.py")
            files = c.own.get(f"{_URL}/{pid}/changes").json()["files"]
            self.assertEqual(sorted((f["path"], f["status"]) for f in files), [("new.py", "renamed")], files)

    def test_the_cut_is_deterministic_and_flagged(self):
        from core.projects import changes
        with self._ctx() as c:
            pid, root = self._new(c)
            for n in range(changes.MAX_FILES + 20):
                (root / f"f{n:04d}.txt").write_text("x")
            a, cut_a = changes.working_changes_ex(root, count_lines=False)
            b, cut_b = changes.working_changes_ex(root, count_lines=False)
            self.assertEqual((len(a), cut_a), (changes.MAX_FILES, True))
            self.assertEqual([f["path"] for f in a], [f["path"] for f in b])
            self.assertTrue(c.own.get(f"{_URL}/{pid}/changes").json()["truncated"])

    def test_reading_never_writes_or_locks_and_a_folder_without_git_is_explained(self):
        with self._ctx() as c:
            pid, root = self._new(c)
            _git(root, "add", "-A")
            _git(root, "commit", "--allow-empty", "-qm", "baseline")
            before = _all_hashes(root)
            for _ in range(3):
                self.assertEqual(c.own.get(f"{_URL}/{pid}/changes").status_code, 200)
            self.assertEqual(_all_hashes(root), before, "reading changed a byte (incl. .git)")
            self.assertFalse((root / ".git" / "index.lock").exists())
            plain = c.root / "no-git"
            plain.mkdir()
            (plain / "a.txt").write_text("x")
            a = c.own.post(f"{_URL}/adopt", json={"location": str(plain)}, headers=self._h(c))
            self.assertEqual(a.status_code, 201, a.text)
            r = c.own.get(f"{_URL}/no-git/changes").json()
            self.assertEqual((r["has_repository"], r["files"]), (False, []))


if __name__ == "__main__":
    unittest.main()
