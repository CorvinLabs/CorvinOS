"""ADR-2323 / PLAN-0972 phase 3-4 - the project bundle: build (T-0396, T-0410) and validation V1-V6 (T-0398).

Pure: projects are created through ``projects_service`` in a sandboxed ``CORVIN_HOME`` (HOME and XDG_DATA_HOME inside the
test's temp dir), git is real, every git call of the code under test goes through ``changes.neutral_git_args`` +
``neutral_git_env``. Nothing touches the live install.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import unittest
import zipfile
from pathlib import Path, PurePosixPath
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0] / "e2e"))
from _projects_fixtures import Sandbox, small_catalogue, write_template  # noqa: E402

from core.projects import bundle, bundle_rules, manifest, registry  # noqa: E402

POSIX = os.name != "nt"
JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
MARKER = "MARKER-7f3c9e1d-never-in-a-bundle"


def _gitc(repo: Path, *a: str, check: bool = True) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@example.invalid", GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    r = subprocess.run(["git", "-C", str(repo), "-c", "commit.gpgSign=false", "-c", "core.hooksPath=" + os.devnull, *a],
                       capture_output=True, text=True, env=env)
    if check and r.returncode != 0:
        raise AssertionError(f"git {a}: {r.stderr}")
    return r.stdout


def _commit_all(repo: Path, msg: str = "c") -> None:
    _gitc(repo, "add", "-A")
    _gitc(repo, "commit", "-q", "-m", msg, "--allow-empty")


class BundleBase(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.tmp = self.sb.tmp
        self.src = small_catalogue(self.tmp / "src")
        write_template(self.src, "plain", ["core"])
        write_template(self.src, "agentic", ["core", "claude", "acs"], ["core", "claude", "acs"])
        from corvin_console import projects_service as svc
        self.svc = svc
        self._p = [mock.patch.object(svc, "template_source", lambda t: self.src),
                   mock.patch.object(svc, "plugin_state", lambda t, p: "enabled")]
        for p in self._p:
            p.start()
        self.sb.set_root()
        self.out = self.tmp / "out"
        self.out.mkdir()

    def tearDown(self):
        for p in self._p:
            p.stop()
        self.sb.close()

    def project(self, title="Alpha", template="plain", commit=True) -> tuple[str, Path]:
        plan = self.svc.plan_project("_default", template=template, title=title, storage="root")
        out = self.svc.create_project("_default", plan["plan_id"])
        pid = out["project"]["id"]
        root = Path(plan["location"])
        if commit:
            _commit_all(root, "initial")
        return pid, root

    def build(self, ids, name="b.zip", **parts) -> tuple[Path, bundle.BuildResult]:
        out = self.out / name
        res = bundle.build("_default", list(ids), bundle.Parts(**parts), out=out)
        return out, res

    @staticmethod
    def names(zpath: Path) -> list[str]:
        with zipfile.ZipFile(zpath) as zf:
            return zf.namelist()

    @staticmethod
    def envelope(zpath: Path) -> dict:
        with zipfile.ZipFile(zpath) as zf:
            return json.loads(zf.read(bundle.ENVELOPE_NAME))

    @staticmethod
    def left_out(env: dict, pid: str) -> dict[str, str]:
        p = [x for x in env["projects"] if x["id"] == pid][0]
        return {r["path"]: r["reason"] for r in p["left_out"]}


class Build(BundleBase):
    # AC1
    def test_never_list_paths_never_enter_and_every_one_is_named_with_its_reason(self):
        pid, root = self.project()
        (root / ".env").write_text("A=1\n")
        (root / "id_rsa").write_text("not a real key\n")
        (root / ".mcp.json").write_text("{}\n")
        (root / ".corvin" / "local").mkdir(parents=True, exist_ok=True)
        (root / ".corvin" / "local" / "x").write_text("x\n")
        (root / "docs" / "kb" / "graph").mkdir(parents=True)
        (root / "docs" / "kb" / "graph" / "entities.jsonl").write_text("{}\n")
        (root / "docs" / "kb" / "notes.md").write_text("kept\n")
        (root / "tools").mkdir()
        (root / "tools" / "ENGINE.json").write_text('{"engine": "copy"}\n')
        z, res = self.build([pid], git_history=False)
        names = self.names(z)
        for n in names:
            rel = n.split("/", 2)[2] if n.startswith("projects/") else ""
            self.assertIsNone(bundle_rules.protected_entry(rel) if rel else None, n)
            self.assertNotIn("/.git/", n + "/")
        self.assertIn(f"projects/{pid}/docs/kb/notes.md", names)
        lo = self.left_out(self.envelope(z), pid)
        self.assertEqual(lo[".env"], "secret_name")
        self.assertEqual(lo["id_rsa"], "secret_name")
        self.assertEqual(lo[".mcp.json"], "machine_local")
        self.assertEqual(lo[".corvin"], "machine_local")
        self.assertEqual(lo[".git"], "git_internal")
        self.assertEqual(lo["docs/kb/graph"], "graph_cache")
        self.assertEqual(lo["tools/ENGINE.json"], "engine_copy")

    # AC2
    def test_project_yaml_travels_without_tenant_and_otherwise_unchanged(self):
        pid, root = self.project()
        z, _ = self.build([pid], git_history=False)
        with zipfile.ZipFile(z) as zf:
            inside = manifest.yaml.safe_load(zf.read(f"projects/{pid}/project.yaml"))
        src = manifest.yaml.safe_load((root / "project.yaml").read_text())
        self.assertNotIn("tenant", inside)
        src.pop("tenant")
        self.assertEqual(inside, src)

    # AC3
    def test_git_history_bundle_verifies_and_docs_repo_too_and_no_repo_is_not_included(self):
        pid, root = self.project()
        (root / "docs").mkdir(exist_ok=True)
        (root / "docs" / "a.md").write_text("doc\n")
        _gitc(root / "docs", "init", "-q", "-b", "trunk")
        _commit_all(root / "docs", "docs")
        z, res = self.build([pid], git_history=True)
        names = self.names(z)
        hb = f"projects/{pid}/{bundle_rules.HISTORY_BUNDLE}"
        dhb = f"projects/{pid}/{bundle_rules.DOCS_HISTORY_BUNDLE}"
        self.assertIn(hb, names)
        self.assertIn(dhb, names)
        env = self.envelope(z)["projects"][0]
        self.assertEqual(env["git"], {"head": "refs/heads/main", "docs_head": "refs/heads/trunk"})
        self.assertTrue([p for p in env["parts"] if p["name"] == "git_history"][0]["included"])
        verify = self.tmp / "verify"
        _gitc(self.tmp, "init", "-q", str(verify))
        with zipfile.ZipFile(z) as zf:
            for n in (hb, dhb):
                (self.tmp / "h.bundle").write_bytes(zf.read(n))
                _gitc(verify, "bundle", "verify", str(self.tmp / "h.bundle"))
        # no repository: the part is not included
        pid2, root2 = self.project("Beta", commit=False)
        shutil.rmtree(root2 / ".git")
        z2, _ = self.build([pid2], name="c.zip", git_history=True)
        p2 = self.envelope(z2)["projects"][0]
        self.assertFalse([p for p in p2["parts"] if p["name"] == "git_history"][0]["included"])
        self.assertNotIn("git", p2)
        self.assertFalse(any(bundle_rules.EXPORT_DIR in n for n in self.names(z2)))

    def test_detached_head_leaves_the_history_out_with_the_warning(self):
        pid, root = self.project()
        _gitc(root, "checkout", "-q", "--detach")
        z, res = self.build([pid], git_history=True)
        self.assertIn("Git history needs a branch checked out", res.warnings)
        part = [p for p in self.envelope(z)["projects"][0]["parts"] if p["name"] == "git_history"][0]
        self.assertFalse(part["included"])

    # AC4
    def test_planted_token_refuses_naming_the_path_and_leaves_no_zip(self):
        pid, root = self.project()
        (root / "notes").mkdir()
        (root / "notes" / "creds.md").write_text(f"deploy with {JWT}\n")
        with self.assertRaises(bundle.BundleRefused) as exc:
            self.build([pid], git_history=False)
        self.assertEqual(exc.exception.reason_class, "secret_shape")
        self.assertEqual(exc.exception.path, "notes/creds.md")
        text = str(exc.exception) + exc.exception.message
        self.assertIn("notes/creds.md", text)
        self.assertNotIn(JWT[:12], text)
        self.assertEqual(list(self.out.iterdir()), [])

    def test_a_nested_zip_with_a_token_is_a_file_and_not_opened(self):
        pid, root = self.project()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as inner:
            inner.writestr("t.txt", JWT)
        (root / "data.zip").write_bytes(buf.getvalue())
        z, _ = self.build([pid], git_history=False)
        self.assertIn(f"projects/{pid}/data.zip", self.names(z))

    # AC5
    def test_envelope_hashes_reverify_and_format_fields_and_21_projects_refuse(self):
        pid, root = self.project()
        (root / "big.bin").write_bytes(os.urandom(300_000))
        z, res = self.build([pid], git_history=True)
        env = self.envelope(z)
        self.assertEqual((env["format"], env["format_version"], env["origin"]), ("corvin.project-bundle", 1, "unverified"))
        self.assertEqual(self.names(z)[-1], bundle.ENVELOPE_NAME)          # written last
        files = env["projects"][0]["files"]
        with zipfile.ZipFile(z) as zf:
            listed = {f["path"] for f in files}
            self.assertEqual(listed, set(zf.namelist()) - {bundle.ENVELOPE_NAME})
            for f in files:
                data = zf.read(f["path"])
                self.assertEqual((len(data), hashlib.sha256(data).hexdigest()), (f["size"], f["sha256"]))
        self.assertEqual(env["projects"][0]["counts"]["files"], len(files))
        with self.assertRaises(bundle.BundleRefused) as exc:
            bundle.build("_default", [f"p{i}" for i in range(21)], bundle.Parts(), out=self.out / "x.zip")
        self.assertEqual(exc.exception.reason_class, "too_many")

    def test_the_project_limits_are_decision_9(self):
        from tests.projects.test_bundle_common import PROJECT_LIMITS
        for k in ("max_compressed_bytes", "max_uncompressed_bytes", "max_entries", "max_envelope_bytes",
                  "max_compression_ratio", "ratio_check_min_bytes", "max_name_bytes"):
            self.assertEqual(bundle.LIMITS[k], PROJECT_LIMITS[k], k)
        self.assertEqual((bundle.LIMITS["max_projects"], bundle.LIMITS["max_depth"]), (20, 64))

    def test_too_large_names_the_largest_optional_part(self):
        pid, root = self.project()
        (root / "node_modules" / "x").mkdir(parents=True)
        (root / "node_modules" / "x" / "a.js").write_bytes(b"a" * 5000)
        (root / "small.txt").write_text("s")
        with mock.patch.dict(bundle.LIMITS_MUTABLE, {"max_uncompressed_bytes": 3000}):
            with self.assertRaises(bundle.BundleRefused) as exc:
                self.build([pid], git_history=False, generated=True)
        self.assertEqual((exc.exception.reason_class, exc.exception.part), ("too_large", "generated"))
        self.assertIn("Leave out generated", exc.exception.message)

    # AC6
    def test_preview_counts_equal_the_built_bundle(self):
        pid, root = self.project()
        (root / "a.txt").write_text("a")
        (root / ".env").write_text("x")
        pv = bundle.preview("_default", [pid], bundle.Parts(git_history=False))
        z, res = self.build([pid], git_history=False)
        env = self.envelope(z)["projects"][0]
        pp = pv["projects"][0]
        self.assertEqual(pp["counts"], env["counts"])
        self.assertEqual(sorted(map(tuple, map(dict.items, pp["left_out"]))), sorted(map(tuple, map(dict.items, env["left_out"]))))
        self.assertEqual(list(self.out.iterdir()), [z])                    # preview wrote nothing
        self.assertIn("Archives inside the project are not checked for secrets.", pv["notes"])

    # AC7
    def test_generated_trees_are_left_out_by_default_and_included_on_request(self):
        pid, root = self.project()
        (root / "node_modules" / "pkg").mkdir(parents=True)
        (root / "node_modules" / "pkg" / "index.js").write_text("x")
        z, _ = self.build([pid], git_history=False)
        self.assertFalse(any("node_modules" in n for n in self.names(z)))
        self.assertEqual(self.left_out(self.envelope(z), pid)["node_modules"], "generated")
        z2, _ = self.build([pid], name="g.zip", git_history=False, generated=True)
        self.assertIn(f"projects/{pid}/node_modules/pkg/index.js", self.names(z2))

    # AC8 (B2)
    @unittest.skipUnless(POSIX, "symlinks and hard links are created on POSIX here; the Windows VM runs the junction")
    def test_links_hard_links_and_junctions_never_enter_by_name_or_content(self):
        pid, root = self.project()
        outside = self.tmp / "outside"
        outside.mkdir()
        (outside / "credentials").write_text(MARKER + "\n")
        (outside / "dir").mkdir()
        (outside / "dir" / "f.txt").write_text(MARKER + "\n")
        (root / "notes").mkdir()
        os.symlink(outside / "credentials", root / "notes" / "a.txt")
        os.symlink(self.sb.home, root / "home-link")
        os.symlink(outside / "dir", root / "linked-dir")
        os.link(outside / "credentials", root / "hard.txt")
        (root / "junction").mkdir()
        (root / "junction" / "f.txt").write_text(MARKER + "\n")
        real_junction = bundle.dirent_is_junction
        with mock.patch.object(bundle, "dirent_is_junction", lambda e: e.name == "junction" or real_junction(e)):
            z, _ = self.build([pid], git_history=False)
        lo = self.left_out(self.envelope(z), pid)
        self.assertEqual((lo["notes/a.txt"], lo["home-link"], lo["linked-dir"], lo["hard.txt"], lo["junction"]),
                         ("link", "link", "link", "hard_link", "link"))
        with zipfile.ZipFile(z) as zf:
            for n in zf.namelist():
                self.assertFalse(any(x in n for x in ("a.txt", "home-link", "linked-dir", "hard.txt", "junction")), n)
                self.assertNotIn(MARKER.encode(), zf.read(n), n)

    # AC9
    @unittest.skipUnless(POSIX, "O_NOFOLLOW")
    def test_a_file_swapped_for_a_link_between_walk_and_open_is_left_out_unread(self):
        pid, root = self.project()
        (root / "swap.txt").write_text("innocent\n")
        (self.tmp / "secret.txt").write_text(MARKER)
        real_open = os.open

        def hook(path, flags, *a, **k):
            if str(path).endswith("swap.txt") and not os.path.islink(path):
                os.unlink(path)
                os.symlink(self.tmp / "secret.txt", path)
            return real_open(path, flags, *a, **k)
        with mock.patch.object(bundle.os, "open", hook):
            z, _ = self.build([pid], git_history=False)
        self.assertEqual(self.left_out(self.envelope(z), pid)["swap.txt"], "link")
        with zipfile.ZipFile(z) as zf:
            self.assertFalse(any(MARKER.encode() in zf.read(n) for n in zf.namelist()))

    # AC9a (N8)
    @unittest.skipUnless(POSIX, "simulates Windows on a POSIX host")
    def test_win32_identity_comes_from_lstat_never_from_entry_stat(self):
        pid, root = self.project()
        (root / "plain.txt").write_text("p\n")
        (self.tmp / "o.txt").write_text("o\n")
        os.link(self.tmp / "o.txt", root / "hl.txt")
        real_scandir = os.scandir

        class Zero:
            st_ino = st_dev = st_nlink = 0
            st_size = 0
            st_mode = 0o100644

        class Proxy:
            def __init__(self, e):
                self._e = e
                self.name, self.path = e.name, e.path

            def stat(self, follow_symlinks=True):
                return Zero()

            def __getattr__(self, k):
                return getattr(self._e, k)

        class It:
            def __init__(self, p):
                self._it = real_scandir(p)

            def __enter__(self):
                return (Proxy(e) for e in self._it)

            def __exit__(self, *a):
                self._it.close()

        with mock.patch.object(bundle, "_scandir", It), mock.patch.object(bundle, "_IS_WINDOWS", True), \
                mock.patch.object(bundle, "_O_NOFOLLOW", 0):
            z, _ = self.build([pid], git_history=False)
        self.assertIn(f"projects/{pid}/plain.txt", self.names(z))
        self.assertEqual(self.left_out(self.envelope(z), pid)["hl.txt"], "hard_link")

    # AC9b (N3)
    def test_acs_fragment_tenants_folder_and_submodule_gitfile_are_left_out_and_v1_accepts(self):
        pid, root = self.project("Agentic", template="agentic")
        (root / ".corvin" / "forge").mkdir(parents=True, exist_ok=True)
        (root / ".corvin" / "forge" / "README.md").write_text("forge\n")
        (root / "src" / "tenants").mkdir(parents=True)
        (root / "src" / "tenants" / "a.txt").write_text("a\n")
        (root / "vendor" / "lib").mkdir(parents=True)
        (root / "vendor" / "lib" / ".git").write_text("gitdir: ../../.git/modules/lib\n")
        (root / "vendor" / "lib" / "x.c").write_text("int x;\n")
        _commit_all(root, "tracked")
        z, _ = self.build([pid], git_history=True)
        lo = self.left_out(self.envelope(z), pid)
        self.assertEqual((lo[".corvin"], lo["src/tenants"], lo["vendor/lib/.git"]),
                         ("machine_local", "protected_name", "git_internal"))
        self.assertIn(f"projects/{pid}/vendor/lib/x.c", self.names(z))
        for n in self.names(z):
            if n.startswith(f"projects/{pid}/"):
                rel = n[len(f"projects/{pid}/"):]
                self.assertIsNone(bundle_rules.protected_entry(rel), n)
                self.assertFalse(bundle_rules.export_payload_problem(rel), n)
        self.assertIs(bundle.NEVER_LIST, bundle_rules.protected_entry)

    # AC9c (N4)
    def test_a_cloned_repository_bundles_only_branches_and_tags(self):
        up = self.tmp / "upstream"
        up.mkdir()
        _gitc(up, "init", "-q", "-b", "main")
        (up / "u.txt").write_text("u\n")
        _commit_all(up, "u")
        _gitc(up, "branch", "feature")
        pid, root = self.project()
        _gitc(root, "remote", "add", "origin", str(up))
        _gitc(root, "fetch", "-q", "origin")
        _gitc(root, "remote", "set-head", "origin", "main")
        (root / "w.txt").write_text("stash me\n")
        _gitc(root, "add", "w.txt")
        _gitc(root, "stash", "-q")
        _gitc(root, "notes", "add", "-m", "a note", "HEAD")
        _gitc(root, "tag", "v1")
        self.assertIn("refs/remotes/origin/main", _gitc(root, "for-each-ref", "--format=%(refname)"))
        z, _ = self.build([pid], git_history=True)
        with zipfile.ZipFile(z) as zf:
            (self.tmp / "h.bundle").write_bytes(zf.read(f"projects/{pid}/{bundle_rules.HISTORY_BUNDLE}"))
        heads = [ln.split(" ", 1)[1] for ln in _gitc(self.tmp, "bundle", "list-heads", str(self.tmp / "h.bundle")).splitlines()]
        self.assertTrue(heads)
        self.assertTrue(all(h == "HEAD" or h.startswith(("refs/heads/", "refs/tags/")) for h in heads), heads)
        self.assertIn("refs/tags/v1", heads)

    # AC10 (m6)
    def test_source_project_uid_is_random_stable_and_never_from_a_path(self):
        pid, root = self.project()
        pid2, _ = self.project("Gamma")
        z1, _ = self.build([pid], git_history=False)
        z2, _ = self.build([pid], name="2.zip", git_history=False)
        z3, _ = self.build([pid2], name="3.zip", git_history=False)
        u1, u2, u3 = (self.envelope(z)["projects"][0]["source_project_uid"] for z in (z1, z2, z3))
        self.assertRegex(u1, r"^[0-9a-f]{32}$")
        self.assertEqual(u1, u2)
        self.assertNotEqual(u1, u3)
        self.assertEqual(registry.get("_default", pid)["uid"], u1)
        for piece in str(self.tmp).split(os.sep):
            if len(piece) >= 4:
                self.assertNotIn(piece, u1)
        self.assertRegex(self.envelope(z1)["projects"][0]["export_id"], r"^[0-9A-HJKMNP-TV-Z]{26}$")

    def test_unportable_names_special_files_and_case_collisions_are_left_out(self):
        pid, root = self.project()
        if POSIX:
            (root / "a:b.txt").write_text("x")
            (root / "con.txt").write_text("x")
            (root / "Same.txt").write_text("1")
            (root / "same.txt").write_text("2")
            os.mkfifo(root / "pipe")
        z, _ = self.build([pid], git_history=False)
        if POSIX:
            lo = self.left_out(self.envelope(z), pid)
            self.assertEqual((lo["a:b.txt"], lo["con.txt"], lo["pipe"]), ("unportable_name", "unportable_name", "special_file"))
            self.assertEqual(len([n for n in self.names(z) if n.lower().endswith("/same.txt")]), 1)


if __name__ == "__main__":
    unittest.main()
