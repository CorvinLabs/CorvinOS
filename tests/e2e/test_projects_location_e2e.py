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
            for k in ("managed_root", "workspace_key"):
                self.assertEqual(after.get(k), before[k], k)
            # the alias of set_default_storage("root", ...) (T-0408): the key stays, its value is now root
            self.assertEqual((before["default_storage"], after["default_storage"]), ("managed", "root"))
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


def _verify_chain(tc, c):
    from forge import security_events as se
    for t in ("_default", "acme"):
        chain = c.home / "tenants" / t / "global" / "forge" / "audit.jsonl"
        if chain.exists():
            ok, problems = se.verify_chain(chain)
            tc.assertTrue(ok, problems[:3])


def _old_registry():
    """``core/projects/registry.py`` as it was at 65b587f2e, loaded beside the current one (Decision 10a table)."""
    import importlib.util
    import subprocess
    src = subprocess.run(["git", "-C", str(HERE.parents[1]), "show", "65b587f2e:core/projects/registry.py"],
                         capture_output=True, text=True, check=True).stdout
    spec = importlib.util.spec_from_loader("core.projects._registry_65b587f2e", loader=None)
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = "core.projects"
    exec(compile(src, "registry@65b587f2e", "exec"), mod.__dict__)
    return mod


class LocationRule(LocationBase):
    """T-0385 / ADR-2321 Decisions 4, 10, 10a, 15: the one location rule over POST /plan and POST /adopt."""

    def _root(self, c, path=None):
        root = path or self.tmp / "work" / "projects"
        r = c.own.put(f"{_URL}/root", json={"path": str(root)}, headers=c.h)
        self.assertEqual(r.status_code, 200, r.text)
        return root

    def _refused(self, c, how: str, loc: str, cls: str):
        from core.projects import registry
        if how == "plan":
            r = self._plan(c, "Rule probe", location=loc, project_id="rule-probe")
            purpose = "create"
        else:
            r = c.own.post(f"{_URL}/adopt", json={"location": loc}, headers=c.h)
            purpose = "adopt"
        self.assertEqual(r.status_code, 422, (loc, r.text))
        d = r.json()["detail"]
        self.assertEqual(d["reason_class"], cls, (loc, d))
        if cls in registry.SENTENCES and cls not in ("path_rule", "other_tenant", "wrong_manifest", "symlink", "nested"):
            self.assertEqual(d["message"], registry.SENTENCES[cls], loc)
        self.assertNotIn(str(self.tmp), json.dumps(d), "the refusal leaks a path")
        # AC4: the picker's verdict (the same function, in-process) answers the same class
        with self.assertRaises(registry.ProjectPathError) as cm:
            registry.validate_location("_default", loc, purpose=purpose)
        self.assertEqual(cm.exception.reason_class, cls, loc)

    # AC1
    def test_adopt_outside_the_projects_root_writes_only_project_yaml(self):
        with self._ctx() as c:
            self._root(c)
            repo = self.tmp / "code" / "my-repo"
            (repo / "src").mkdir(parents=True)
            (repo / "src" / "a.py").write_text("print(1)\n")
            before = _tree(repo)
            r = c.own.post(f"{_URL}/adopt", json={"location": str(repo)}, headers=c.h)
            self.assertEqual(r.status_code, 201, r.text)
            after = _tree(repo)
            self.assertEqual(set(after) - set(before), {"project.yaml"})
            self.assertEqual({k for k in before if before[k] != after[k]}, set())
            self.assertEqual(r.json()["storage"], "own")
            _verify_chain(self, c)

    # AC2: one planted case per reason class, REVIEW-0061 B1 and REVIEW-0062 cases included
    def test_every_reason_class_answers_422_with_its_sentence(self):
        with self._ctx() as c:
            from core.paths import user_dirs
            root = self._root(c)
            t = self.tmp
            mr = Path(c.own.get(_URL).json()["managed_root"])
            (t / "real" / "a").mkdir(parents=True)
            (t / "lnk").symlink_to(t / "real", target_is_directory=True)
            full = t / "full"
            full.mkdir()
            (full / "x").write_text("x")
            plan, r = self._create(c, "Outer one", location=str(t / "code" / "outer"))
            self.assertEqual(r.status_code, 201, r.text)
            stray = t / "stray"
            _plant_project(stray, "stray")                                   # a project.yaml no registry lists
            foreign = t / "foreign"
            _plant_project(foreign, "foreign", tenant="acme")
            other_home = next((p for p in (Path("/home"), Path("/Users")) if p.is_dir() and any(p.iterdir())), None)
            repo = HERE.parents[1]
            cases = [
                ("plan", "relative/p", "path_rule"),
                ("plan", str(t / "a" / ".." / "b"), "path_rule"),
                ("plan", str(c.home / "tenants" / "_default" / "newproj"), "corvin_home"),
                ("plan", str(t / ".corvin" / "tenants" / "_default" / "newproj"), "corvin_home"),   # the real ~/.corvin
                ("plan", str(c.home), "corvin_home"),
                ("plan", str(repo / ".git" / "hooks" / "newproj"), "protected"),
                ("plan", str(t / "x" / "session_ledger" / "p"), "protected"),
                ("plan", str(t / ".ssh" / "newproj"), "hidden"),
                ("plan", str(t / ".config" / "systemd" / "user" / "newproj"), "hidden"),
                ("plan", str(t / ".local" / "share" / "x"), "hidden"),
                ("plan", str(t / "lnk" / "a" / "newproj"), "symlink"),
                ("plan", str(t), "top_level"),                                  # the own home
                ("plan", "/", "top_level"),
                ("plan", str(t.parent), "top_level"),                           # an ancestor of the own home
                ("plan", "/tmp/corvin-rule-probe-x", "system_prefix"),
                ("plan", "/etc/newproj", "system_prefix"),
                ("plan", "/usr/local/newproj", "system_prefix"),
                ("plan", str(mr.parent), "other_tenant"),                       # the managed parent
                ("plan", str(mr.parent / "w-0000000000" / "newproj"), "other_tenant"),
                ("plan", str(mr), "workspace_root"),
                ("plan", str(mr / ".trash" / "x"), "workspace_root"),
                ("plan", str(t / "code" / "outer" / "inner"), "nested"),
                ("plan", str(stray / "inner"), "nested"),
                ("plan", str(root), "nested"),                                  # the projects folder itself
                ("plan", str(full), "not_empty"),
                ("adopt", str(t / "nope"), "missing"),
                ("adopt", str(foreign), "other_tenant"),
                ("adopt", str(t / ".ssh"), "hidden"),
            ]
            (t / ".ssh").mkdir()
            if other_home is not None:
                someone = sorted(p for p in other_home.iterdir() if p.is_dir())[0]
                cases += [("plan", str(someone), "top_level"), ("plan", str(someone / "code" / "newproj"), "protected")]
            for how, loc, cls in cases:
                self._refused(c, how, loc, cls)
            # removable media (REVIEW-0062 N2): below /run/media/<own user>/<label> passes check 6; the mount point is top_level
            me = user_dirs._own_user()
            stick = Path("/run/media") / me / "Stick"
            with mock.patch.object(user_dirs, "drives", lambda: [{"kind": "drive", "label": "Stick", "path": str(stick),
                                                                   "available": True, "note": "removable"}]):
                ok = self._plan(c, "On the stick", location=str(stick / "p"), project_id="on-the-stick")
                self.assertEqual(ok.status_code, 200, ok.text)
                self.assertFalse((stick / "p").exists(), "a plan writes nothing")
                self._refused(c, "plan", str(stick), "top_level")
                self._refused(c, "plan", str(Path("/run/media") / "someone-else" / "Stick" / "p"), "system_prefix")
            self.assertEqual(sorted(p.name for p in root.iterdir()), [], "no refusal created anything")
            blocked = {(e.get("details") or {}).get("reason_class") for e in self._events(c, "project.blocked")}
            self.assertTrue({"corvin_home", "protected", "hidden", "symlink", "top_level", "system_prefix", "other_tenant",
                             "workspace_root", "nested", "not_empty", "missing"} <= blocked, blocked)
            self._chain_has_no_path(c)
            _verify_chain(self, c)

    @unittest.skipUnless(os.name != "nt", "the win32 fixture simulates Windows on a POSIX host")
    def test_unc_paths_are_refused_without_a_filesystem_call(self):
        from core.projects import registry
        calls = []
        real_stat, real_lstat = os.stat, os.lstat
        for raw in (r"\\server\share\p", r"\\?\UNC\server\share\p", r"\\.\PhysicalDrive0"):
            with mock.patch.object(os, "name", "nt"), \
                    mock.patch.object(os, "stat", lambda *a, **k: calls.append(a) or real_stat(*a, **k)), \
                    mock.patch.object(os, "lstat", lambda *a, **k: calls.append(a) or real_lstat(*a, **k)):
                for purpose in ("create", "adopt", "projects_root", "move", "relocate", "import"):
                    with self.assertRaises(registry.ProjectPathError) as cm:
                        registry.validate_location("_default", raw, purpose=purpose)
                    self.assertEqual(cm.exception.reason_class, "path_rule")
        self.assertEqual(calls, [], "a UNC path was stat-ed")

    @unittest.skipUnless(os.name == "nt", "a drive-letter root exists only on Windows")
    def test_a_drive_root_child_is_top_level(self):
        from core.projects import registry
        with self.assertRaises(registry.ProjectPathError) as cm:
            registry.validate_location("_default", r"D:\projects", purpose="projects_root")
        self.assertEqual(cm.exception.reason_class, "top_level")

    def test_a_bind_mount_of_corvin_home_is_corvin_home(self):
        import shutil as _sh
        import subprocess
        if os.name == "nt" or _sh.which("unshare") is None:
            self.skipTest("needs unshare (Linux) to bind-mount without root")
        probe = subprocess.run(["unshare", "-rm", "true"], capture_output=True)
        if probe.returncode != 0:
            self.skipTest("this runner cannot create a user+mount namespace (unshare -rm)")
        with self._ctx() as c:
            mnt = self.tmp / "bind"
            mnt.mkdir()
            code = ("import sys; from core.projects import registry\n"
                    "try:\n registry.validate_location('_default', sys.argv[1], purpose='create')\n print('ACCEPTED')\n"
                    "except registry.ProjectPathError as e:\n print(e.reason_class)\n")
            sh = (f"mount --bind {c.home} {mnt} && {sys.executable} -c \"$CODE\" {mnt / 'tenants' / 'newproj'}")
            r = subprocess.run(["unshare", "-rm", "sh", "-c", sh], capture_output=True, text=True,
                               env={**os.environ, "CODE": code, "PYTHONPATH": str(HERE.parents[1])}, timeout=120)
            if "mount" in r.stderr and r.returncode != 0:
                self.skipTest(f"bind mount refused here: {r.stderr.strip()[:120]}")
            self.assertEqual(r.stdout.strip().splitlines()[-1], "corvin_home", r.stdout + r.stderr)

    # AC3: every kept check of Decision 10a refuses as at 65b587f2e for the same input
    def test_the_kept_checks_refuse_as_before(self):
        with self._ctx() as c:
            from core.projects import registry
            old = _old_registry()
            root = self._root(c)
            t = self.tmp
            (root / "real").mkdir()
            (root / "link").symlink_to(root / "real", target_is_directory=True)
            (root / "full").mkdir()
            (root / "full" / "x").write_text("x")
            (root / "afile").write_text("x")
            reg = _plant_project(root / "mine", "mine")
            registry.register("_default", "mine", root / "mine", "adopted@1")
            _plant_project(root / "acmes", "acmes", tenant="acme")
            registry.register("acme", "acmes", root / "acmes", "adopted@1")
            (root / "bad").mkdir()
            (root / "bad" / "project.yaml").write_text("schema: 1\nid: BAD ID\n")
            (root / "noman").mkdir()
            (root / "lroot-real").mkdir()
            (t / "lroot").symlink_to(root / "lroot-real", target_is_directory=True)
            loc_cases = {
                "tenant id": ("../x", str(root / "p"), {}),
                "not a string": ("_default", None, {}),
                "NUL": ("_default", str(root / "a\x00b"), {}),
                "too long": ("_default", "/" + "a" * 1001, {}),
                "relative": ("_default", "projects/p", {}),
                "dotdot": ("_default", str(root / "a" / ".." / "b"), {}),
                "inside CORVIN_HOME": ("_default", str(c.home / "tenants" / "_default" / "p"), {}),
                "the CORVIN_HOME itself": ("_default", str(c.home), {}),
                "above CORVIN_HOME": ("_default", str(c.home.parent), {}),
                "protected component": ("_default", str(root / "p" / ".git" / "x"), {}),
                "protected, case": ("_default", str(root / "p" / ".GIT" / "x"), {}),
                "hidden": ("_default", str(root / ".hidden"), {}),
                "below a link": ("_default", str(root / "link" / "p"), {}),
                "the link itself": ("_default", str(root / "link"), {}),
                "another tenant's directory": ("_default", str(root / "acmes"), {}),
                "nested in a project": ("_default", str(root / "mine" / "sub"), {}),
                "create: not empty": ("_default", str(root / "full"), {}),
                "create: a file": ("_default", str(root / "afile"), {}),
                "adopt: missing": ("_default", str(root / "nope"), {"adopt": True}),
                "adopt: invalid manifest": ("_default", str(root / "bad"), {"adopt": True}),
                "adopt: another tenant": ("_default", str(root / "acmes"), {"adopt": True}),
                "adopt: no manifest (managed adopt)": ("_default", str(root / "noman"), {"adopt": True}),
                "unmanaged adopt with a manifest": ("_default", str(root / "mine"), {"adopt": True, "unmanaged": True}),
            }
            for label, (tid, raw, kw) in loc_cases.items():
                with self.assertRaises((old.ProjectPathError, ValueError), msg=f"old: {label}"):
                    old.validate_location(tid, raw, **kw)
                with self.assertRaises((registry.ProjectPathError, ValueError), msg=f"new: {label}"):
                    registry.validate_location(tid, raw, **kw)
            root_cases = {
                "fewer than three parts": "/x",
                "the anchor": "/",
                "inside CORVIN_HOME": str(c.home / "x"),
                "above CORVIN_HOME": str(c.home.parent),
                "protected component": str(t / "x" / "tenants" / "p"),
                "a file": str(root / "afile"),
                "relative": "rel/x",
                "dotdot": str(t / "a" / ".." / "b"),
            }
            for label, raw in root_cases.items():
                with self.assertRaises(old.ProjectPathError, msg=f"old root: {label}"):
                    old.validate_root("_default", raw)
                with self.assertRaises(registry.ProjectPathError, msg=f"new root: {label}"):
                    registry.validate_root("_default", raw)
            # a link given as the root: the old function resolved it first and accepted it; the new rule refuses (an added refusal)
            with self.assertRaises(registry.ProjectPathError) as cm:
                registry.validate_root("_default", str(t / "lroot"))
            self.assertEqual(cm.exception.reason_class, "symlink")
            # the re-adopt exception for the tenant's own registered path stays: the rule passes it (old and new alike), the service
            # answers id_taken
            self.assertEqual(old.validate_location("_default", str(root / "mine"), adopt=True), (root / "mine").resolve())
            self.assertEqual(registry.validate_location("_default", str(root / "mine"), purpose="adopt"), (root / "mine").resolve())
            del reg

    # AC5: grandfathering of a stored projects root (REVIEW-0062 N1)
    def test_a_stored_root_keeps_working_and_is_rejudged_only_when_set_again(self):
        from core.paths import user_dirs
        t = self.tmp
        (t / "realdir" / "projects").mkdir(parents=True)
        (t / "lnkdir").symlink_to(t / "realdir", target_is_directory=True)
        stand_in = Path(tempfile.mkdtemp(prefix="sysprefix-"))             # OUTSIDE the own home: the home's subtree is exempt
        self.addCleanup(shutil.rmtree, stand_in, True)
        real_prefixes = user_dirs.system_prefixes
        for stored, cls, extra in ((t / "lnkdir" / "projects", "symlink", None),
                                   (t / ".projects", "hidden", None),
                                   (stand_in / "projects", "system_prefix", stand_in)):
            stored.mkdir(parents=True, exist_ok=True)
            with self._ctx(f"gf-{cls}") as c, \
                    mock.patch.object(user_dirs, "system_prefixes",
                                      (lambda e=extra: (*real_prefixes(), e)) if extra else real_prefixes):
                f = self._settings_file(c)
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(json.dumps({"projects_root": str(stored)}))
                plan, r = self._create(c, f"Under {cls}")
                self.assertEqual(r.status_code, 201, (cls, r.text))
                self.assertEqual(Path(plan["location"]).parent, stored.resolve())
                rr = c.own.put(f"{_URL}/root", json={"path": str(stored)}, headers=c.h)
                self.assertEqual(rr.status_code, 422, (cls, rr.text))
                self.assertEqual(rr.json()["detail"]["reason_class"], cls)
                (stored / "realsub").mkdir(exist_ok=True)
                (stored / "linkbelow").symlink_to(stored / "realsub", target_is_directory=True)
                for loc, want in ((stored / "a" / ".git" / "b", "protected"), (stored / ".hidden" / "b", "hidden"),
                                  (stored / "linkbelow" / "b", "symlink")):
                    self._refused(c, "plan", str(loc), want)
                _verify_chain(self, c)


class StorageAndRelocate(LocationBase):
    """T-0408 / ADR-2321 Decisions 8, 11, 14, 16: PUT /projects/settings/storage, the /projects/root alias, POST /{id}/relocate."""

    def _outcomes(self, c, name):
        return [((e.get("details") or {}).get("to"), (e.get("details") or {}).get("outcome")) for e in self._events(c, name)]

    # AC1
    def test_storage_round_trip_moves_nothing(self):
        with self._ctx() as c:
            r = c.own.put(f"{_URL}/settings/storage", json={"default_storage": "root"}, headers=c.h)
            self.assertEqual(r.status_code, 409, r.text)
            self.assertEqual(r.json()["detail"]["reason_class"], "root_required")
            self.assertEqual(r.json()["detail"]["message"], "Choose a folder of your own before switching to it.")
            _p, made = self._create(c, "Managed first")
            self.assertEqual(made.status_code, 201, made.text)
            mr = Path(c.own.get(_URL).json()["managed_root"])
            root = self.tmp / "work" / "projects"
            r = c.own.put(f"{_URL}/settings/storage", json={"default_storage": "root", "projects_root": str(root)}, headers=c.h)
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual((r.json()["default_storage"], r.json()["projects_root"], r.json()["managed_root"]),
                             ("root", str(root), str(mr)))
            self.assertEqual(r.json()["outside"], ["managed-first"])
            plan, made = self._create(c, "Rooted now")
            self.assertEqual(made.status_code, 201, made.text)
            self.assertEqual(Path(plan["location"]).parent, root)
            before = (_tree(mr), _tree(root))
            r = c.own.put(f"{_URL}/settings/storage", json={"default_storage": "managed"}, headers=c.h)
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["default_storage"], "managed")
            self.assertEqual((_tree(mr), _tree(root)), before, "switching moved something")
            self.assertEqual(Path(self._plan(c, "Back home").json()["location"]).parent, mr)
            bad = c.own.put(f"{_URL}/settings/storage", json={"default_storage": "root", "projects_root": "/etc/x"}, headers=c.h)
            self.assertEqual((bad.status_code, bad.json()["detail"]["reason_class"]), (422, "system_prefix"))
            self.assertEqual(self._outcomes(c, "project.storage_changed"),
                             [("root", "requested"), ("root", "applied"), ("managed", "requested"), ("managed", "applied")])
            self.assertIn(c.own.put(f"{_URL}/settings/storage", json={"default_storage": "root"}).status_code, (401, 403))
            self._chain_has_no_path(c)
            _verify_chain(self, c)

    # AC2 (REVIEW-0061 M16, REVIEW-0062 N9)
    def test_put_root_is_an_alias_that_keeps_the_settings(self):
        with self._ctx() as c:
            c.own.get(_URL)
            before = json.loads(self._settings_file(c).read_text())
            a = self.tmp / "work" / "a"
            b = self.tmp / "work" / "b"
            self.assertEqual(c.own.put(f"{_URL}/root", json={"path": str(a)}, headers=c.h).status_code, 200)
            after = json.loads(self._settings_file(c).read_text())
            self.assertEqual({k: after.get(k) for k in ("managed_root", "workspace_key")},
                             {k: before[k] for k in ("managed_root", "workspace_key")})
            self.assertEqual(after["default_storage"], "root")
            self.assertEqual(c.own.put(f"{_URL}/root", json={"path": str(b)}, headers=c.h).status_code, 200)
            self.assertEqual(len(self._events(c, "project.root_changed")), 2)
            self.assertEqual(self._outcomes(c, "project.storage_changed"), [("root", "requested"), ("root", "applied")],
                             "only the call that flips managed -> root records a storage change")

    def test_rekey_only_for_a_foreign_workspace(self):
        with self._ctx() as c:
            mr = Path(c.own.get(_URL).json()["managed_root"])
            r = c.own.put(f"{_URL}/settings/storage", json={"rekey": True}, headers=c.h)
            self.assertEqual((r.status_code, r.json()["detail"]["reason_class"]), (409, "not_foreign"))
            other = self.tmp / "copy-of" / ".corvin"
            other.mkdir(parents=True)
            m = json.loads((mr / "workspace.json").read_text())
            (mr / "workspace.json").write_text(json.dumps({**m, "corvin_home": str(other)}))
            r = c.own.put(f"{_URL}/settings/storage", json={"rekey": True}, headers=c.h)
            self.assertEqual(r.status_code, 200, r.text)
            new = Path(r.json()["managed_root"])
            self.assertNotEqual(new, mr)
            self.assertEqual(c.own.get(_URL).json()["workspace"], "ok")
            self.assertTrue((mr / "workspace.json").is_file(), "the old workspace is left as it is")
            self.assertIn((None, "rekeyed"), [(None, o) for _t, o in self._outcomes(c, "project.storage_changed")])

    # AC3
    def test_relocate_relinks_a_renamed_folder(self):
        with self._ctx() as c:
            from core.projects import attachment
            plan, r = self._create(c, "Wander", location=str(self.tmp / "code" / "wander"))
            self.assertEqual(r.status_code, 201, r.text)
            pid = plan["project_id"]
            h = c.own.post(f"{_URL}/{pid}/relocate", json={"location": str(self.tmp / "code" / "wander")}, headers=c.h)
            self.assertEqual((h.status_code, h.json()["detail"]["reason_class"]), (409, "not_missing"))
            self.assertEqual(h.json()["detail"]["message"],
                             "This project's folder is still where the list says; use Move to change it.")
            attachment.attach("_default", "chat-1", pid, str(self.tmp / "code" / "wander"))
            os.rename(self.tmp / "code" / "wander", self.tmp / "code" / "renamed")
            self.assertEqual(c.own.get(f"{_URL}/{pid}").json()["state"], "missing")
            other = self.tmp / "code" / "someone"
            _plant_project(other, "someone")
            w = c.own.post(f"{_URL}/{pid}/relocate", json={"location": str(other)}, headers=c.h)
            self.assertEqual((w.status_code, w.json()["detail"]["reason_class"]), (422, "wrong_manifest"))
            ok = c.own.post(f"{_URL}/{pid}/relocate", json={"location": str(self.tmp / "code" / "renamed")}, headers=c.h)
            self.assertEqual(ok.status_code, 200, ok.text)
            self.assertEqual(ok.json()["state"], "ok")
            self.assertEqual(ok.json()["path"], str((self.tmp / "code" / "renamed").resolve()))
            self.assertIsNotNone(ok.json()["repaired_files"], "repair ran")
            self.assertEqual(attachment._load("_default")["chat-1"]["project_id"], pid)
            self.assertEqual(Path(attachment._load("_default")["chat-1"]["path"]), (self.tmp / "code" / "renamed").resolve())
            self.assertEqual([(e.get("details") or {}).get("outcome") for e in self._events(c, "project.relocated")],
                             ["requested", "applied"])
            self.assertEqual(c.other.post(f"{_URL}/{pid}/relocate", json={"location": str(other)},
                                          headers={"X-CSRF-Token": c.other_csrf}).status_code, 404)
            self._chain_has_no_path(c)
            _verify_chain(self, c)


class MoveNamespaceHints(LocationBase):
    """T-0409 / ADR-2321 Decisions 2, 11, 16: cross-disk refusal on Move, the /projects/-/ namespace, the path-gate hint guard."""

    # AC1
    def test_a_cross_device_move_is_refused_and_nothing_changes(self):
        import errno
        with self._ctx() as c:
            plan, r = self._create(c, "Stay put", location=str(self.tmp / "code" / "stay-put"))
            self.assertEqual(r.status_code, 201, r.text)
            pid, src = plan["project_id"], self.tmp / "code" / "stay-put"
            tree, index = _tree(src), c.own.get(f"{_URL}/{pid}").json()["path"]

            def exdev(a, b, **kw):          # the move renames into a directory descriptor (REVIEW-0063 m4)
                raise OSError(errno.EXDEV, "Invalid cross-device link")
            with mock.patch.object(c.svc.os, "rename", exdev):
                m = c.own.post(f"{_URL}/{pid}/move", json={"location": str(self.tmp / "other-disk" / "stay-put")}, headers=c.h)
            self.assertEqual(m.status_code, 409, m.text)
            self.assertEqual(m.json()["detail"], {"message": c.svc.CROSS_DEVICE, "reason_class": "cross_device"})
            self.assertEqual(_tree(src), tree)
            self.assertEqual(c.own.get(f"{_URL}/{pid}").json()["path"], index)
            self.assertIn("cross_device", [(e.get("details") or {}).get("reason_class") for e in self._events(c, "project.blocked")])

    def test_moving_into_and_out_of_the_workspace_updates_storage(self):
        with self._ctx() as c:
            mr = Path(c.own.get(_URL).json()["managed_root"])
            plan, r = self._create(c, "Traveller", location=str(self.tmp / "code" / "traveller"))
            self.assertEqual(r.status_code, 201, r.text)
            pid = plan["project_id"]
            m = c.own.post(f"{_URL}/{pid}/move", json={"location": str(mr / pid)}, headers=c.h)
            self.assertEqual(m.status_code, 200, m.text)
            self.assertEqual(m.json()["storage"], "managed")
            m = c.own.post(f"{_URL}/{pid}/move", json={"location": str(self.tmp / "code" / "back")}, headers=c.h)
            self.assertEqual(m.status_code, 200, m.text)
            self.assertEqual(m.json()["storage"], "own")

    # AC2
    def test_projects_named_like_collection_words_keep_working(self):
        with self._ctx() as c:
            names = ("import", "browse", "trash", "export", "exports", "files")
            for n in names:
                plan, r = self._create(c, n.title(), project_id=n)
                self.assertEqual(r.status_code, 201, (n, r.text))
            for n in names:
                self.assertEqual(c.own.get(f"{_URL}/{n}").json()["state"], "ok", n)
                self.assertEqual(c.own.get(f"{_URL}/{n}/health").status_code, 200, n)
                mv = c.own.post(f"{_URL}/{n}/move", json={"location": str(self.tmp / "moved" / n)}, headers=c.h)
                self.assertEqual(mv.status_code, 200, (n, mv.text))
            for method in ("get", "post", "put", "delete"):
                kw = {"headers": c.h} if method != "get" else {}
                if method in ("post", "put"):
                    kw["json"] = {}
                g = getattr(c.own, method)(f"{_URL}/-/anything", **kw)
                self.assertEqual(g.status_code, 404, (method, g.text))
                self.assertEqual(g.json()["detail"]["reason_class"], "not_found", method)
            self.assertEqual(c.own.get(f"{_URL}/-/import/x/y").status_code, 404)

    # AC3
    def test_hint_words_refuse_a_managed_id_and_warn_for_an_own_folder(self):
        with self._ctx() as c:
            r = self._plan(c, "Forge lab", project_id="forge-lab")
            self.assertEqual(r.status_code, 422, r.text)
            self.assertEqual(r.json()["detail"], {"message": "The id contains 'forge', a word the assistant's safety check protects: "
                                                             "commands that name this folder would be refused. Choose another id.",
                                                  "reason_class": "hint_word"})
            plan, made = self._create(c, "Forge lab", project_id="forge-lab", location=str(self.tmp / "code" / "forge-lab"))
            self.assertEqual(made.status_code, 201, made.text)
            self.assertEqual(plan["location_warnings"], ["hint_word"])
            self.assertIn("The assistant may refuse some shell commands in a folder whose path contains 'forge'.", plan["warnings"])
            plain = self._plan(c, "Quiet one").json()
            self.assertEqual(plain["location_warnings"], [])

    def test_the_hint_test_is_the_gate_s_own(self):
        from core.projects import hints
        gate_dir = str(hints.gate_path().parent)
        sys.path.insert(0, gate_dir)
        try:
            import path_gate  # noqa: PLC0415 - the way the hook's own tests import it
        finally:
            sys.path.remove(gate_dir)
        samples = []
        for h in path_gate._PROTECTED_HINTS:
            samples += [h, h.upper(), f"/home/u/x-{h}-y", f"C:\\Users\\u\\{h}"]
        samples += ["/home/u/code/alpha", "w-0123456789", "/home/u/.local/share/corvin/projects/w-abcdef0123/alpha", "Corvin"]
        for x in samples:
            self.assertEqual(hints.looks_protected(x), path_gate._looks_protected(x), x)
        src = Path(hints.__file__).read_text()
        for h in ("skill-forge", "audit.jsonl", "mcp-tools", "requirements.txt"):
            self.assertNotIn(f'"{h}"', src, "hints.py keeps no copy of the list")

    # AC4
    def test_an_unloadable_hint_source_refuses_managed_creates(self):
        from core.projects import hints, registry
        copy = self.tmp / "hooks" / "path_gate.py"
        copy.parent.mkdir()
        shutil.copy2(hints.gate_path(), copy)
        copy.rename(copy.with_name("path_gate.renamed"))
        with self._ctx() as c, mock.patch.object(hints, "GATE_PATH", copy):
            hints.reset()
            try:
                # REVIEW-0064 #1: refused under its own class (the id is not at fault), named in GET / and logged once
                with self.assertLogs("corvin.projects", level="ERROR") as logged:
                    r = self._plan(c, "Harmless name")
                    again = self._plan(c, "Another name")
                self.assertEqual(r.status_code, 422, r.text)
                self.assertEqual(r.json()["detail"], {"message": registry.SENTENCES["hint_source"], "reason_class": "hint_source"})
                self.assertEqual(again.json()["detail"]["reason_class"], "hint_source")
                self.assertEqual(len([m for m in logged.output if "hint_source" in m]), 1, logged.output)
                self.assertIn("could not be loaded", c.own.get(_URL).json()["hint_source"])
                own = self._plan(c, "Harmless name", location=str(self.tmp / "code" / "harmless"))
                self.assertEqual(own.status_code, 200, "an own folder is never refused for the hint source")
            finally:
                hints.reset()


class WizardStorageChoice(LocationBase):
    """T-0388: the wizard's step 2 names the place (``storage`` on POST /plan), whatever the tenant's default is; the inspector's
    Location section reads the hint-word warning of an own folder from the detail (``location_warnings``)."""

    def test_plan_storage_managed_wins_over_a_root_default(self):
        with self._ctx() as c:
            mr = Path(c.own.get(_URL).json()["managed_root"])
            root = self.tmp / "work" / "projects"
            r = c.own.put(f"{_URL}/settings/storage", json={"default_storage": "root", "projects_root": str(root)}, headers=c.h)
            self.assertEqual(r.status_code, 200, r.text)
            p = self._plan(c, "Chosen managed", storage="managed")
            self.assertEqual(p.status_code, 200, p.text)
            self.assertEqual((Path(p.json()["location"]).parent, p.json()["storage"]), (mr, "managed"))
            p = self._plan(c, "Chosen root", storage="root")
            self.assertEqual((Path(p.json()["location"]).parent, p.json()["storage"]), (root, "own"))
            p = self._plan(c, "Default root")
            self.assertEqual(Path(p.json()["location"]).parent, root, "no storage: the tenant default as before")

    def test_plan_storage_root_without_a_projects_folder_is_root_required(self):
        with self._ctx() as c:
            p = self._plan(c, "No root", storage="root")
            self.assertEqual(p.status_code, 409, p.text)
            self.assertEqual(p.json()["detail"]["reason_class"], "root_required")
            bad = self._plan(c, "Bad kind", storage="other")
            self.assertEqual(bad.status_code, 400, bad.text)

    def test_the_detail_carries_the_hint_warning_of_an_own_folder(self):
        with self._ctx() as c:
            _plan, made = self._create(c, "Forge lab", project_id="forge-lab", location=str(self.tmp / "code" / "forge-lab"))
            self.assertEqual(made.status_code, 201, made.text)
            d = c.own.get(f"{_URL}/forge-lab").json()
            self.assertEqual(d["location_warnings"], [{"class": "hint_word", "message":
                             "The assistant may refuse some shell commands in a folder whose path contains 'forge'."}])
            _plan, made = self._create(c, "Quiet one")
            self.assertEqual(made.status_code, 201, made.text)
            self.assertEqual(c.own.get(f"{_URL}/quiet-one").json()["location_warnings"], [])


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
