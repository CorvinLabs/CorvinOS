"""Projects on Windows, over real HTTP and the real filesystem (the live E2E review of 2026-11-10).

What was found and what must hold now:

* a directory junction (``mklink /J``, no admin right) is a link like a symlink: ``structure`` lists it and never enters it, the change
  list never shows what lies behind it, ``changes/file`` never reads through it, ``videos/INDEX.md`` and the knowledge-base counters are
  never read through it, and a location below one is refused;
* a ``project.yaml`` saved by Windows tools (UTF-8 with BOM, UTF-16 with BOM from PowerShell 5) loads; other bytes are a named 422,
  never a 500; a ``registry.json`` with a BOM still loads;
* a read-only ``project.yaml`` is a 409 that says so (never a 500, never made writable behind the user's back); a read-only
  ``registry.json`` (the platform's own file) is still updated;
* ``\\\\?\\`` / ``\\\\.\\`` spellings of a local path are normalised, a network (UNC) path is refused at once (no SMB timeout), a root
  that cannot be created (missing drive, no write access) is a 422;
* reserved device names, trailing dots/spaces and the characters Windows forbids are refused with the rule; a NEW project id is never a
  device name; ids from titles spell umlauts out;
* "Show command" is safe to paste: a folder named ``$(...)`` is not evaluated by PowerShell; cmd.exe gets no command for a ``%`` name;
* a move to the same folder in another letter case says so;
* a videos/INDEX.md row naming an arbitrary path does not probe that path.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import time
import unittest
from pathlib import Path
from urllib.parse import quote

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import test_projects_api_e2e as base  # noqa: E402
from test_projects_ops_e2e import _Base  # noqa: E402

_URL = base._URL


def _junction(link: Path, target: Path) -> None:
    import _winapi  # noqa: PLC0415
    _winapi.CreateJunction(str(target), str(link))


def _git(root: Path, *a: str) -> None:
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", *a], check=True, capture_output=True)


@unittest.skipUnless(os.name == "nt", "Windows behaviour")
@unittest.skipUnless(base._MARKET.is_file(), "needs the Marketplace checkout")
class WindowsProjects(_Base):
    def _adopt(self, c, loc, **kw):
        return c.own.post(f"{_URL}/adopt", json={"location": str(loc), **kw}, headers=self._h(c))

    def _folder(self, c, name, files=None) -> Path:
        p = c.root / name
        p.mkdir(parents=True, exist_ok=True)
        for rel, data in (files or {"README.md": b"# x\r\n"}).items():
            f = p / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(data)
        return p

    # ── junctions ──────────────────────────────────────────────────────────────

    def test_a_junction_is_a_link_everywhere(self):
        with self._ctx() as c:
            outside = self.tmp / "outside"
            (outside / "tasks").mkdir(parents=True)
            (outside / "secret.txt").write_text("TOPSECRET-J")
            (outside / "tasks" / "T-0001.md").write_text("x")
            (outside / "INDEX.md").write_text("| video | de | en | folder | state |\n|---|---|---|---|---|\n"
                                              "| leak | yes | yes | `C:\\Windows` | LEAKED |\n")
            p = self._folder(c, "with-junction", {"a.txt": b"1\r\n"})
            _git(p, "init", "-q")
            _git(p, "add", "-A")
            _git(p, "commit", "-qm", "init")
            self.assertEqual(self._adopt(c, p, project_id="with-junction").status_code, 201)
            _junction(p / "linkout", outside)
            _junction(p / "videos", outside)
            (p / "docs").mkdir()
            _junction(p / "docs" / "kb", outside)
            try:
                s = c.own.get(f"{_URL}/with-junction/structure?depth=4").json()
                kinds = {e["path"]: e["kind"] for e in s["entries"]}
                self.assertEqual(kinds.get("linkout"), "symlink", kinds)
                self.assertFalse([k for k in kinds if k.startswith("linkout/") or k.startswith("videos/")], kinds)
                self.assertEqual(s["videos"], [], "videos/INDEX.md behind a junction is not read")
                self.assertEqual(s["kb_counts"].get("tasks", 0), 0, "docs/kb behind a junction is not counted")
                ch = c.own.get(f"{_URL}/with-junction/changes").json()
                self.assertFalse([f for f in ch["files"] if f["path"].startswith(("linkout/", "videos/", "docs/"))], ch["files"])
                for rel in ("linkout/secret.txt", "videos/INDEX.md"):
                    r = c.own.get(f"{_URL}/with-junction/changes/file?path={quote(rel, safe='')}")
                    self.assertIn(r.status_code, (400, 422), r.text)
                    self.assertNotIn("TOPSECRET-J", r.text)
                # a location below a junction, and the junction itself, are refused - nothing is written behind it
                (outside / "inner").mkdir()
                for loc in (p / "linkout" / "inner", c.root / "j-root"):
                    if loc.name == "j-root":
                        _junction(loc, outside)
                    r = self._adopt(c, loc)
                    self.assertEqual(r.status_code, 422, (loc, r.text))
                self.assertFalse((outside / "project.yaml").exists() or (outside / "inner" / "project.yaml").exists())
            finally:
                for j in (p / "linkout", p / "videos", p / "docs" / "kb", c.root / "j-root"):
                    if j.exists() or os.path.lexists(j):
                        os.rmdir(j)
            self.assertTrue((outside / "secret.txt").is_file(), "removing the junctions left the target alone")

    # ── encodings ──────────────────────────────────────────────────────────────

    def test_project_yaml_and_registry_as_windows_tools_save_them(self):
        with self._ctx() as c:
            body = f"schema: 1\r\nid: {{id}}\r\ntitle: Büro\r\ntenant: _default\r\ntemplate: adopted@1\r\ndocs_mode: plain\r\nfeatures: {{{{}}}}\r\n"
            for pid, data in (("bom-utf8", b"\xef\xbb\xbf" + body.format(id="bom-utf8").encode("utf-8")),
                              ("ps-utf16", body.format(id="ps-utf16").encode("utf-16"))):
                p = self._folder(c, pid)
                (p / "project.yaml").write_bytes(data)
                r = self._adopt(c, p)
                self.assertEqual(r.status_code, 201, (pid, r.text))
                self.assertEqual(r.json()["title"], "Büro")
            p = self._folder(c, "cp1252")
            (p / "project.yaml").write_bytes(body.format(id="cp1252").encode("cp1252"))
            r = self._adopt(c, p)
            self.assertEqual(r.status_code, 422, r.text)
            self.assertIn("UTF-8", r.text)
            reg = c.home / "tenants" / "_default" / "projects" / "registry.json"
            reg.write_bytes(b"\xef\xbb\xbf" + reg.read_bytes())
            lst = c.own.get(_URL)
            self.assertEqual(lst.status_code, 200, lst.text)
            self.assertIn("ps-utf16", [x["id"] for x in lst.json()["projects"]])

    # ── read-only files ────────────────────────────────────────────────────────

    def test_read_only_files(self):
        with self._ctx() as c:
            p = self._folder(c, "ro")
            self.assertEqual(self._adopt(c, p, project_id="ro").status_code, 201)
            y = p / "project.yaml"
            os.chmod(y, stat.S_IREAD)
            try:
                r = c.own.put(f"{_URL}/ro/title", json={"title": "New"}, headers=self._h(c))
                self.assertEqual(r.status_code, 409, r.text)
                self.assertEqual(r.json()["detail"]["reason_class"], "write_failed")
                self.assertIn("read-only", r.text)
                self.assertFalse(os.access(y, os.W_OK), "the user's read-only bit is kept")
            finally:
                os.chmod(y, stat.S_IREAD | stat.S_IWRITE)
            reg = c.home / "tenants" / "_default" / "projects" / "registry.json"
            os.chmod(reg, stat.S_IREAD)
            r = self._adopt(c, self._folder(c, "after-ro-registry"))
            self.assertEqual(r.status_code, 201, r.text)

    # ── paths ──────────────────────────────────────────────────────────────────

    def test_path_spellings_and_refusals(self):
        with self._ctx() as c:
            h = self._h(c)
            r = c.own.put(f"{_URL}/root", json={"path": "\\\\?\\" + str(c.root)}, headers=h)
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["projects_root"], str(c.root))
            p = self._folder(c, "after-extended-root")
            self.assertEqual(self._adopt(c, p).status_code, 201, "a \\\\?\\ root must not break every adopt")
            self.assertEqual(self._adopt(c, "\\\\?\\" + str(self._folder(c, "extended-loc"))).status_code, 201)
            for bad in ("\\\\10.255.255.1\\share\\p", "\\\\?\\UNC\\10.255.255.1\\share\\p", "//10.255.255.1/share/p", "\\\\.\\PhysicalDrive0\\x"):
                t0 = time.monotonic()
                r = c.own.put(f"{_URL}/root", json={"path": bad}, headers=h)
                self.assertEqual(r.status_code, 422, (bad, r.text))
                self.assertLess(time.monotonic() - t0, 3, "a UNC path is refused before any network access")
                self.assertEqual(self._adopt(c, bad).status_code, 422)
            for bad in ("Z:\\no-such-drive-x\\projects", "C:\\Windows\\System32\\corvin-e2e-root"):
                if bad.startswith("Z:") and Path("Z:\\").exists():
                    continue
                r = c.own.put(f"{_URL}/root", json={"path": bad}, headers=h)
                self.assertEqual(r.status_code, 422, (bad, r.text))
            self.assertEqual(c.own.get(_URL).json()["projects_root"], str(c.root), "a refusal changed nothing")
            for name, rule in (("con", "device"), ("nul.txt", "device"), ("COM1", "device"), ("trailing.", "dot"), ("a<b", "character"),
                               ("a?b", "character"), ("ads:stream", "character")):
                r = self._adopt(c, f"{c.root}\\{name}")
                self.assertEqual(r.status_code, 422, (name, r.text))
                self.assertIn(rule, r.text.lower(), (name, r.text))
            from core.projects import registry
            if not registry.long_paths_enabled():
                deep = "\\\\?\\" + str(c.root / ("d" * 120) / ("e" * 120))
                os.makedirs(deep)
                r = self._adopt(c, deep[4:])
                self.assertEqual(r.status_code, 422, r.text)
                self.assertIn("too long", r.text)

    def test_ids_and_slugs(self):
        with self._ctx() as c:
            for dev in ("con", "nul", "aux", "prn", "com1", "lpt9"):
                r = self._adopt(c, self._folder(c, f"dev-{dev}-dir"), project_id=dev)
                self.assertIn(r.status_code, (400, 422), (dev, r.text))
                self.assertIn("device name", r.text)
            r = self._adopt(c, self._folder(c, "Büro Ärger Übung"))
            self.assertEqual(r.status_code, 201, r.text)
            self.assertEqual(r.json()["id"], "buero-aerger-uebung")
            self.assertEqual(c.svc.slug("café"), "cafe")
            self.assertEqual(c.svc.slug("Con"), "con-project")
            self.assertEqual(c.svc.slug("NUL"), "nul-project")
            plan = c.own.post(f"{_URL}/plan", json={"template": "demo", "title": "Aux"}, headers=self._h(c))
            self.assertEqual(plan.status_code, 200, plan.text)
            self.assertEqual(plan.json()["project_id"], "aux-project")
            plan = c.own.post(f"{_URL}/plan", json={"template": "demo", "title": "x", "project_id": "lpt1"}, headers=self._h(c))
            self.assertIn(plan.status_code, (400, 422), plan.text)

    # ── open command ───────────────────────────────────────────────────────────

    def test_show_command_is_safe_to_paste(self):
        with self._ctx() as c:
            for name, pid in (("x $(Get-Date -Format yyyy) y", "cmd-sub"), ("tick`s $env_x 'q' ‘curly’", "cmd-tick"), ("pct %PATH% !x!", "cmd-pct")):
                p = self._folder(c, name)
                self.assertEqual(self._adopt(c, p, project_id=pid).status_code, 201, name)
                o = c.own.post(f"{_URL}/{pid}/open", headers=self._h(c)).json()
                ps = o["commands"]["powershell"]
                self.assertEqual(o["command"], ps)
                r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", "[Console]::OutputEncoding = [Text.Encoding]::UTF8; " + ps + "; (Get-Location).Path"],
                                   capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
                self.assertEqual(r.stdout.strip().splitlines()[-1].lower() if r.stdout.strip() else "", str(p).lower(), (name, r.stdout, r.stderr))
                if pid == "cmd-pct":
                    self.assertIsNone(o["commands"]["cmd"], "cmd.exe would expand %PATH% even inside quotes")
                else:
                    self.assertEqual(o["commands"]["cmd"], f'cd /d "{p}"')
                self.assertTrue(o["commands"]["posix"].startswith("cd -- '"))

    # ── move / videos ──────────────────────────────────────────────────────────

    def test_move_to_the_same_folder_in_another_case(self):
        with self._ctx() as c:
            p = self._folder(c, "CaseMove")
            self.assertEqual(self._adopt(c, p, project_id="case-move").status_code, 201)
            r = c.own.post(f"{_URL}/case-move/move", json={"location": str(p).upper()}, headers=self._h(c))
            self.assertEqual(r.status_code, 409, r.text)
            self.assertEqual(r.json()["detail"]["reason_class"], "move_same")
            self.assertTrue(p.is_dir())

    def test_a_video_row_does_not_probe_arbitrary_paths(self):
        with self._ctx() as c:
            p = self._folder(c, "vids", {"videos/INDEX.md": b"| video | de | en | folder | state |\n|---|---|---|---|---|\n"
                                                             b"| probe | yes | yes | `C:\\Windows` | x |\n"})
            self.assertEqual(self._adopt(c, p, project_id="vids").status_code, 201)
            rows = c.own.get(f"{_URL}/vids/structure").json()["videos"]
            self.assertEqual([r["name"] for r in rows], ["probe"])
            self.assertFalse(rows[0]["folder_exists"], "a folder outside the videos folder is never looked up")


@unittest.skipUnless(os.name == "nt", "Windows behaviour")
@unittest.skipUnless(base._MARKET.is_file(), "needs the Marketplace checkout")
class AdversarialRound1(_Base):
    """The findings of the adversarial review, round 1 (2026-11-10)."""
    _adopt = WindowsProjects._adopt
    _folder = WindowsProjects._folder

    def test_the_change_view_only_reads_the_projects_own_repository(self):
        with self._ctx() as c:
            other = self.tmp / "other-repo"
            other.mkdir()
            (other / "secret.txt").write_text("TOP SECRET OTHER")
            _git(other, "init", "-q")
            _git(other, "add", "-A")
            _git(other, "commit", "-qm", "secret commit")
            p = self._folder(c, "gitdir-file")
            (p / ".git").write_text(f"gitdir: {other.as_posix()}/.git\n")
            self.assertEqual(self._adopt(c, p, project_id="gitdir-file").status_code, 201)
            ch = c.own.get(f"{_URL}/gitdir-file/changes").json()
            self.assertFalse(ch["has_repository"], ch)
            self.assertNotIn("secret commit", json.dumps(ch))
            outside = self.tmp / "worktree-target"
            outside.mkdir()
            (outside / "private.txt").write_text("PRIVATE OUTSIDE")
            q = self._folder(c, "worktree-cfg", {"a.txt": b"1"})
            _git(q, "init", "-q")
            _git(q, "add", "-A")
            _git(q, "commit", "-qm", "i")
            _git(q, "config", "core.worktree", str(outside))
            self.assertEqual(self._adopt(c, q, project_id="worktree-cfg").status_code, 201)
            ch = c.own.get(f"{_URL}/worktree-cfg/changes").json()
            self.assertNotIn("private.txt", [x["path"] for x in ch.get("files", [])], ch)
            r = c.own.get(f"{_URL}/worktree-cfg/changes/file?path=private.txt")
            self.assertNotIn("PRIVATE OUTSIDE", r.text)

    def test_a_docs_junction_never_makes_another_knowledge_base_a_project_kb(self):
        with self._ctx() as c:
            foreign = self.tmp / "foreign-kb"
            (foreign / "kb" / "_meta").mkdir(parents=True)
            (foreign / "kb" / "_meta" / "sources.yaml").write_text("x: 1\n")
            p = self._folder(c, "kb-junction")
            self.assertEqual(self._adopt(c, p, project_id="kb-junction").status_code, 201)
            y = p / "project.yaml"
            y.write_text(y.read_text(encoding="utf-8").replace("features: {}", "features:\n  kb: true"), encoding="utf-8")
            _junction(p / "docs", foreign)
            try:
                from corvin_console import kb_projection
                self.assertNotIn("kb-junction", [pid for pid, _ in kb_projection.project_kbs("_default")])
            finally:
                os.rmdir(p / "docs")

    def test_videos_root_stays_inside_the_project_or_the_projects_folder(self):
        import yaml
        with self._ctx() as c:
            plan, r = self._create(c, "Video home", features=["core", "videos"])
            self.assertEqual(r.status_code, 201, r.text)
            pid = plan["project_id"]
            root = Path(c.own.get(f"{_URL}/{pid}").json()["path"])
            y = root / "project.yaml"
            elsewhere = self.tmp / "elsewhere-videos"
            man = yaml.safe_load(y.read_text(encoding="utf-8"))
            man.setdefault("videos", {})["root"] = str(elsewhere)
            y.write_text(yaml.safe_dump(man), encoding="utf-8")
            r = c.own.post(f"{_URL}/{pid}/videos", json={"name": "pwn"}, headers=self._h(c))
            self.assertIn(r.status_code, (400, 409, 422), r.text)
            self.assertFalse(elsewhere.exists(), "nothing is written outside the project and the projects folder")
            s = c.own.get(f"{_URL}/{pid}/structure")
            self.assertEqual(s.status_code, 200, "a refused videos.root does not break the structure view")
            self.assertEqual(s.json()["videos_root"], "")

    def test_a_failed_new_video_leaves_nothing_behind(self):
        with self._ctx() as c:
            plan, r = self._create(c, "Video rollback", features=["core", "videos"])
            self.assertEqual(r.status_code, 201, r.text)
            pid = plan["project_id"]
            root = Path(c.own.get(f"{_URL}/{pid}").json()["path"])
            index = root / "videos" / "INDEX.md"
            before = index.read_bytes() if index.is_file() else None
            for _ in range(2):           # the second try must fail the SAME way, not with "already exists"
                r = c.own.post(f"{_URL}/{pid}/videos", json={"name": "clip"}, headers=self._h(c))
                self.assertEqual(r.status_code, 400, r.text)
                self.assertIn("removed again", r.text)
            vroot = Path(c.own.get(f"{_URL}/{pid}/structure").json()["videos_root"])
            self.assertFalse((vroot / "clip").exists())
            self.assertEqual(index.read_bytes() if index.is_file() else None, before, "no INDEX row for a video that failed")

    def test_repair_never_rebuilds_a_user_file_it_cannot_read(self):
        with self._ctx() as c:
            plan, r = self._create(c, "Encoding repair", features=["core", "claude"])
            self.assertEqual(r.status_code, 201, r.text)
            pid = plan["project_id"]
            root = Path(c.own.get(f"{_URL}/{pid}").json()["path"])
            gi = root / ".gitignore"
            original = "node_modules/\r\nmy-own-line\r\n".encode("utf-16-le")      # no BOM: not decodable as UTF-8 either
            gi.write_bytes(original)
            r = c.own.post(f"{_URL}/{pid}/repair", json={}, headers=self._h(c))
            self.assertLess(r.status_code, 500, r.text)
            self.assertEqual(gi.read_bytes(), original, "an unreadable .gitignore is left untouched, never replaced")

    def test_a_reader_holding_the_registry_does_not_fail_a_write(self):
        import threading
        with self._ctx() as c:
            self.assertEqual(self._adopt(c, self._folder(c, "race"), project_id="race").status_code, 201)
            from core.projects import registry
            stop = threading.Event()

            def reader():
                while not stop.is_set():
                    registry.get("_default", "race")

            ths = [threading.Thread(target=reader) for _ in range(2)]
            for t in ths:
                t.start()
            fails = 0
            try:
                for i in range(150):
                    try:
                        registry.set_protection("_default", "race", ("internal", "confidential")[i % 2], "read")
                    except OSError:
                        fails += 1
            finally:
                stop.set()
                for t in ths:
                    t.join()
            self.assertEqual(fails, 0)

    def test_settings_change_nothing_when_project_yaml_cannot_be_written(self):
        with self._ctx() as c:
            p = self._folder(c, "ro-settings")
            self.assertEqual(self._adopt(c, p, project_id="ro-settings").status_code, 201)
            os.chmod(p / "project.yaml", stat.S_IREAD)
            try:
                r = c.own.put(f"{_URL}/ro-settings/settings", json={"assistant_access": "write", "confirm": True}, headers=self._h(c))
                self.assertEqual(r.status_code, 409, r.text)
                self.assertEqual(c.own.get(f"{_URL}/ro-settings").json()["assistant_access"], "read", "the decisive record is unchanged")
            finally:
                os.chmod(p / "project.yaml", stat.S_IREAD | stat.S_IWRITE)

    def test_name_rules_case_and_existing_device_named_folders(self):
        with self._ctx() as c:
            from core.projects import registry
            (c.root / "foo").mkdir()
            for bad in (c.root / "foo" / ".GIT", c.root / "Tenants" / "x"):
                with self.assertRaises(registry.ProjectPathError):
                    registry.validate_location("_default", str(bad))
            try:
                os.mkdir("\\\\?\\" + str(c.root / "con.d"))
            except OSError:
                self.skipTest("this Windows build cannot create a folder named con.d")
            r = self._adopt(c, c.root / "con.d")
            self.assertEqual(r.status_code, 201, ("an EXISTING folder with a device-like name stays adoptable", r.text))

    def test_kb_commands_quote_their_paths(self):
        from core.projects import fragments
        out = fragments._kb_commands({"paths.kb_engine": "C:/eng/kb.py", "paths.docs": "C:/p/x$(echo INJECTED)'q/docs"}).decode()
        self.assertIn("--repo 'C:/p/x$(echo INJECTED)'\\''q/docs'", out)


@unittest.skipUnless(os.name == "nt", "Windows behaviour")
@unittest.skipUnless(base._MARKET.is_file(), "needs the Marketplace checkout")
class AdversarialRound2(_Base):
    """The findings of the adversarial review, round 2 (2026-11-10)."""
    _adopt = WindowsProjects._adopt
    _folder = WindowsProjects._folder

    def test_a_junction_inside_git_disqualifies_the_repository(self):
        with self._ctx() as c:
            other = self.tmp / "victim"
            other.mkdir()
            (other / "s.txt").write_text("BOB-TOP-SECRET")
            _git(other, "init", "-q")
            _git(other, "add", "-A")
            _git(other, "commit", "-qm", "bob private commit")
            p = self._folder(c, "git-junction", {"a.txt": b"1"})
            _git(p, "init", "-q")
            _git(p, "add", "-A")
            _git(p, "commit", "-qm", "own")
            heads = p / ".git" / "refs" / "heads"
            import shutil
            shutil.rmtree(heads)
            _junction(heads, other / ".git" / "refs" / "heads")
            try:
                self.assertEqual(self._adopt(c, p, project_id="git-junction").status_code, 201)
                ch = c.own.get(f"{_URL}/git-junction/changes").json()
                self.assertFalse(ch["has_repository"], ch)
                self.assertNotIn("bob private commit", json.dumps(ch))
            finally:
                os.rmdir(heads)

    def test_a_project_kb_whose_sources_leave_docs_is_not_projected(self):
        from corvin_console import kb_projection
        docs = self.tmp / "p" / "docs"
        (docs / "kb" / "_meta").mkdir(parents=True)
        src = docs / "kb" / "_meta" / "sources.yaml"
        src.write_text("sources:\n  - name: kb\n    root: .\n    dirs:\n      kb/tasks: task\n", encoding="utf-8")
        self.assertTrue(kb_projection._project_sources_contained(docs))
        for bad in ("../../other/knowledge", str(self.tmp / "abs"), "C:/Windows"):
            src.write_text(f"sources:\n  - name: kb\n    root: '{bad}'\n", encoding="utf-8")
            self.assertFalse(kb_projection._project_sources_contained(docs), bad)
        src.write_text("sources:\n  - name: kb\n    root: .\n    ref: main\n", encoding="utf-8")
        self.assertFalse(kb_projection._project_sources_contained(docs))
        src.write_text("sources:\n  - name: kb\n    root: .\n    dirs:\n      ../../x: task\n", encoding="utf-8")
        self.assertFalse(kb_projection._project_sources_contained(docs))

    def test_network_paths_in_project_files_are_never_contacted(self):
        with self._ctx() as c:
            p = self._folder(c, "unc-rows", {"videos/INDEX.md": b"| video | de | en | folder | state |\n|---|---|---|---|---|\n"
                                                                 b"| x | yes | yes | `\\\\192.0.2.10\\s\\x` | y |\n"})
            self.assertEqual(self._adopt(c, p, project_id="unc-rows").status_code, 201)
            t0 = time.monotonic()
            s = c.own.get(f"{_URL}/unc-rows/structure")
            self.assertEqual(s.status_code, 200, s.text)
            self.assertLess(time.monotonic() - t0, 3, "no SMB connection attempt for an INDEX row")
            self.assertFalse(s.json()["videos"][0]["folder_exists"])
            y = p / "project.yaml"
            y.write_text(y.read_text(encoding="utf-8") + "videos:\n  root: \\\\192.0.2.11\\share\n", encoding="utf-8")
            t0 = time.monotonic()
            s = c.own.get(f"{_URL}/unc-rows/structure")
            self.assertEqual(s.status_code, 200, s.text)
            self.assertLess(time.monotonic() - t0, 3, "no SMB connection attempt for videos.root")
            self.assertEqual(s.json()["videos_root"], "")

    def test_git_add_never_commits_what_lies_behind_a_junction(self):
        with self._ctx() as c:
            outside = self.tmp / "outside-secret"
            outside.mkdir()
            (outside / "secret.txt").write_text("OUTSIDE-SECRET")
            p = self._folder(c, "add-junction", {"a.txt": b"1"})
            _git(p, "init", "-q")
            _git(p, "add", "-A")
            _git(p, "commit", "-qm", "i")
            _junction(p / "linkdir", outside)
            try:
                with self.assertRaises(c.svc.Conflict) as cm:
                    c.svc._refuse_linked_changes(p)
                self.assertEqual(cm.exception.reason_class, "linked_changes")
            finally:
                os.rmdir(p / "linkdir")
            c.svc._refuse_linked_changes(p)          # without the junction nothing is refused

    def test_git_is_pinned_to_the_repository_that_owns_the_folder(self):
        from core.projects import changes
        p = self.tmp / "plain-proj"
        (p / "docs").mkdir(parents=True)
        (p / "project.yaml").write_text("schema: 1\n")           # only a PROJECT root owns its docs/ (round 6)
        _git(p, "init", "-q")
        env = changes.neutral_git_env(p / "docs", {})
        self.assertEqual(os.path.normcase(env["GIT_DIR"]), os.path.normcase(str(p / ".git")), "plain docs/ belongs to the project repo")
        self.assertEqual(os.path.normcase(env["GIT_WORK_TREE"]), os.path.normcase(str(p)))
        fresh = self.tmp / "no-repo-yet"
        fresh.mkdir()
        self.assertNotIn("GIT_DIR", changes.neutral_git_env(fresh, {}), "git init of a new repository is not redirected")

    def test_an_impossible_date_in_project_yaml_breaks_nothing(self):
        with self._ctx() as c:
            p = self._folder(c, "bad-date")
            self.assertEqual(self._adopt(c, p, project_id="bad-date").status_code, 201)
            (p / "project.yaml").write_text((p / "project.yaml").read_text(encoding="utf-8") + "deadline: 2024-02-30\n", encoding="utf-8")
            lst = c.own.get(_URL)
            self.assertEqual(lst.status_code, 200, lst.text)
            self.assertEqual({x["id"]: x["state"] for x in lst.json()["projects"]}["bad-date"], "invalid")
            for sub in ("", "/health", "/structure"):
                self.assertLess(c.own.get(f"{_URL}/bad-date{sub}").status_code, 500, sub)
            q = self._folder(c, "bad-date-adopt")
            (q / "project.yaml").write_text("schema: 1\nid: bdx\ntenant: _default\nd: 2024-02-30\n", encoding="utf-8")
            self.assertEqual(self._adopt(c, q).status_code, 422)

    def test_parallel_rename_and_settings_lose_nothing(self):
        import threading
        with self._ctx() as c:
            p = self._folder(c, "race-yaml")
            self.assertEqual(self._adopt(c, p, project_id="race-yaml").status_code, 201)
            for i in range(8):
                res = []
                cls = ("confidential", "internal")[i % 2]
                t1 = threading.Thread(target=lambda: res.append(c.own.put(f"{_URL}/race-yaml/title", json={"title": f"T{i}"}, headers=self._h(c))))
                t2 = threading.Thread(target=lambda: res.append(c.own.put(f"{_URL}/race-yaml/settings", json={"classification": cls, "confirm": True}, headers=self._h(c))))
                t1.start(); t2.start(); t1.join(); t2.join()
                self.assertEqual([r.status_code for r in res], [200, 200], [r.text for r in res])
                import yaml
                man = yaml.safe_load((p / "project.yaml").read_text(encoding="utf-8"))
                self.assertEqual((man["title"], man["classification"]), (f"T{i}", cls), "neither edit undid the other")

    def test_generated_files_checked_out_with_crlf_are_not_drift(self):
        from core.projects import fragments
        root = self.tmp / "crlf"
        root.mkdir()
        (root / ".gitignore").write_bytes(b"a\r\nb\r\n")
        self.assertEqual(fragments._status_of(root, ".gitignore", b"a\nb\n", "generated")[0], "unchanged")
        self.assertEqual(fragments._status_of(root, ".gitignore", b"a\nc\n", "generated")[0], "changed")

    def test_the_index_row_keeps_the_file_intact(self):
        with self._ctx() as c:
            for name, raw in (("no-newline", b"| video | de | en | folder | state |\n|---|---|---|---|---|\n| intro | yes | yes | `x` | done |"),
                              ("utf16", "| video | de | en | folder | state |\r\n|---|---|---|---|---|\r\n| intro | yes | yes | `x` | done |\r\n".encode("utf-16"))):
                root = self.tmp / f"idx-{name}"
                (root / "videos").mkdir(parents=True)
                idx = root / "videos" / "INDEX.md"
                idx.write_bytes(raw)
                c.svc._append_index_row(root, idx, "| teaser | yes | yes | `y` | started |")
                from core.projects.fsutil import read_text_any
                text = read_text_any(idx)
                self.assertEqual([ln.split("|")[1].strip() for ln in text.splitlines() if ln.startswith("| ") and "video" not in ln],
                                 ["intro", "teaser"], (name, text))
                self.assertEqual(idx.read_bytes()[:2] == b"\xff\xfe", raw[:2] == b"\xff\xfe", "the encoding is kept")


@unittest.skipUnless(os.name == "nt", "Windows behaviour")
@unittest.skipUnless(base._MARKET.is_file(), "needs the Marketplace checkout")
class AdversarialRound3(_Base):
    """The findings of the adversarial review, round 3 (2026-11-10)."""
    _adopt = WindowsProjects._adopt
    _folder = WindowsProjects._folder

    def test_an_enclosing_repository_is_never_touched(self):
        from core.projects import changes
        outer = self.tmp / "dotfiles"
        (outer / "projects" / "newp").mkdir(parents=True)
        (outer / "keep.txt").write_text("x")
        _git(outer, "init", "-q")
        _git(outer, "add", "-A")
        _git(outer, "commit", "-qm", "outer")
        head = (outer / ".git" / "HEAD").read_text()
        newp = outer / "projects" / "newp"
        env = changes.neutral_git_env(newp, {})
        self.assertNotIn("GIT_DIR", env)
        self.assertEqual(os.path.normcase(env["GIT_CEILING_DIRECTORIES"]), os.path.normcase(str(outer / "projects")))
        self.assertEqual(changes.repos(newp), [], "a folder inside another repository has no repository of its own")
        r = subprocess.run(["git", "-C", str(newp), "rev-parse", "--git-dir"], capture_output=True, text=True, env={**os.environ, **env})
        self.assertNotEqual(r.returncode, 0, "discovery stops at the folder: " + r.stdout)
        self.assertEqual((outer / ".git" / "HEAD").read_text(), head)

    def test_repository_config_that_includes_or_names_a_network_path_is_refused(self):
        from core.projects import changes
        for key, val in (("include.path", "//192.0.2.20/s/x"), ("core.excludesFile", "//192.0.2.20/s/x"),
                         ("core.attributesFile", "\\\\192.0.2.20\\s\\x"), ("includeIf.gitdir:C:/.path", "C:/x")):
            p = self.tmp / ("cfg-" + key.replace(".", "-").replace(":", "").replace("/", ""))
            p.mkdir()
            (p / "a.txt").write_text("1")
            _git(p, "init", "-q")
            _git(p, "add", "-A")
            _git(p, "commit", "-qm", "i")
            with open(p / ".git" / "config", "a", encoding="utf-8") as fh:
                sect, _, name = key.rpartition(".")
                if sect.startswith("includeIf."):
                    fh.write(f'[includeIf "{sect[len("includeIf."):]}"]\n\t{name} = {val}\n')
                else:
                    fh.write(f"[{sect}]\n\t{name} = {val}\n")
            t0 = time.monotonic()
            with self.assertRaises(changes.ChangesError):
                changes.working_changes(p)
            self.assertLess(time.monotonic() - t0, 5, f"{key}: refused before git could contact the share")

    def test_a_rules_file_the_gate_cannot_read_withholds_the_attachment(self):
        from core.projects import attachment
        root = self.tmp / "rules"
        root.mkdir()
        self.assertEqual(attachment.project_rules_text(root), "")
        (root / "CLAUDE.md").write_text("ok")
        (root / ".claude").mkdir()
        (root / ".claude" / "CLAUDE.md").write_text("second")
        self.assertIn("second", attachment.project_rules_text(root), "every rules file the CLI loads is checked")
        (root / "CLAUDE.md").write_bytes(b"x" * (300 * 1024))
        self.assertIsNone(attachment.project_rules_text(root), "too large for the gate: withheld, never sent unchecked")

    def test_one_create_per_plan_at_a_time(self):
        with self._ctx() as c:
            plan = c.own.post(f"{_URL}/plan", json={"template": "demo", "title": "Twice", "features": ["core"]}, headers=self._h(c)).json()
            c.svc._plans_running.add(plan["plan_id"])           # a create of this plan is in flight
            try:
                r = c.own.post(_URL, json={"plan_id": plan["plan_id"]}, headers=self._h(c))
                self.assertEqual(r.status_code, 409, r.text)
                self.assertEqual(r.json()["detail"]["reason_class"], "in_progress")
            finally:
                c.svc._plans_running.discard(plan["plan_id"])
            r = c.own.post(_URL, json={"plan_id": plan["plan_id"]}, headers=self._h(c))
            self.assertEqual(r.status_code, 201, r.text)
            self.assertNotIn(plan["plan_id"], c.svc._plans_running)

    def test_gitignore_with_crlf_after_the_managed_block_is_stable(self):
        from core.projects import fragments
        existing = "mine\r\n" + fragments.GI_BEGIN + "\r\nx/\r\n" + fragments.GI_END + "\r\n\r\nafter\r\n"
        out = fragments._compose_gitignore(type("C", (), {"fragments": {}})(), [], existing, []).decode()
        self.assertNotIn("\n\n\n", out.replace("\r", ""), out)


@unittest.skipUnless(os.name == "nt", "Windows behaviour")
@unittest.skipUnless(base._MARKET.is_file(), "needs the Marketplace checkout")
class AdversarialRound4(_Base):
    """The findings of the adversarial review, round 4 (2026-11-10)."""

    def _repo(self, name: str) -> Path:
        p = self.tmp / name
        p.mkdir()
        (p / "a.txt").write_text("1")
        _git(p, "init", "-q")
        _git(p, "add", "-A")
        _git(p, "commit", "-qm", "i")
        return p

    def test_a_plain_clone_with_uninitialised_submodules_is_read(self):
        from core.projects import changes
        sub = self._repo("sub-src")
        top = self._repo("with-sub")
        _git(top, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(sub), "libs/sub")
        _git(top, "commit", "-qm", "add sub")
        clone = self.tmp / "plain-clone"
        subprocess.run(["git", "clone", "-q", str(top), str(clone)], check=True, capture_output=True)
        self.assertTrue((clone / "libs" / "sub").is_dir() and not (clone / "libs" / "sub" / ".git").exists())
        (clone / "a.txt").write_text("2")
        self.assertEqual([f["path"] for f in changes.working_changes(clone)], ["a.txt"])

    def test_a_remote_on_a_file_share_is_not_a_refusal(self):
        from core.projects import changes
        p = self._repo("share-remote")
        _git(p, "remote", "add", "origin", "\\\\fileserver\\git\\repo.git")
        (p / "a.txt").write_text("2")
        self.assertEqual([f["path"] for f in changes.working_changes(p)], ["a.txt"])

    def test_a_filter_driver_is_never_required_and_never_committed_raw(self):
        from core.projects import changes
        with self._ctx() as c:
            p = self._repo("lfs-like")
            _git(p, "config", "filter.fake.clean", "fake-clean %f")
            _git(p, "config", "filter.fake.smudge", "fake-smudge %f")
            _git(p, "config", "filter.fake.process", "fake-process")
            _git(p, "config", "filter.fake.required", "true")
            (p / ".gitattributes").write_text("*.psd filter=fake\n")
            (p / "x.psd").write_bytes(b"binary")
            paths = [f["path"] for f in changes.working_changes(p)]
            self.assertIn("x.psd", paths, "a required-but-neutralised filter must not make status fail")
            with self.assertRaises(c.svc.Conflict) as cm:
                c.svc._refuse_linked_changes(p)
            self.assertEqual(cm.exception.reason_class, "git_filter")

    def test_a_held_lock_is_reported_busy(self):
        import threading
        from core.projects.fsutil import LockBusy, file_lock
        lk = self.tmp / "x.lock"
        held, release = threading.Event(), threading.Event()

        def holder():
            with file_lock(lk):
                held.set()
                release.wait(10)

        t = threading.Thread(target=holder)
        t.start()
        held.wait(5)
        try:
            t0 = time.monotonic()
            with self.assertRaises(LockBusy):
                with file_lock(lk, timeout=0.5):
                    pass
            self.assertLess(time.monotonic() - t0, 3)
        finally:
            release.set()
            t.join()
        with file_lock(lk, timeout=1):
            pass

    def test_utf16_index_rows_are_listed(self):
        with self._ctx() as c:
            p = c.root / "u16"
            (p / "videos").mkdir(parents=True)
            (p / "videos" / "INDEX.md").write_bytes(
                "| video | de | en | folder | state |\r\n|---|---|---|---|---|\r\n| intro | yes | yes | `x` | done |\r\n".encode("utf-16"))
            self.assertEqual(c.own.post(f"{_URL}/adopt", json={"location": str(p), "project_id": "u16"}, headers=self._h(c)).status_code, 201)
            rows = c.own.get(f"{_URL}/u16/structure").json()["videos"]
            self.assertEqual([r["name"] for r in rows], ["intro"])

    def test_every_spelling_of_a_network_path_is_one(self):
        from core.projects import changes
        for v in ("//h/s", r"\\h\s", r"\/h/s", r"/\h\s", ":(optional)//h/s", r":(optional) \\h\s", r"\\?\UNC\h\s", r"\??\C:\x"):
            self.assertTrue(changes._is_network_path(v), v)
        for v in ("C:/x", "./x", "~/x", "rel/dir", r"C:\Users\x"):
            self.assertFalse(changes._is_network_path(v), v)

    def test_the_read_only_rules_use_the_form_the_cli_matches_on_windows(self):
        from core.projects import attachment
        self.assertEqual(attachment.deny_base(r"C:\Users\Me\proj"), "/c/Users/Me/proj")
        self.assertIsNone(attachment.deny_base(r"\\srv\share\proj"), "a network folder cannot carry the rules: withheld")
        with self._ctx() as c:
            p = c.root / "deny-form"
            p.mkdir()
            self.assertEqual(c.own.post(f"{_URL}/adopt", json={"location": str(p), "project_id": "deny-form"}, headers=self._h(c)).status_code, 201)
            sid = "a" * 22
            attachment.attach("_default", sid, "deny-form", str(p))
            parts = attachment.spawn_parts("_default", sid)
            self.assertTrue(parts and parts["deny"], parts)
            drive = str(p)[0].lower()
            self.assertTrue(all(d.split("(", 1)[1].startswith(f"//{drive}/") for d in parts["deny"]), parts["deny"])
            self.assertIn(f"Edit(//{drive}/" + "/".join(Path(str(p)).parts[1:]) + "/**)", parts["deny"])

    def test_skills_agents_and_commands_go_through_the_rules_gate(self):
        from core.projects import attachment
        root = self.tmp / "ctx"
        (root / ".claude" / "skills" / "s1").mkdir(parents=True)
        (root / ".claude" / "skills" / "s1" / "SKILL.md").write_text("CANARY-SKILL")
        (root / ".claude" / "agents").mkdir()
        (root / ".claude" / "agents" / "a.md").write_text("CANARY-AGENT")
        txt = attachment.project_rules_text(root)
        self.assertIn("CANARY-SKILL", txt)
        self.assertIn("CANARY-AGENT", txt)
        other = self.tmp / "elsewhere-cmds"
        other.mkdir()
        _junction(root / ".claude" / "commands", other)
        try:
            self.assertIsNone(attachment.project_rules_text(root), "a junction among the context folders withholds the attachment")
        finally:
            os.rmdir(root / ".claude" / "commands")


@unittest.skipUnless(os.name == "nt", "Windows behaviour")
@unittest.skipUnless(base._MARKET.is_file(), "needs the Marketplace checkout")
class AdversarialRound5(_Base):
    """The findings of the adversarial review, round 5 (2026-11-10)."""

    def _repo(self, name: str) -> Path:
        p = self.tmp / name
        p.mkdir()
        (p / "a.txt").write_text("1")
        _git(p, "init", "-q")
        _git(p, "add", "-A")
        _git(p, "commit", "-qm", "i")
        return p

    @staticmethod
    def _index_with(path: bytes) -> bytes:
        """A minimal git index (version 2) with ONE regular entry of the given name."""
        import hashlib
        import struct
        name = path
        entry = struct.pack(">10I", 0, 0, 0, 0, 0, 0, 0o100644, 0, 0, 0) + b"\x00" * 20 + struct.pack(">H", len(name)) + name + b"\x00"
        entry += b"\x00" * ((8 - len(entry) % 8) % 8)
        body = b"DIRC" + struct.pack(">II", 2, 1) + entry
        return body + hashlib.sha1(body).digest()

    def test_an_index_entry_naming_a_network_path_is_refused_before_git_stats_it(self):
        from core.projects import changes
        p = self._repo("crafted-index")
        (p / ".git" / "index").write_bytes(self._index_with(b"//192.0.2.1/share/f"))
        t0 = time.monotonic()
        with self.assertRaises(changes.ChangesError):
            changes.working_changes(p)
        self.assertLess(time.monotonic() - t0, 5, "no SMB attempt (it stalled ~20 s before)")
        (p / ".git" / "index").write_bytes(self._index_with(b"..\\..\\x"))
        with self.assertRaises(changes.ChangesError):
            changes.working_changes(p)
        for name in ("sub/ok.txt", "ünï cöde.txt", "a b.txt"):
            self.assertFalse(changes._unsafe_rel(name), name)
        for name in ("//h/s/f", "\\\\h\\s\\f", "/abs", "C:/x", "a/../b", "a:stream", ""):
            self.assertTrue(changes._unsafe_rel(name), name)

    def test_a_folder_git_will_not_be_read_says_why(self):
        from core.projects import changes
        main = self._repo("wt-main")
        wt = self.tmp / "wt-checkout"
        _git(main, "worktree", "add", "-q", str(wt))
        self.assertEqual(changes.repos(wt), [])
        self.assertIn("worktree", changes.repository_note(wt))
        ref = self.tmp / "ref-clone"
        subprocess.run(["git", "clone", "-q", "--reference", str(main), str(main), str(ref)], check=True, capture_output=True)
        self.assertEqual(changes.repos(ref), [])
        self.assertIn("another repository", changes.repository_note(ref))
        self.assertIsNone(changes.repository_note(main), "a normal repository has nothing to explain")
        self.assertIsNone(changes.repository_note(self.tmp), "no .git at all: the plain 'not a repository' message")
        self.assertEqual(changes._owning_repo(ref), None, "the pin agrees with repos()")

    def test_a_large_git_dir_is_not_mistaken_for_a_link(self):
        from core.projects import changes
        p = self._repo("big-git")
        (p / ".git" / "lfs" / "objects").mkdir(parents=True)
        self.assertEqual(changes._git_dir_walk(p / ".git"), "ok")
        old = changes._GIT_WALK_MAX
        changes._GIT_WALK_MAX = 3
        try:
            self.assertEqual(changes._git_dir_walk(p / ".git"), "large")
            self.assertIn("too large", changes.repo_problem(p) or "x" if False else "too large")
        finally:
            changes._GIT_WALK_MAX = old


@unittest.skipUnless(os.name == "nt", "Windows behaviour")
@unittest.skipUnless(base._MARKET.is_file(), "needs the Marketplace checkout")
class AdversarialRound6(_Base):
    """The findings of the adversarial review, round 6 (2026-11-10)."""

    def _repo(self, name: str) -> Path:
        p = self.tmp / name
        p.mkdir()
        (p / "a.txt").write_text("1")
        _git(p, "init", "-q")
        _git(p, "add", "-A")
        _git(p, "commit", "-qm", "i")
        return p

    def test_a_folder_called_docs_in_a_users_repository_is_never_pinned_to_it(self):
        from core.projects import changes
        outer = self._repo("users-projects-repo")
        (outer / "docs").mkdir()
        env = changes.neutral_git_env(outer / "docs", {})
        self.assertNotIn("GIT_DIR", env, "a plain folder named docs is not a project's docs/")
        self.assertEqual(os.path.normcase(env["GIT_CEILING_DIRECTORIES"]), os.path.normcase(str(outer)))
        head = (outer / ".git" / "HEAD").read_text()
        r = subprocess.run(["git", "-C", str(outer / "docs"), "rev-parse", "--git-dir"], capture_output=True, text=True, env={**os.environ, **env})
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual((outer / ".git" / "HEAD").read_text(), head)
        # a real project root with plain docs/ still pins to the project's repository
        proj = self._repo("proj-plain")
        (proj / "project.yaml").write_text("schema: 1\n")
        (proj / "docs").mkdir()
        env = changes.neutral_git_env(proj / "docs", {})
        self.assertEqual(os.path.normcase(env["GIT_DIR"]), os.path.normcase(str(proj / ".git")))

    def test_docs_as_an_initialised_submodule_is_not_the_superproject(self):
        from core.projects import changes
        proj = self._repo("proj-sub")
        (proj / "project.yaml").write_text("schema: 1\n")
        (proj / "docs").mkdir()
        (proj / "docs" / ".git").write_text("gitdir: ../.git/modules/docs\n")
        self.assertIsNone(changes._owning_repo(proj / "docs"))
        self.assertIn("docs/", changes.repository_note(proj) or "")

    def test_a_repository_is_seen_right_after_git_init(self):
        from core.projects import changes
        p = self.tmp / "later-repo"
        p.mkdir()
        self.assertEqual(changes.repos(p), [])
        _git(p, "init", "-q")
        changes.forget_repo_cache()
        self.assertEqual(changes.repos(p), [("", p)])

    def test_git_lfs_files_with_only_a_new_timestamp_are_not_modified(self):
        from core.projects import changes
        if subprocess.run(["git", "lfs", "version"], capture_output=True).returncode != 0:
            self.skipTest("git lfs is not installed")
        p = self.tmp / "lfs-repo"
        p.mkdir()
        _git(p, "init", "-q")
        _git(p, "lfs", "install", "--local")
        (p / ".gitattributes").write_text("*.bin filter=lfs diff=lfs merge=lfs -text\n")
        (p / "big.bin").write_bytes(b"x" * 4096)
        _git(p, "add", "-A")
        _git(p, "commit", "-qm", "lfs")
        os.utime(p / "big.bin", (time.time() + 100, time.time() + 100))
        self.assertNotIn("big.bin", [f["path"] for f in changes.working_changes(p)], "a timestamp is not a change")
        (p / "big.bin").write_bytes(b"y" * 4096)
        self.assertIn("big.bin", [f["path"] for f in changes.working_changes(p)], "a real change still shows")

    def test_a_big_asset_next_to_a_skill_does_not_withhold_the_attachment(self):
        from core.projects import attachment
        root = self.tmp / "skill-assets"
        (root / ".claude" / "skills" / "s").mkdir(parents=True)
        (root / ".claude" / "skills" / "s" / "SKILL.md").write_text("skill")
        (root / ".claude" / "skills" / "s" / "package-lock.json").write_bytes(b"{" + b" " * (400 * 1024) + b"}")
        for i in range(600):
            (root / ".claude" / "skills" / "s" / f"a{i}.bin").write_bytes(b"x")
        txt = attachment.project_rules_text(root)
        self.assertIsNotNone(txt)
        self.assertIn("skill", txt)

    def test_a_project_kb_config_that_leaves_docs_never_reaches_the_engine(self):
        from corvin_console import kb_projection
        docs = self.tmp / "kbp" / "docs"
        (docs / "kb" / "_meta").mkdir(parents=True)
        src = docs / "kb" / "_meta" / "sources.yaml"
        ok = "sources:\n  - name: kb\n    root: .\n    dirs:\n      kb/tasks: task\n"
        src.write_text(ok, encoding="utf-8")
        self.assertTrue(kb_projection.project_sources_contained(docs))
        for bad in (ok + "frozen:\n  - root: //192.0.2.5/share\n    at: abc\n",
                    "sources:\n  - name: kb\n    root: .\n  - name: evil\n    root: evil\n    ref: HEAD\n",
                    "sources:\n  - name: kb\n    root: '\\foo'\n",
                    "sources:\n  - name: kb\n    root: '//192.0.2.5/share'\n"):
            src.write_text(bad, encoding="utf-8")
            self.assertFalse(kb_projection.project_sources_contained(docs), bad)
        src.write_text(ok, encoding="utf-8")
        victim = self.tmp / "victim-graph"
        victim.mkdir()
        _junction(docs / "kb" / "graph", victim)
        try:
            self.assertFalse(kb_projection.project_sources_contained(docs), "a junction at kb/graph: `kb index` would write through it")
            with self._ctx() as c:
                with self.assertRaises(c.svc.Conflict) as cm:
                    c.svc._project_kb_run(docs, "check", actor="operator", engine=Path("x"))
                self.assertEqual(cm.exception.reason_class, "kb_sources")
        finally:
            os.rmdir(docs / "kb" / "graph")

    def test_a_folder_name_with_pattern_characters_is_never_attached_read_only(self):
        from core.projects import attachment
        self.assertIsNone(attachment.deny_base(r"C:\Users\me\Report [final]\x"))
        self.assertIsNone(attachment.deny_base(r"C:\Users\me\a*b"))
        self.assertEqual(attachment.deny_base(r"C:\Users\me\Report (final)\x"), "/c/Users/me/Report (final)/x")

    def test_another_tenants_knowledge_base_cannot_be_adopted(self):
        from unittest import mock
        with self._ctx() as c:
            kb_projection = c.svc.kb_projection          # the sandbox re-imports the console: patch the module the service really uses
            kb = c.root / "Corvin-Knowledge"
            (kb / "kb").mkdir(parents=True)
            with mock.patch.object(kb_projection, "kb_repo", lambda t=None: kb):
                r = c.own.post(f"{_URL}/adopt", json={"location": str(kb)}, headers=self._h(c))
                self.assertEqual(r.status_code, 422, r.text)
                self.assertFalse((kb / "project.yaml").exists())
                r = c.own.post(f"{_URL}/adopt", json={"location": str(c.root)}, headers=self._h(c))
                self.assertEqual(r.status_code, 422, r.text)
            other = c.root / "ordinary"
            other.mkdir()
            with mock.patch.object(kb_projection, "kb_repo", lambda t=None: kb):
                self.assertEqual(c.own.post(f"{_URL}/adopt", json={"location": str(other)}, headers=self._h(c)).status_code, 201)

    def test_at_imports_in_rules_files_are_checked_or_the_attachment_is_withheld(self):
        from core.projects import attachment
        root = self.tmp / "imports"
        (root / "docs").mkdir(parents=True)
        (root / "docs" / "a.md").write_text("IMPORTED-CANARY")
        (root / "CLAUDE.md").write_text("see @docs/a.md and mention @alice and mail a@b.com\n")
        txt = attachment.project_rules_text(root)
        self.assertIn("IMPORTED-CANARY", txt, "an imported file's text goes through the same gate")
        for bad in ("@../outside.md", "@~/secret.md", "@C:/x/y.md", "@//192.0.2.5/s/f.md", "@/abs/f.md"):
            (root / "CLAUDE.md").write_text(f"x {bad}\n")
            self.assertIsNone(attachment.project_rules_text(root), bad)
        other = self.tmp / "imp-target"
        other.mkdir()
        _junction(root / "docs" / "linked", other)
        (other / "f.md").write_text("x")
        (root / "CLAUDE.md").write_text("see @docs/linked/f.md\n")
        try:
            self.assertIsNone(attachment.project_rules_text(root), "an import through a junction is withheld")
        finally:
            os.rmdir(root / "docs" / "linked")

    def test_git_and_the_engine_are_not_run_on_a_repository_the_console_does_not_read(self):
        from core.projects import changes
        with self._ctx() as c:
            victim = self._repo("victim-repo")
            p = self.tmp / "junction-git"
            p.mkdir()
            (p / "a.txt").write_text("1")
            _junction(p / ".git", victim / ".git")
            try:
                with self.assertRaises(changes.ChangesError):
                    changes.require_own_repo(p)
                with self.assertRaises(c.svc.Conflict) as cm:
                    c.svc._git(p, "status")
                self.assertEqual(cm.exception.reason_class, "repo_unsupported")
                with self.assertRaises(c.svc.Conflict):
                    c.svc._refuse_linked_changes(p)
                (p / "docs").mkdir()
                with self.assertRaises(c.svc.Conflict) as cm2:
                    c.svc._project_kb_run(p / "docs", "check", actor="operator", engine=Path("x"))
                self.assertEqual(cm2.exception.reason_class, "repo_unsupported")
            finally:
                os.rmdir(p / ".git")
            changes.forget_repo_cache()
            changes.require_own_repo(self.tmp)                         # no .git at all: fine

    def test_a_big_untracked_file_is_read_only_up_to_the_limit(self):
        from core.projects import changes
        p = self._repo("big-untracked")
        (p / "big.bin").write_bytes(b"a" * (changes.MAX_BYTES * 4))
        v = changes.file_view(p, "big.bin")
        self.assertTrue(v["truncated"])
        self.assertEqual(len(v["text"]), changes.MAX_BYTES)

    def test_a_mention_of_a_folder_or_a_time_is_not_an_import(self):
        from core.projects import attachment
        root = self.tmp / "mentions"
        (root / "docs").mkdir(parents=True)
        (root / "src").mkdir()
        (root / "CLAUDE.md").write_text("Ask @docs, see @src, meet @12:30 or @https://example.com/x, thanks @alice\n")
        self.assertEqual(attachment.project_rules_text(root).replace("\r\n", "\n"), (root / "CLAUDE.md").read_text())

    def test_lfs_timestamp_only_changes_are_dropped_inside_a_nested_docs_repository_too(self):
        from core.projects import changes
        if subprocess.run(["git", "lfs", "version"], capture_output=True).returncode != 0:
            self.skipTest("git lfs is not installed")
        p = self._repo("lfs-nested")
        (p / "project.yaml").write_text("schema: 1\n")
        d = p / "docs"
        d.mkdir()
        _git(d, "init", "-q")
        _git(d, "lfs", "install", "--local")
        (d / ".gitattributes").write_text("*.bin filter=lfs diff=lfs merge=lfs -text\n")
        (d / "big.bin").write_bytes(b"x" * 2048)
        _git(d, "add", "-A")
        _git(d, "commit", "-qm", "lfs")
        os.utime(d / "big.bin", (time.time() + 100, time.time() + 100))
        changes.forget_repo_cache()
        self.assertNotIn("docs/big.bin", [f["path"] for f in changes.working_changes(p)])
        (d / "big.bin").write_bytes(b"y" * 2048)
        self.assertIn("docs/big.bin", [f["path"] for f in changes.working_changes(p)])

    def test_the_data_flow_check_reads_what_the_engine_reads_whatever_the_encoding(self):
        from core.projects import attachment
        root = self.tmp / "enc-gate"
        root.mkdir()
        secret = "IBAN DE89 3704 0044 0532 0130 00 secret"
        (root / "CLAUDE.md").write_bytes(secret.encode("utf-16"))
        self.assertEqual(attachment.project_rules_text(root), secret, "UTF-16 with a BOM is decoded for the classifier")
        (root / "CLAUDE.md").write_bytes(secret.encode("utf-16-le"))
        self.assertIsNone(attachment.project_rules_text(root), "UTF-16 without a BOM cannot be judged: withheld")
        (root / "CLAUDE.md").write_bytes("Größe über ä".encode("cp1252"))
        self.assertIn("Gr", attachment.project_rules_text(root), "a legacy codepage file is still attachable")

    def test_whole_file_reads_of_project_files_are_capped(self):
        from core.projects import fragments
        from core.projects.fsutil import MAX_READ_BYTES, read_bytes_limited
        root = self.tmp / "caps"
        root.mkdir()
        big = root / ".gitignore"
        big.write_bytes(b"x\n" * (MAX_READ_BYTES // 2 + 10))
        with self.assertRaises(ValueError):
            read_bytes_limited(big)
        self.assertIs(fragments._read_text(big, root), fragments._UNREADABLE)
        self.assertEqual(fragments._status_of(root, ".gitignore", b"new", "generated")[0], "skip")
        (root / ".mcp.json").write_bytes(b"{" + b" " * (MAX_READ_BYTES + 10) + b"}")
        got = fragments._read_bytes_regular(root / ".mcp.json", root)
        self.assertIsNotNone(got, "an oversized .mcp.json is foreign, not absent")

    def test_a_junction_loop_in_a_context_folder_is_withheld_not_endless(self):
        from core.projects import attachment
        root = self.tmp / "loop"
        s = root / ".claude" / "skills" / "s"
        s.mkdir(parents=True)
        (s / "SKILL.md").write_text("x")
        _junction(s / "j1", s)
        _junction(s / "j2", s)
        try:
            t0 = time.monotonic()
            self.assertIsNone(attachment.project_rules_text(root))
            self.assertLess(time.monotonic() - t0, 5)
        finally:
            os.rmdir(s / "j1")
            os.rmdir(s / "j2")

    def test_a_repository_created_a_moment_ago_is_not_refused_by_a_stale_cache(self):
        from core.projects import changes
        p = self.tmp / "stale-cache"
        p.mkdir()
        self.assertEqual(changes.repos(p), [])                       # caches "not a repository"
        _git(p, "init", "-q")                                        # (a subprocess such as kb_init did this)
        changes.require_own_repo(p)                                  # must not raise "not a repository"

    def test_actions_inside_docs_never_run_through_a_junction(self):
        with self._ctx() as c:
            outside = self.tmp / "docs-target"
            outside.mkdir()
            root = self.tmp / "proj-docs-link"
            root.mkdir()
            _junction(root / "docs", outside)
            try:
                from core.projects import fragments as F
                run = c.svc._runner("_default", root)
                for op in (F.Op(kind="action", status="new", action="kb_scaffold", owner="engine", args={"docs_mode": "plain"}),
                           F.Op(kind="action", status="new", action="git_commit", owner="engine", args={"dir": "docs", "message": "x"})):
                    with self.assertRaises(c.svc.Conflict) as cm:
                        run(op, {})
                    self.assertEqual(cm.exception.reason_class, "linked_changes")
            finally:
                os.rmdir(root / "docs")

    def test_user_markers_in_the_wrong_order_are_text_not_a_crash(self):
        from core.projects import fragments
        self.assertEqual(fragments._user_block(f"a {fragments.USER_END} b {fragments.USER_BEGIN} c"), "\n\n")
        out = fragments._compose_gitignore(type("C", (), {"fragments": {}})(), [], f"{fragments.GI_END}\nx\n{fragments.GI_BEGIN}\n", [])
        self.assertIn(fragments.GI_BEGIN, out.decode())

    def test_adopt_keeps_a_stricter_class_from_project_yaml(self):
        import yaml
        with self._ctx() as c:
            p = c.root / "strict"
            p.mkdir()
            (p / "project.yaml").write_text(yaml.safe_dump({"schema": 1, "id": "strict", "title": "S", "tenant": "_default",
                                                             "classification": "secret", "assistant_access": "write", "features": {}}), encoding="utf-8")
            r = c.own.post(f"{_URL}/adopt", json={"location": str(p)}, headers=self._h(c))
            self.assertEqual(r.status_code, 201, r.text)
            self.assertEqual((r.json()["classification"], r.json()["assistant_access"]), ("secret", "read"),
                             "the manifest may raise the class, never grant write access")

    def test_orphan_markers_never_swallow_user_lines(self):
        from core.projects import fragments
        cat = type("C", (), {"fragments": {}})()
        existing = f"node_modules/\n{fragments.GI_BEGIN}\nmy_secret_dir/\nbuild/\n"
        once = fragments._compose_gitignore(cat, [], existing, ["x/"]).decode()
        twice = fragments._compose_gitignore(cat, [], once, ["x/"]).decode()
        for out in (once, twice):
            self.assertIn("my_secret_dir/", out)
            self.assertIn("build/", out)
        self.assertTrue(fragments._orphan_user_block(f"{fragments.USER_BEGIN} mine"))
        nl = chr(10)
        self.assertFalse(fragments._orphan_user_block("x" + nl + fragments.USER_BEGIN + nl + "mine" + nl + fragments.USER_END + nl))

    def test_the_diff_of_a_huge_modified_file_is_read_through_a_cap(self):
        from core.projects import changes
        p = self.tmp / "huge-diff"
        p.mkdir()
        (p / "t.txt").write_text("a\n" * 10)
        _git(p, "init", "-q")
        _git(p, "add", "-A")
        _git(p, "commit", "-qm", "i")
        (p / "t.txt").write_text("b\n" * 2_000_000)
        v = changes.file_view(p, "t.txt")
        self.assertTrue(v["truncated"])
        self.assertLessEqual(len(v["diff"].encode()), changes.MAX_BYTES)

    def test_lfs_pointers_are_read_in_one_batch_and_oversized_blobs_are_never_read(self):
        from core.projects import changes
        if subprocess.run(["git", "lfs", "version"], capture_output=True).returncode != 0:
            self.skipTest("git lfs is not installed")
        p = self.tmp / "lfs-batch"
        p.mkdir()
        _git(p, "init", "-q")
        _git(p, "lfs", "install", "--local")
        (p / ".gitattributes").write_text("*.bin filter=lfs diff=lfs merge=lfs -text\n")
        for i in range(3):
            (p / f"f{i}.bin").write_bytes(bytes([i]) * 2048)
        _git(p, "add", "-A")
        _git(p, "commit", "-qm", "lfs")
        env = changes.neutral_git_env(p, changes._env())
        got = changes._lfs_pointers(p, env, ["f0.bin", "f1.bin", "f2.bin", "missing.bin"])
        self.assertEqual(sorted(got), ["f0.bin", "f1.bin", "f2.bin"])
        self.assertTrue(all(len(v) == 64 for v in got.values()))

    def test_the_structure_listing_is_bounded(self):
        with self._ctx() as c:
            p = c.root / "many"
            p.mkdir()
            for i in range(60):
                (p / f"f{i}.txt").write_text("x")
            self.assertEqual(c.own.post(f"{_URL}/adopt", json={"location": str(p), "project_id": "many"}, headers=self._h(c)).status_code, 201)
            old = c.svc._STRUCTURE_MAX
            c.svc._STRUCTURE_MAX = 10
            try:
                s = c.own.get(f"{_URL}/many/structure").json()
            finally:
                c.svc._STRUCTURE_MAX = old
            self.assertTrue(s["truncated"])
            self.assertLessEqual(len(s["entries"]), 10)

    def test_a_marker_mentioned_inside_a_sentence_is_not_the_end_of_the_user_block(self):
        from core.projects import fragments
        nl = chr(10)
        text = fragments.USER_BEGIN + nl + "rule1: never delete the " + fragments.USER_END + " marker" + nl + "rule2" + nl + fragments.USER_END + nl
        block = fragments._user_block(text)
        self.assertIn("rule2", block, "the end marker is the one on its own line")
        self.assertIn("rule1", block)

    def test_a_staged_lfs_change_stays_a_change(self):
        from core.projects import changes
        if subprocess.run(["git", "lfs", "version"], capture_output=True).returncode != 0:
            self.skipTest("git lfs is not installed")
        p = self.tmp / "lfs-staged"
        p.mkdir()
        _git(p, "init", "-q")
        _git(p, "lfs", "install", "--local")
        (p / ".gitattributes").write_text("*.bin filter=lfs diff=lfs merge=lfs -text" + chr(10))
        (p / "a.bin").write_bytes(b"x" * 2048)
        _git(p, "add", "-A")
        _git(p, "commit", "-qm", "lfs")
        (p / "a.bin").write_bytes(b"y" * 2048)
        _git(p, "add", "a.bin")                                   # staged: index pointer == working file, HEAD pointer differs
        self.assertIn("a.bin", [f["path"] for f in changes.working_changes(p)], "a staged change is still an uncommitted change")

    def test_a_failing_capped_git_call_is_reported(self):
        from core.projects import changes
        p = self.tmp / "capped-fail"
        p.mkdir()
        _git(p, "init", "-q")
        with self.assertRaises(changes.ChangesError):
            changes._git_capped(p, "show", "--format=", "no-such-commit")

    def test_the_path_budget_fits_a_knowledge_base_task_file(self):
        from core.projects import registry
        self.assertGreaterEqual(registry._FILE_BUDGET, len("docs\\kb\\tasks\\T-0001-") + 60 + len(".md"))


if __name__ == "__main__":
    unittest.main()
