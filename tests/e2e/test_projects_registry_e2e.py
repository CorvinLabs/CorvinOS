"""T-0351 / ADR-2315 Decisions 1 and 6 - the project registry and the rules for client-supplied paths.

Real filesystem, sandboxed CORVIN_HOME. The HTTP surface of the same rules is proved in
``test_projects_api_e2e.py``; here the module's own invariants:

* the registry file is 0o600 in a 0o700 directory under ``tenant_home()``;
* a location outside the projects root, with ``..``, inside CORVIN_HOME, below a symlink, with a
  protected component, or already owned by another tenant is refused (nothing is created);
* the same directory cannot be registered twice or by a second tenant;
* losing the registry loses no project: ``rebuild`` restores the index from the directories;
* a manifest that is invalid, of another tenant, or a symlink is never adopted.
"""
from __future__ import annotations

import json
import os
import stat
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _projects_fixtures import Sandbox  # noqa: E402

from core.projects import manifest, registry  # noqa: E402


def _project(root: Path, pid: str, tenant: str = "_default", **extra) -> Path:
    d = root / pid
    d.mkdir(parents=True)
    manifest.write(d, {"schema": 1, "id": pid, "title": pid, "tenant": tenant, "template": "demo@1",
                       "docs_mode": "nested", "features": {"core": True}, **extra})
    return d


class RegistryE2E(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.sb.set_root()

    def tearDown(self):
        self.sb.close()

    def test_file_and_directory_modes(self):
        d = _project(self.sb.root, "alpha")
        registry.register("_default", "alpha", d, "demo@1")
        p = registry.registry_path("_default")
        self.assertEqual(p.parent, self.sb.home / "tenants" / "_default" / "projects")
        self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(p.parent.stat().st_mode), 0o700)
        data = json.loads(p.read_text(encoding="utf-8"))
        self.assertEqual(data["projects"][0]["id"], "alpha")
        self.assertEqual(data["projects"][0]["path"], str(d.resolve()))

    def test_locations_that_must_be_refused(self):
        sb = self.sb
        (sb.tmp / "outside").mkdir()
        (sb.root / "real").mkdir()
        (sb.root / "link").symlink_to(sb.root / "real", target_is_directory=True)
        full = sb.root / "full"
        full.mkdir()
        (full / "x.txt").write_text("x")
        cases = {
            "outside the root": str(sb.tmp / "outside" / "p"),
            "the root itself": str(sb.root),
            "dotdot": str(sb.root / "a" / ".." / "b"),
            "relative": "projects/p",
            "inside CORVIN_HOME": str(sb.home / "tenants" / "_default" / "p"),
            "the home itself": str(sb.home),
            "below a symlink": str(sb.root / "link" / "p"),
            "the symlink itself": str(sb.root / "link"),
            "protected component": str(sb.root / "p" / ".git" / "x"),
            "hidden top level": str(sb.root / ".hidden"),
            "not empty": str(full),
            "nul byte": str(sb.root / "a\x00b"),
            "empty": "",
        }
        for label, raw in cases.items():
            with self.assertRaises(registry.ProjectPathError, msg=label):
                registry.validate_location("_default", raw)
        # nothing was created by any refusal
        self.assertEqual(sorted(p.name for p in sb.root.iterdir()), ["full", "link", "real"])
        # positive controls: a fresh child and an existing empty directory are fine
        self.assertEqual(registry.validate_location("_default", str(sb.root / "fresh")), (sb.root / "fresh").resolve())
        (sb.root / "emptydir").mkdir()
        registry.validate_location("_default", str(sb.root / "emptydir"))

    def test_second_tenant_cannot_claim_a_registered_directory(self):
        d = _project(self.sb.root, "alpha")
        registry.register("_default", "alpha", d, "demo@1")
        self.sb.set_root("acme")
        with self.assertRaises(registry.ProjectPathError):
            registry.validate_location("acme", str(d))
        with self.assertRaises(registry.ProjectPathError):
            registry.validate_location("acme", str(d), adopt=True)
        # a manifest naming another tenant is not adoptable either
        other = _project(self.sb.root, "beta", tenant="acme")
        with self.assertRaises(registry.ProjectPathError):
            registry.validate_location("_default", str(other), adopt=True)
        registry.validate_location("acme", str(other), adopt=True)   # positive control

    def test_duplicates_are_refused(self):
        d = _project(self.sb.root, "alpha")
        registry.register("_default", "alpha", d, "demo@1")
        e = _project(self.sb.root, "beta")
        with self.assertRaises(registry.RegistryError):
            registry.register("_default", "alpha", e, "demo@1")        # same id
        with self.assertRaises(registry.RegistryError):
            registry.register("_default", "gamma", d, "demo@1")        # same directory
        with self.assertRaises(registry.ProjectPathError):
            registry.register("_default", "Bad_ID", e, None)

    def test_losing_the_registry_loses_no_project(self):
        a, b = _project(self.sb.root, "alpha"), _project(self.sb.root, "beta")
        _project(self.sb.root, "foreign", tenant="acme")
        for pid, d in (("alpha", a), ("beta", b)):
            registry.register("_default", pid, d, "demo@1")
        registry.registry_path("_default").unlink()
        self.assertEqual(registry.entries("_default"), [])
        res = registry.rebuild("_default")
        self.assertEqual(sorted(res["added"]), ["alpha", "beta"])
        self.assertEqual(res["skipped"], ["foreign"])
        self.assertEqual(sorted(e["id"] for e in registry.entries("_default")), ["alpha", "beta"])
        self.assertEqual(registry.rebuild("_default")["added"], [])          # idempotent
        # directories untouched
        self.assertTrue((a / "project.yaml").is_file() and (b / "project.yaml").is_file())

    def test_invalid_or_symlinked_manifests_are_never_adopted(self):
        bad = self.sb.root / "bad"
        bad.mkdir()
        (bad / "project.yaml").write_text("schema: 1\nid: BAD ID\ntenant: _default\n", encoding="utf-8")
        with self.assertRaises(registry.ProjectPathError):
            registry.validate_location("_default", str(bad), adopt=True)
        sym = self.sb.root / "sym"
        sym.mkdir()
        real = self.sb.tmp / "elsewhere.yaml"
        real.write_text("schema: 1\nid: sym\ntenant: _default\n", encoding="utf-8")
        (sym / "project.yaml").symlink_to(real)
        with self.assertRaises(registry.ProjectPathError):
            registry.validate_location("_default", str(sym), adopt=True)
        self.assertEqual(registry.rebuild("_default")["added"], [])

    def test_an_unreadable_registry_is_an_error_not_an_empty_list(self):
        p = registry.registry_path("_default")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{not json", encoding="utf-8")
        with self.assertRaises(registry.RegistryError):
            registry.entries("_default")


if __name__ == "__main__":
    unittest.main()
