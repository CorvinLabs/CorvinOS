"""T-0362 / ADR-2315 - Doctor calibration, Adopt of a foreign repository, Rename and Move, over real HTTP.

Doctor: a clean project passes every check (false-positive control); every seeded defect is found by
the check that names it (100 % of the seeded list - the plan's acceptance criterion).
Adopt: a copy of the real ``LLM_lie_detector`` repository gains exactly one file, ``project.yaml``;
every other byte, ``.git`` included, is unchanged.
Rename changes only the title. Move relocates the directory and the index together and refuses
every unsafe target.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import test_projects_api_e2e as base  # noqa: E402

_URL = base._URL
_LIE = Path.home() / "projects" / "LLM_lie_detector"


def _all_hashes(root: Path) -> dict[str, str]:
    """Every file, ``.git`` included - an adopt must not even touch the git directory."""
    out: dict[str, str] = {}
    for dp, _dn, files in os.walk(root):
        for f in files:
            p = Path(dp) / f
            if p.is_symlink():
                out[p.relative_to(root).as_posix()] = "symlink:" + os.readlink(p)
            else:
                out[p.relative_to(root).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


class _Base(unittest.TestCase):
    setUp = base.ProjectsApiE2E.setUp
    tearDown = base.ProjectsApiE2E.tearDown
    _ctx = base.ProjectsApiE2E._ctx
    _create = base.ProjectsApiE2E._create

    def _h(self, c):
        return {"X-CSRF-Token": c.own_csrf}

    def _new(self, c, title="Doctor subject"):
        plan, r = self._create(c, title, features=["core", "kb", "claude", "tasks"])
        self.assertEqual(r.status_code, 201, r.text)
        pid = plan["project_id"]
        return pid, Path(c.own.get(f"{_URL}/{pid}").json()["path"])

    def _failed(self, c, pid) -> set[str]:
        h = c.own.get(f"{_URL}/{pid}/health").json()
        return {x["id"] for x in h["checks"] if not x["ok"]}


@unittest.skipUnless(base._MARKET.is_file(), "needs the Marketplace checkout")
class DoctorCalibration(_Base):
    def test_a_clean_project_has_no_failed_check_and_every_seeded_defect_is_found(self):
        with self._ctx() as c:
            pid, root = self._new(c)
            self.assertEqual(self._failed(c, pid), set(), "false positive on a clean project")
            backup = self.tmp / "clean-backup"
            shutil.copytree(root, backup, symlinks=True)

            def restore():
                if root.exists():
                    shutil.rmtree(root)
                shutil.copytree(backup, root, symlinks=True)

            def edit_yaml(fn):
                import yaml
                p = root / "project.yaml"
                d = yaml.safe_load(p.read_text())
                fn(d)
                p.write_text(yaml.safe_dump(d))

            def gone():
                os.rename(root, self.tmp / "moved-away")

            def comeback():
                if (self.tmp / "moved-away").exists():
                    os.rename(self.tmp / "moved-away", root)

            def bad_kb():
                (root / "docs" / "kb" / "tasks" / "T-9999-bad.md").write_text(
                    "---\nid: T-9999\ntitle: bad\nstatus: bogus\n---\nx\n")

            seeded = [
                ("directory_missing", gone, "path_exists"),
                ("manifest_unparseable", lambda: (root / "project.yaml").write_text("id: [\n"), "manifest"),
                ("manifest_wrong_id", lambda: edit_yaml(lambda d: d.update(id="other-id")), "manifest"),
                ("manifest_foreign_tenant", lambda: edit_yaml(lambda d: d.update(tenant="acme")), "manifest"),
                ("feature_requirement_off", lambda: edit_yaml(lambda d: d["features"].update(kb=False)), "features_closed"),
                ("generated_section_edited", lambda: (root / "CLAUDE.md").write_text(
                    (root / "CLAUDE.md").read_text().replace("## Core", "## Core (hand edited)", 1)),
                 "generated_files_in_sync"),
                ("kb_removed", lambda: shutil.rmtree(root / "docs" / "kb"), "kb_check"),
                ("kb_inconsistent", bad_kb, "kb_check"),
            ]
            found, missed = [], []
            for name, seed, expect in seeded:
                try:
                    seed()
                    failed = self._failed(c, pid)
                finally:
                    comeback()
                    restore()
                (found if expect in failed else missed).append(name)
                self.assertEqual(self._failed(c, pid), set(), f"restore after {name} left a failed check")
            self.plugins["corvin_knowledge"] = "absent"                      # a tenant plugin the features need
            (found if "tenant_plugins" in self._failed(c, pid) else missed).append("plugin_missing")
            self.plugins["corvin_knowledge"] = "enabled"
            self.assertEqual(missed, [], f"defects the doctor did not find: {missed}; found: {found}")
            self.assertEqual(len(found), len(seeded) + 1)


@unittest.skipUnless(base._MARKET.is_file(), "needs the Marketplace checkout")
class AdoptRenameMove(_Base):
    def test_adopt_a_foreign_repo_writes_exactly_one_file(self):
        with self._ctx() as c:
            dst = c.root / "plain-repo"
            (dst / "src").mkdir(parents=True)
            (dst / "src" / "a.py").write_text("print(1)\n")
            (dst / "README.md").write_text("# mine\n")
            base.fx._sh("git", "-C", dst, "init", "-q")
            base.fx._sh("git", "-C", dst, "add", "-A")
            base.fx._sh("git", "-C", dst, "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "init")
            before = _all_hashes(dst)
            r = c.own.post(f"{_URL}/adopt", json={"location": str(dst)}, headers=self._h(c))
            self.assertEqual(r.status_code, 201, r.text)
            after = _all_hashes(dst)
            self.assertEqual(set(after) - set(before), {"project.yaml"})
            self.assertEqual({k for k in before if before[k] != after[k]}, set(), "an existing byte changed")
            self.assertEqual(r.json()["state"], "ok")
            self.assertEqual(r.json()["id"], "plain-repo")
            self.assertEqual(base.fx._sh("git", "-C", dst, "status", "--porcelain").strip(), "?? project.yaml")
            again = c.own.post(f"{_URL}/adopt", json={"location": str(dst)}, headers=self._h(c))
            self.assertEqual(again.status_code, 409, "adopting twice must not write a second manifest")

    @unittest.skipUnless(_LIE.is_dir(), "needs the LLM_lie_detector checkout")
    def test_adopt_a_copy_of_the_real_lie_detector_repo(self):
        with self._ctx() as c:
            dst = c.root / "lie-copy"
            shutil.copytree(_LIE, dst, symlinks=True, ignore=shutil.ignore_patterns("runs", ".pytest_cache", "__pycache__", ".venv"))
            before = _all_hashes(dst)
            r = c.own.post(f"{_URL}/adopt", json={"location": str(dst), "title": "Lie detector"}, headers=self._h(c))
            self.assertEqual(r.status_code, 201, r.text)
            after = _all_hashes(dst)
            self.assertEqual(set(after) - set(before), {"project.yaml"})
            self.assertEqual({k for k in before if before[k] != after[k]}, set())
            self.assertEqual(set(before) - set(after), set())
            got = c.own.get(f"{_URL}/lie-copy").json()
            self.assertEqual((got["state"], got["title"]), ("ok", "Lie detector"))
            self.assertEqual(sorted(got["active"]), ["kb"] if (dst / "docs" / "kb" / "_meta").is_dir() else [])

    def test_adopt_refuses_outside_the_root_and_a_foreign_manifest(self):
        with self._ctx() as c:
            outside = self.tmp / "elsewhere"
            outside.mkdir()
            r = c.own.post(f"{_URL}/adopt", json={"location": str(outside)}, headers=self._h(c))
            self.assertEqual(r.status_code, 422)
            self.assertEqual(os.listdir(outside), [])
            r = c.own.post(f"{_URL}/adopt", json={"location": str(c.root / "nope")}, headers=self._h(c))
            self.assertEqual(r.status_code, 422)

    def test_rename_changes_only_the_title(self):
        with self._ctx() as c:
            pid, root = self._new(c)
            before = _all_hashes(root)
            r = c.own.put(f"{_URL}/{pid}/title", json={"title": "A better name"}, headers=self._h(c))
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["id"], pid)
            self.assertEqual(r.json()["title"], "A better name")
            after = _all_hashes(root)
            self.assertEqual({k for k in after if before.get(k) != after[k]}, {"project.yaml"})
            self.assertEqual(c.own.put(f"{_URL}/{pid}/title", json={"title": "  "}, headers=self._h(c)).status_code, 400)
            self.assertEqual(c.own.put(f"{_URL}/{pid}/title", json={"title": "x"}).status_code in (401, 403), True)
            self.assertEqual(c.other.put(f"{_URL}/{pid}/title", json={"title": "x"}, headers={"X-CSRF-Token": c.other_csrf}).status_code, 404)

    def test_move_follows_in_the_index_and_refuses_unsafe_targets(self):
        with self._ctx() as c:
            pid, root = self._new(c)
            before = _all_hashes(root)
            target = c.root / "relocated"
            r = c.own.post(f"{_URL}/{pid}/move", json={"location": str(target)}, headers=self._h(c))
            self.assertEqual(r.status_code, 200, r.text)
            self.assertFalse(root.exists())
            self.assertEqual(Path(r.json()["path"]), target.resolve())
            moved = _all_hashes(target)
            local = lambda k: k.startswith(".corvin/local/") or k == ".mcp.json"   # noqa: E731 - regenerated, hold absolute paths
            self.assertEqual({k: v for k, v in moved.items() if not local(k)},
                             {k: v for k, v in before.items() if not local(k)}, "a user byte changed by the move")
            changed_local = {k for k in moved if local(k) and moved[k] != before.get(k)}
            self.assertTrue(changed_local, "the machine-local files were not regenerated")
            for k in changed_local:
                self.assertNotIn(str(root), (target / k).read_text(), k)
            self.assertIsInstance(r.json()["repaired_files"], int)
            self.assertEqual(c.own.get(f"{_URL}/{pid}").json()["state"], "ok")
            hh = c.own.get(f"{_URL}/{pid}/health").json()
            self.assertEqual(self._failed(c, pid), set(), hh)
            for loc, want in ((str(target / "inner"), 409), (str(self.tmp / "out" / "p"), 422),
                              (str(c.root / "a" / ".." / "b"), 422), (str(c.home / "tenants" / "_default" / "p"), 422)):
                rr = c.own.post(f"{_URL}/{pid}/move", json={"location": loc}, headers=self._h(c))
                self.assertEqual(rr.status_code, want, (loc, rr.text))
                self.assertTrue(target.is_dir())
            busy = c.root / "busy"
            busy.mkdir()
            (busy / "keep.txt").write_text("mine")
            rr = c.own.post(f"{_URL}/{pid}/move", json={"location": str(busy)}, headers=self._h(c))
            self.assertEqual(rr.status_code, 422, rr.text)                       # create rules: not empty
            self.assertEqual((busy / "keep.txt").read_text(), "mine")
            self.assertEqual(c.other.post(f"{_URL}/{pid}/move", json={"location": str(c.root / "z")},
                                          headers={"X-CSRF-Token": c.other_csrf}).status_code, 404)

    def test_the_audit_records_for_adopt_rename_and_move_are_not_dropped(self):
        with self._ctx() as c:
            pid, root = self._new(c)
            c.own.put(f"{_URL}/{pid}/title", json={"title": "T2"}, headers=self._h(c))
            c.own.post(f"{_URL}/{pid}/move", json={"location": str(c.root / "m2")}, headers=self._h(c))
            ev = base._audit_events(c.home)
            names = [e.get("event") or e.get("event_type") for e in ev]
            self.assertIn("project.renamed", names)
            self.assertIn("project.moved", names)
            for e in ev:
                if str(e.get("event") or e.get("event_type")) in ("project.renamed", "project.moved"):
                    self.assertNotIn("_dropped_fields", e.get("details", e))


if __name__ == "__main__":
    unittest.main()
