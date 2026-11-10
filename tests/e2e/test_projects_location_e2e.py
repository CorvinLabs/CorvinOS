"""PLAN-0972 phase 1 / ADR-2321 - where projects live, over real HTTP (the console router, real sessions, real filesystem, real git).

T-0384: the managed workspace (``<data dir>/corvin/projects/w-<10 hex>/``) is the default for a fresh tenant; a tenant with a
stored projects root keeps it; existing entries read ``own`` and are never touched; the workspace marker's binding rules
(use / rebound / foreign / recovered); one settings writer; rebuild over both roots; no path in the audit chain.

Every test runs with HOME and XDG_DATA_HOME inside its own temp dir (``_projects_fixtures.sandbox_home_env``), so the real
workspace is never reached (the repo conftest's tripwire would fail the test).
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "core" / "console" / "tests"))
from _projects_fixtures import sandbox_home_env, small_catalogue, write_template  # noqa: E402
from test_admin_route import _audit_events, _sandbox  # noqa: E402

_URL = "/v1/console/projects"
_KEY = re.compile(r"w-[0-9a-f]{10}")


def _tree(root: Path) -> dict[str, str]:
    out = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if p.is_symlink():
            out[rel] = "link:" + os.readlink(p)
        elif p.is_file():
            out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
        else:
            out[rel] = "dir"
    return out


def _plant_project(d: Path, pid: str, tenant: str = "_default") -> None:
    from core.projects import manifest
    d.mkdir(parents=True, exist_ok=True)
    manifest.write(d, {"schema": manifest.SCHEMA, "id": pid, "title": pid.title(), "tenant": tenant,
                       "template": "adopted@1", "docs_mode": "plain", "features": {}})


class LocationBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.src = small_catalogue(self.tmp / "src")
        write_template(self.src, "plain", ["core"])
        self._env = sandbox_home_env(self.tmp)
        self._env.__enter__()

    def tearDown(self):
        self._env.__exit__(None, None, None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _ctx(self, sub: str = "s"):
        outer = self

        class Ctx:
            pass

        @contextlib.contextmanager
        def cm():
            with _sandbox(outer.tmp / sub, tenants=("_default", "acme")) as (_c, _t, home, clients):
                from corvin_console import projects_service as svc
                with mock.patch.object(svc, "template_source", lambda tenant: outer.src), \
                        mock.patch.object(svc, "plugin_state", lambda tenant, pid: "enabled"):
                    c = Ctx()
                    c.home, c.svc = home, svc
                    c.own, c.csrf = clients["_default"]
                    c.other, c.other_csrf = clients["acme"]
                    c.h = {"X-CSRF-Token": c.csrf}
                    yield c
        return cm()

    def _plan(self, c, title, **extra):
        return c.own.post(f"{_URL}/plan", json={"template": "plain", "title": title, **extra}, headers=c.h)

    def _create(self, c, title, **extra):
        p = self._plan(c, title, **extra)
        self.assertEqual(p.status_code, 200, p.text)
        r = c.own.post(_URL, json={"plan_id": p.json()["plan_id"]}, headers=c.h)
        return p.json(), r

    def _settings_file(self, c, tenant="_default") -> Path:
        return c.home / "tenants" / tenant / "projects" / "settings.json"

    def _chain_has_no_path(self, c):
        for t in ("_default", "acme"):
            chain = c.home / "tenants" / t / "global" / "forge" / "audit.jsonl"
            if chain.exists():
                text = chain.read_text(encoding="utf-8")
                self.assertNotIn(str(self.tmp), text, "an audit record carries a path")
                self.assertIsNone(_KEY.search(text), "an audit record carries a workspace key")

    def _events(self, c, name):
        return [e for e in _audit_events(c.home) if (e.get("event_type") or e.get("event")) == name]


class ManagedWorkspace(LocationBase):
    # AC1, AC4b
    def test_a_fresh_tenant_creates_in_the_managed_workspace(self):
        with self._ctx() as c:
            lst = c.own.get(_URL).json()
            self.assertEqual(lst["default_storage"], "managed")
            mr = Path(lst["managed_root"])
            parent = self.tmp / "data" / "corvin" / "projects"
            self.assertEqual(mr.parent, parent)
            self.assertRegex(mr.name, r"^w-[0-9a-f]{10}$")
            self.assertNotIn("_default", str(mr))
            if os.name != "nt":
                self.assertEqual(oct(parent.stat().st_mode & 0o777), "0o700")
                self.assertEqual(oct(mr.stat().st_mode & 0o777), "0o700")
            plan, r = self._create(c, "Managed one")
            self.assertEqual(plan["location"], str(mr / "managed-one"))
            self.assertEqual(plan["storage"], "managed")
            self.assertEqual(r.status_code, 201, r.text)
            self.assertTrue((mr / "managed-one" / "project.yaml").is_file())
            marker = json.loads((mr / "workspace.json").read_text())
            self.assertEqual(marker, {"schema": 1, "tenant": "_default", "workspace_key": mr.name,
                                      "corvin_home": str(c.home.resolve())})
            got = [p for p in c.own.get(_URL).json()["projects"] if p["id"] == "managed-one"][0]
            self.assertEqual(got["storage"], "managed")
            self.assertEqual(got["display_path"], "Corvin workspace › managed-one")
            st = json.loads(self._settings_file(c).read_text())
            self.assertEqual((st["schema"], st["default_storage"], st["workspace_key"], st["managed_root"]),
                             (2, "managed", mr.name, str(mr)))
            other = c.other.get(_URL).json()
            self.assertNotEqual(other["managed_root"], str(mr), "another tenant has its own workspace")
            self.assertEqual([(e.get("details") or {}).get("storage") for e in self._events(c, "project.created")], ["managed", "managed"])
            self._chain_has_no_path(c)

    # AC2
    def test_a_tenant_with_a_stored_root_keeps_creating_under_it(self):
        with self._ctx() as c:
            root = self.tmp / "my-projects"
            root.mkdir()
            f = self._settings_file(c)
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps({"projects_root": str(root)}))               # schema 1, as written before ADR-2321
            lst = c.own.get(_URL).json()
            self.assertEqual(lst["default_storage"], "root")
            plan, r = self._create(c, "Rooted")
            self.assertEqual(r.status_code, 201, r.text)
            self.assertEqual(plan["location"], str(root / "rooted"))
            got = [p for p in c.own.get(_URL).json()["projects"] if p["id"] == "rooted"][0]
            self.assertEqual(got["storage"], "own")
            self.assertEqual(json.loads(f.read_text())["default_storage"], "root")

    # AC3
    def test_an_existing_home_projects_entry_reads_own_and_stays_byte_identical(self):
        with self._ctx() as c:
            from core.projects import registry
            legacy = self.tmp / "projects" / "x"                                  # ~/projects/x of a pre-ADR-2321 install
            _plant_project(legacy, "x")
            (legacy / "notes.txt").write_text("mine\n")
            reg = c.home / "tenants" / "_default" / "projects" / "registry.json"
            reg.parent.mkdir(parents=True, exist_ok=True)
            reg.write_text(json.dumps({"schema": 1, "projects": [{"id": "x", "path": str(legacy), "template": "adopted@1",
                                                                  "added_at": "2026-11-01T00:00:00Z"}]}))
            before = _tree(legacy)
            got = [p for p in c.own.get(_URL).json()["projects"] if p["id"] == "x"][0]
            self.assertEqual((got["state"], got["storage"]), ("ok", "own"))
            self.assertEqual(got["display_path"], "~/projects/x")
            registry.rebuild("_default")
            registry.settings("_default")
            self.assertEqual(_tree(legacy), before)

    # AC4 (c): a copy of another installation
    def test_a_marker_of_a_home_that_still_exists_refuses_the_create(self):
        with self._ctx() as c:
            mr = Path(c.own.get(_URL).json()["managed_root"])
            other_home = self.tmp / "other-install" / ".corvin"
            other_home.mkdir(parents=True)
            m = json.loads((mr / "workspace.json").read_text())
            (mr / "workspace.json").write_text(json.dumps({**m, "corvin_home": str(other_home)}))
            before = _tree(mr)
            r = self._plan(c, "Should not")
            self.assertEqual(r.status_code, 422, r.text)
            self.assertEqual(r.json()["detail"], {"message": "This workspace belongs to another Corvin installation.",
                                                  "reason_class": "workspace_foreign"})
            self.assertEqual(_tree(mr), before, "nothing was written")
            self.assertEqual(c.own.get(_URL).json()["workspace"], "foreign")
            self.assertIn("workspace_foreign", [(e.get("details") or {}).get("reason_class")
                                                for e in self._events(c, "project.blocked")])
            # (d) another tenant's marker
            (mr / "workspace.json").write_text(json.dumps({**m, "tenant": "acme"}))
            self.assertEqual(self._plan(c, "Should not").json()["detail"]["reason_class"], "workspace_foreign")

    # AC4 (b): the home was moved
    def test_a_moved_home_rebinds_the_workspace(self):
        with self._ctx("s1") as c:
            mr = Path(c.own.get(_URL).json()["managed_root"])
            old_home = c.home
            saved = self._settings_file(c).read_text()
        moved = self.tmp / "moved-home"
        os.rename(old_home, moved)                                                # CORVIN_HOME moved with its checkout
        with self._ctx("s2") as c:
            f = self._settings_file(c)
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(saved)
            plan, r = self._create(c, "After the move")
            self.assertEqual(r.status_code, 201, r.text)
            self.assertEqual(Path(plan["location"]).parent, mr)
            self.assertEqual(json.loads((mr / "workspace.json").read_text())["corvin_home"], str(c.home.resolve()))
            ev = self._events(c, "project.storage_changed")
            self.assertEqual([(e.get("details") or {}).get("outcome") for e in ev], ["rebound"])
            self._chain_has_no_path(c)

    # AC4 (e): settings.json lost
    def test_a_lost_settings_file_recovers_the_key_from_the_marker(self):
        with self._ctx() as c:
            mr = Path(c.own.get(_URL).json()["managed_root"])
            self._settings_file(c).unlink()
            self.assertEqual(c.own.get(_URL).json()["managed_root"], str(mr))
            self.assertEqual(json.loads(self._settings_file(c).read_text())["workspace_key"], mr.name)
            ev = self._events(c, "project.storage_changed")
            self.assertEqual([(e.get("details") or {}).get("outcome") for e in ev], ["recovered"])

    # AC4a: one settings writer
    def test_put_root_keeps_the_workspace_settings(self):
        with self._ctx() as c:
            c.own.get(_URL)
            before = json.loads(self._settings_file(c).read_text())
            new = self.tmp / "work" / "projects"
            r = c.own.put(f"{_URL}/root", json={"path": str(new)}, headers=c.h)
            self.assertEqual(r.status_code, 200, r.text)
            after = json.loads(self._settings_file(c).read_text())
            for k in ("default_storage", "managed_root", "workspace_key"):
                self.assertEqual(after.get(k), before[k], k)
            self.assertEqual(after["projects_root"], str(new))

    # AC5
    def test_rebuild_finds_both_roots(self):
        with self._ctx() as c:
            from core.projects import registry
            mr = Path(c.own.get(_URL).json()["managed_root"])
            root = self.tmp / "work" / "projects"
            self.assertEqual(c.own.put(f"{_URL}/root", json={"path": str(root)}, headers=c.h).status_code, 200)
            _plant_project(mr / "in-managed", "in-managed")
            _plant_project(root / "in-root", "in-root")
            _plant_project(self.tmp / "elsewhere" / "far", "far")
            res = registry.rebuild("_default")
            self.assertEqual(sorted(res["added"]), ["in-managed", "in-root"])
            by_id = {p["id"]: p for p in c.own.get(_URL).json()["projects"]}
            self.assertEqual(by_id["in-managed"]["storage"], "managed")
            self.assertEqual(by_id["in-root"]["storage"], "own")
            self.assertNotIn("far", by_id, "an own folder elsewhere is re-found only by adopt")


class PlatformFixtures(LocationBase):
    """AC7: the managed_root string of GET /projects per platform (macOS on a POSIX host through the real filesystem)."""

    def _root_for(self, platform: str, env: dict[str, str | None]):
        with mock.patch.object(sys, "platform", platform), mock.patch.dict(os.environ, {}, clear=False):
            for k, v in env.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
            with self._ctx(f"s-{platform}-{len(env)}") as c:
                return Path(c.own.get(_URL).json()["managed_root"])

    def test_linux_with_xdg(self):
        self.assertEqual(self._root_for("linux", {}).parent, self.tmp / "data" / "corvin" / "projects")

    def test_linux_without_xdg(self):
        self.assertEqual(self._root_for("linux", {"XDG_DATA_HOME": None}).parent,
                         self.tmp / ".local" / "share" / "corvin" / "projects")

    @unittest.skipIf(os.name == "nt", "darwin path flavour on a POSIX host")
    def test_darwin(self):
        self.assertEqual(self._root_for("darwin", {}).parent,
                         self.tmp / "Library" / "Application Support" / "Corvin" / "projects")


if __name__ == "__main__":
    unittest.main()
