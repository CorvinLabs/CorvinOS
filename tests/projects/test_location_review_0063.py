"""REVIEW-0063 m4/m5 - the location rule against a same-user writer that swaps a path component for a link.

m4: the window between the verdict (``registry.validate_location``) and the mkdir/rename of create, move and the projects root.
The swap is injected deterministically right after the verdict returns (a wrapper around ``validate_location``).
m5: grandfathering below a stored projects root must end when an ancestor of that root is re-linked after it was set.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from test_project_bundle import POSIX, BundleBase  # noqa: E402

from core.projects import registry  # noqa: E402


def _swap_for_link(d: Path, target: Path, parked: Path) -> None:
    os.rename(d, parked)
    os.symlink(target, d)


@unittest.skipUnless(POSIX, "symlinks without privileges")
class VerdictToMkdir(BundleBase):
    def setUp(self):
        super().setUp()
        self.victim = self.tmp / "victim"
        self.victim.mkdir()

    def _arm(self, purpose: str, swap):
        real = registry.validate_location
        state = {"armed": False, "fired": False}

        def wrapper(tenant_id, raw, *a, **k):
            res = real(tenant_id, raw, *a, **k)
            if state["armed"] and not state["fired"] and k.get("purpose") == purpose:
                swap()
                state["fired"] = True
            return res
        return mock.patch.object(registry, "validate_location", wrapper), state

    def _victim_untouched(self):
        self.assertEqual([p.relative_to(self.victim) for p in self.victim.rglob("*")], [])

    def test_create_refuses_when_a_component_becomes_a_link_after_the_verdict(self):
        w = self.tmp / "w"
        w.mkdir()
        target = w / "sub" / "proj"
        plan = self.svc.plan_project("_default", template="plain", title="Raced", location=str(target))
        patcher, st = self._arm("create", lambda: _swap_for_link(w, self.victim, self.tmp / "w-parked"))
        with patcher:
            st["armed"] = True
            with self.assertRaises(registry.ProjectPathError) as cm:
                self.svc.create_project("_default", plan["plan_id"])
        self.assertTrue(st["fired"])
        self.assertEqual(cm.exception.reason_class, "symlink")
        self._victim_untouched()
        self.assertIsNone(registry.get("_default", plan["project_id"]))

    def test_move_refuses_when_the_target_parent_becomes_a_link_after_the_verdict(self):
        pid, root = self.project("Mover")
        dest_parent = self.tmp / "dst"
        dest_parent.mkdir()
        dest = dest_parent / "moved"
        patcher, st = self._arm("move", lambda: _swap_for_link(dest_parent, self.victim, self.tmp / "dst-parked"))
        with patcher:
            st["armed"] = True
            with self.assertRaises(registry.ProjectPathError) as cm:
                self.svc.move_project("_default", pid, str(dest))
        self.assertTrue(st["fired"])
        self.assertEqual(cm.exception.reason_class, "symlink")
        self._victim_untouched()
        self.assertTrue((root / "project.yaml").is_file(), "the project stayed where it was")
        self.assertEqual(Path(registry.get("_default", pid)["path"]), root)

    def test_projects_root_refuses_when_a_component_becomes_a_link_after_the_verdict(self):
        a = self.tmp / "ra"
        a.mkdir()
        before = registry.settings("_default")["projects_root"]
        patcher, st = self._arm("projects_root", lambda: _swap_for_link(a, self.victim, self.tmp / "ra-parked"))
        with patcher:
            st["armed"] = True
            with self.assertRaises(registry.ProjectPathError) as cm:
                registry.set_projects_root("_default", str(a / "projects"))
        self.assertTrue(st["fired"])
        self.assertEqual(cm.exception.reason_class, "symlink")
        self._victim_untouched()
        self.assertEqual(registry.settings("_default")["projects_root"], before)

    def test_unraced_create_move_and_root_still_work_and_record_the_root_resolution(self):
        r2 = self.tmp / "r2" / "projects"
        registry.set_projects_root("_default", str(r2))
        raw = json.loads(registry.settings_path("_default").read_text())
        self.assertEqual(raw[registry.ROOT_RESOLVED_KEY], str(r2.resolve()))
        pid, root = self.project("Plain")
        self.assertEqual(root.parent, r2)
        out = self.svc.move_project("_default", pid, str(self.tmp / "else" / "deep" / "plain"))
        self.assertTrue((self.tmp / "else" / "deep" / "plain" / "project.yaml").is_file())
        self.assertTrue(out)


@unittest.skipUnless(POSIX, "symlinks without privileges")
class GrandfatheringBinding(BundleBase):
    def test_an_ancestor_relinked_after_the_root_was_set_ends_the_grandfathering(self):
        a = self.tmp / "a"
        a.mkdir()
        registry.set_projects_root("_default", str(a / "projects"))
        victim = self.tmp / "victim"
        (victim / "projects").mkdir(parents=True)
        shutil.rmtree(a)
        os.symlink(victim, a)
        with self.assertRaises(registry.ProjectPathError) as cm:
            registry.validate_location("_default", str(a / "projects" / "x"), purpose="create")
        self.assertEqual(cm.exception.reason_class, "symlink")
        self.assertIsNone(registry.grandfather_root("_default", pin=False))

    def test_a_legacy_root_is_pinned_on_first_use_and_a_later_relink_ends_it(self):
        # a root stored before the resolved form was recorded, below a linked folder (REVIEW-0062 N1's grandfathered case)
        real = self.tmp / "real"
        (real / "projects").mkdir(parents=True)
        lnk = self.tmp / "lnk"
        os.symlink(real, lnk)
        f = registry.settings_path("_default")
        raw = json.loads(f.read_text())
        raw["projects_root"] = str(lnk / "projects")
        raw.pop(registry.ROOT_RESOLVED_KEY, None)
        f.write_text(json.dumps(raw))
        self.assertEqual(registry.validate_location("_default", str(lnk / "projects" / "x"), purpose="create"),
                         real / "projects" / "x")
        self.assertEqual(json.loads(f.read_text())[registry.ROOT_RESOLVED_KEY], str(real / "projects"))
        other = self.tmp / "other"
        (other / "projects").mkdir(parents=True)
        os.unlink(lnk)
        os.symlink(other, lnk)
        with self.assertRaises(registry.ProjectPathError) as cm:
            registry.validate_location("_default", str(lnk / "projects" / "y"), purpose="create")
        self.assertEqual(cm.exception.reason_class, "symlink")


@unittest.skipUnless(POSIX, "symlinks without privileges")
class CreateWritesThroughTheMadeFolder(BundleBase):
    """REVIEW-0065 (REVIEW-0063 m4 residual): a swap AFTER make_location - between it and the first write, or after the
    project is recorded - never leaves ``project.yaml`` at the link's target, and a failed create names where the folder
    resolves now."""

    def setUp(self):
        super().setUp()
        self.outside = self.tmp / "outside"
        self.outside.mkdir()

    def _plan(self, title: str):
        w = self.tmp / ("w-" + title.lower())
        w.mkdir()
        plan = self.svc.plan_project("_default", template="plain", title=title, location=str(w / "proj"))
        return plan, w / "proj"

    def test_a_swap_between_make_location_and_the_manifest_never_writes_at_the_link_target(self):
        plan, loc = self._plan("Before")
        real = registry.make_location
        fired = []

        def hook(resolved):
            h = real(resolved)
            if Path(resolved) == loc and not fired:
                _swap_for_link(loc, self.outside, self.tmp / "parked")
                fired.append(1)
            return h
        with mock.patch.object(registry, "make_location", hook):
            with self.assertRaises(registry.ProjectPathError) as cm:
                self.svc.create_project("_default", plan["plan_id"])
        self.assertTrue(fired)
        self.assertEqual(cm.exception.reason_class, "symlink")
        self.assertEqual(sorted(p.name for p in self.outside.iterdir()), [])
        self.assertFalse((self.tmp / "parked" / "project.yaml").exists(), "the manifest was taken back in the made folder")
        self.assertIsNone(registry.get("_default", plan["project_id"]))

    def test_a_swap_after_the_project_is_recorded_names_where_the_folder_resolves(self):
        plan, loc = self._plan("After")
        real = registry.register
        fired = []

        def hook(tenant_id, pid, where, *a, **k):
            out = real(tenant_id, pid, where, *a, **k)
            if Path(where) == loc and not fired:
                _swap_for_link(loc, self.outside, self.tmp / "parked")
                fired.append(1)
            return out
        with mock.patch.object(registry, "register", hook):
            with self.assertRaises(self.svc.Conflict) as cm:
                self.svc.create_project("_default", plan["plan_id"])
        self.assertEqual(cm.exception.reason_class, "create_failed")
        self.assertIn(f"it now resolves to {self.outside}", str(cm.exception))      # the operator learns where the files went
        self.assertTrue(fired)
        self.assertIsNone(registry.get("_default", plan["project_id"]))
        self.assertFalse((self.outside / "project.yaml").exists())

    def test_an_unraced_create_writes_the_manifest_once_with_no_temp_left(self):
        plan, loc = self._plan("Plain")
        self.svc.create_project("_default", plan["plan_id"])
        self.assertTrue((loc / "project.yaml").is_file())
        self.assertEqual([p.name for p in loc.iterdir() if p.name.startswith(".project.yaml")], [])
        self.assertEqual(oct((loc / "project.yaml").stat().st_mode & 0o777), oct(0o644))     # as atomic_write leaves it


if __name__ == "__main__":
    unittest.main()
