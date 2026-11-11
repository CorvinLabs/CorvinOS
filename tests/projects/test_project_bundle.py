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

        swapped = []

        def hook(path, flags, *a, **k):
            # the walk opens by name relative to a directory descriptor (REVIEW-0063 M1): swap the real file by its full path
            target = root / "swap.txt"
            if str(path).endswith("swap.txt") and not target.is_symlink():
                os.unlink(target)
                os.symlink(self.tmp / "secret.txt", target)
                swapped.append(1)
            return real_open(path, flags, *a, **k)
        with mock.patch.object(bundle.os, "open", hook):
            z, _ = self.build([pid], git_history=False)
        self.assertTrue(swapped, "positive control: the swap happened at open time")
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
        self.assertIs(bundle.NEVER_LIST, bundle_rules.export_left_out)

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

    def test_a_sparse_tail_is_stored_so_the_export_passes_its_own_v1_ratio_rule(self):
        pid, root = self.project()
        with open(root / "disk.img", "wb") as f:
            f.write(os.urandom(1 << 20))
            f.truncate(201 << 20)                         # 1 MiB random, then 200 MiB of zeros: the probe says deflate
        with open(root / "zeros.img", "wb") as f:
            f.truncate(5 << 20)                           # all zeros: the probe says store
        z, _ = self.build([pid], git_history=False)
        with zipfile.ZipFile(z) as zf:
            types = {i.filename.rsplit("/", 1)[-1]: i.compress_type for i in zf.infolist()}
        self.assertEqual((types["disk.img"], types["zeros.img"]), (zipfile.ZIP_STORED, zipfile.ZIP_STORED))
        self.assertTrue(bundle.validate(z, None)["ok"])

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



class HistoryScan(BundleBase):
    """T-0410 / ADR-2323 Decision 12: never-list paths and secret shapes anywhere in the exported history refuse."""

    def _refused(self, pid):
        with self.assertRaises(bundle.BundleRefused) as exc:
            self.build([pid], git_history=True)
        self.assertEqual(list(self.out.iterdir()), [])
        return exc.exception

    # AC1 + AC2 + AC3
    def test_a_deleted_env_file_in_history_refuses_and_exports_without_history(self):
        pid, root = self.project()
        (root / ".gitignore").unlink()
        (root / ".env").write_text("TOKEN=1\n")
        _gitc(root, "add", "-f", ".env")
        _gitc(root, "commit", "-q", "-m", "add env")
        sha = _gitc(root, "rev-parse", "HEAD").strip()
        _gitc(root, "rm", "-q", "--cached", ".env")
        (root / ".env").unlink()
        _commit_all(root, "remove env")
        err = self._refused(pid)
        self.assertEqual((err.reason_class, err.path), ("secret_in_history", ".env"))
        self.assertEqual(err.message, ".env in the git history looks like it contains a secret. Export without git history, "
                                      "or remove it from the history yourself.")
        self.assertNotIn(sha[:7], err.message)
        z, _ = self.build([pid], name="nohist.zip", git_history=False)
        self.assertTrue(bundle.validate(z, None)["ok"])
        self.assertEqual(bundle.preview("_default", [pid], bundle.Parts())["projects"][0]["history_findings"], 1)

    def test_a_jwt_in_an_old_version_of_a_file_refuses_naming_only_the_path(self):
        pid, root = self.project()
        (root / "notes.md").write_text(f"token {JWT}\n")
        _commit_all(root, "oops")
        (root / "notes.md").write_text("clean now\n")
        _commit_all(root, "clean")
        err = self._refused(pid)
        self.assertEqual((err.reason_class, err.path), ("secret_in_history", "notes.md"))
        for piece in (JWT[:10], JWT[-10:]):
            self.assertNotIn(piece, err.message)
            self.assertNotIn(piece, str(err))

    def test_tracked_fragment_files_under_dot_corvin_do_not_refuse_the_history(self):
        pid, root = self.project("Agentic", template="agentic")
        (root / ".corvin" / "forge").mkdir(parents=True, exist_ok=True)
        (root / ".corvin" / "forge" / "README.md").write_text("forge\n")
        _commit_all(root, "tracked")
        z, _ = self.build([pid], git_history=True)
        self.assertTrue(bundle.validate(z, None)["ok"])

    # AC4
    def test_a_planted_filter_driver_never_runs_during_the_scan(self):
        pid, root = self.project()
        flag = self.tmp / "filter-ran"
        (root / ".gitattributes").write_text("* filter=evil diff=evil\n")
        (root / "a.txt").write_text("a\n")
        _commit_all(root, "attrs")
        for k in ("clean", "smudge", "process"):
            _gitc(root, "config", f"filter.evil.{k}", f"sh -c 'touch {flag}; cat'")
        _gitc(root, "config", "diff.evil.textconv", f"sh -c 'touch {flag}; cat'")
        _gitc(root, "config", "core.fsmonitor", f"sh -c 'touch {flag}'")
        z, _ = self.build([pid], git_history=True)
        bundle.preview("_default", [pid], bundle.Parts())
        self.assertFalse(flag.exists())
        self.assertIn(f"projects/{pid}/{bundle_rules.HISTORY_BUNDLE}", self.names(z))

    # AC4a (N4)
    def test_a_token_only_on_a_remote_tracking_ref_does_not_travel_and_on_a_branch_refuses(self):
        up = self.tmp / "upstream"
        up.mkdir()
        _gitc(up, "init", "-q", "-b", "main")
        (up / "u.txt").write_text("u\n")
        _commit_all(up, "u")
        _gitc(up, "checkout", "-q", "-b", "x")
        (up / "leak.md").write_text(f"{JWT}\n")
        _commit_all(up, "leak")
        _gitc(up, "checkout", "-q", "main")
        pid, root = self.project()
        _gitc(root, "remote", "add", "origin", str(up))
        _gitc(root, "fetch", "-q", "origin")
        z, _ = self.build([pid], git_history=True)
        with zipfile.ZipFile(z) as zf:
            (self.tmp / "h.bundle").write_bytes(zf.read(f"projects/{pid}/{bundle_rules.HISTORY_BUNDLE}"))
        heads = _gitc(self.tmp, "bundle", "list-heads", str(self.tmp / "h.bundle"))
        self.assertNotIn("refs/remotes", heads)
        check = self.tmp / "check"
        _gitc(self.tmp, "init", "-q", str(check))
        _gitc(check, "bundle", "unbundle", str(self.tmp / "h.bundle"))
        blobs = subprocess.run(["git", "-C", str(check), "cat-file", "--batch-all-objects", "--batch"], capture_output=True).stdout
        self.assertGreater(len(blobs), 0)
        self.assertNotIn(JWT.encode(), blobs)
        z.unlink()
        _gitc(root, "branch", "x", "origin/x")
        err = self._refused(pid)
        self.assertEqual((err.reason_class, err.path), ("secret_in_history", "leak.md"))

    def test_the_history_budget_names_the_git_history_part(self):
        pid, root = self.project()
        with mock.patch.dict(bundle.LIMITS_MUTABLE, {"max_history_objects": 2}):
            err = self._refused(pid)
        self.assertEqual((err.reason_class, err.part), ("too_large", "git_history"))


# ── T-0398: validation V1-V6 and the hostile corpus ──────────────────────────────────────────────────────────────────────

import builtins  # noqa: E402
import stat as _stat  # noqa: E402

KNOWN = {"templates": {"plain@1", "agentic@1", "demo@1"}, "features": {"core", "kb", "tasks", "claude", "acs", "videos"}}


def _manifest_bytes(pid: str, **extra) -> bytes:
    data = {"schema": 1, "id": pid, "title": pid.title(), "template": "plain@1", "docs_mode": "plain",
            "classification": "internal", "features": {"core": True}, **extra}
    return manifest.dumps({k: v for k, v in data.items() if v is not None}).encode()


def make_bundle(path: Path, projects: dict[str, dict[str, bytes]] | None = None, *, extra=(), list_extra=True,
                envelope_patch=None, raw_envelope: bytes | None = None, compression=zipfile.ZIP_DEFLATED) -> Path:
    """A valid bundle (every file listed) plus ``extra`` raw entries ``(name_or_ZipInfo, bytes)``, listed unless
    ``list_extra`` is False, so a defect isolates the stage that must refuse it."""
    projects = projects if projects is not None else {"alpha": {"README.md": b"# Alpha\n" + MARKER.encode()}}
    env_projects = []
    entries: list[tuple] = []
    for pid, files in projects.items():
        files = {"project.yaml": _manifest_bytes(pid), **files}
        listed = []
        for rel, data in files.items():
            name = f"projects/{pid}/{rel}"
            entries.append((name, data))
            listed.append({"path": name, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)})
        env_projects.append({"id": pid, "title": pid.title(), "template": "plain@1", "role": "", "classification": "internal",
                             "docs_mode": "plain", "features": {"core": True}, "export_id": bundle.new_ulid(),
                             "source_project_uid": "ab" * 16,
                             "parts": [{"name": n, "included": n == "files"} for n in bundle.PART_NAMES],
                             "left_out": [], "files": listed, "counts": {"files": len(listed), "bytes": sum(x["size"] for x in listed)}})
    for item, data in extra:
        name = item.filename if isinstance(item, zipfile.ZipInfo) else item
        entries.append((item, data))
        if list_extra and not name.endswith("/"):
            pid = name.split("/")[1] if name.startswith("projects/") and name.count("/") >= 2 else None
            target = next((p for p in env_projects if p["id"] == pid), env_projects[0])
            target["files"].append({"path": name, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)})
            target["counts"] = {"files": len(target["files"]), "bytes": sum(x["size"] for x in target["files"])}
    env = {"format": bundle.FORMAT, "format_version": 1, "created_at": "2026-11-11T00:00:00Z",
           "exporter": {"app": "corvinos", "version": "2.0.0"}, "origin": "unverified", "projects": env_projects}
    if envelope_patch:
        envelope_patch(env)
    with zipfile.ZipFile(path, "w", compression) as zf:
        for item, data in entries:
            zf.writestr(item, data)
        zf.writestr(bundle.ENVELOPE_NAME, raw_envelope if raw_envelope is not None else json.dumps(env).encode())
    return path


class WriteSpy:
    """Every write-mode open and every new path under a root while validating."""

    def __init__(self, root: Path):
        self.root, self.writes = root, []

    def _tree(self):
        out = {}
        for dp, dns, fns in os.walk(self.root):
            for n in dns + fns:
                p = os.path.join(dp, n)
                with contextlib.suppress(OSError):
                    st = os.lstat(p)
                    out[p] = (st.st_size, st.st_mtime_ns)
        return out

    def __enter__(self):
        self.before = self._tree()
        real_open, real_os_open = builtins.open, os.open

        def spy_open(f, mode="r", *a, **k):
            if any(c in str(mode) for c in "wax+"):
                self.writes.append(str(f))
            return real_open(f, mode, *a, **k)

        def spy_os_open(p, flags, *a, **k):
            if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT):
                self.writes.append(str(p))
            return real_os_open(p, flags, *a, **k)
        self._p = [mock.patch.object(builtins, "open", spy_open), mock.patch.object(os, "open", spy_os_open)]
        for p in self._p:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in self._p:
            p.stop()
        self.after = self._tree()
        return False


import contextlib  # noqa: E402


class Validate(BundleBase):
    def check(self, path: Path, stage: str | None, *, known=KNOWN, contains: str | None = None) -> dict:
        with WriteSpy(self.tmp) as spy:
            rep = bundle.validate(path, known)
        self.assertEqual(spy.writes, [], "validation wrote")
        self.assertEqual(spy.before, spy.after, "validation changed the sandbox")
        blob = json.dumps(rep)
        self.assertNotIn(MARKER, blob)
        self.assertNotIn(JWT[:16], blob)
        if stage is None:
            self.assertTrue(rep["ok"], rep)
        else:
            self.assertFalse(rep["ok"], rep)
            self.assertEqual((rep["stage"], rep["reason_class"]), (stage, stage), rep)
            if contains:
                self.assertIn(contains, rep["message"])
        return rep

    def z(self, name="h.zip", **kw) -> Path:
        return make_bundle(self.tmp / name, **kw)

    # AC1 + AC6 (N3)
    def test_build_validate_round_trip_incl_the_acs_tenants_and_submodule_project(self):
        pid, root = self.project("Agentic", template="agentic")
        (root / ".corvin" / "forge").mkdir(parents=True, exist_ok=True)
        (root / ".corvin" / "forge" / "README.md").write_text("forge\n")
        (root / "src" / "tenants").mkdir(parents=True)
        (root / "src" / "tenants" / "a.txt").write_text("a\n")
        (root / "vendor" / "lib").mkdir(parents=True)
        (root / "vendor" / "lib" / ".git").write_text("gitdir: ../../.git/modules/lib\n")
        (root / "sub").mkdir()
        (root / "sub" / "project.yaml").write_text("name: not a corvin manifest\n")
        _commit_all(root, "tracked")
        pid2, _ = self.project("Beta")
        z, res = self.build([pid, pid2], git_history=True)
        rep = self.check(z, None)
        self.assertEqual({p["id"]: p["counts"] for p in rep["projects"]},
                         {p["id"]: p["counts"] for p in res.envelope["projects"]})
        self.assertEqual(rep["origin"], "unverified")
        self.assertIs(bundle.V1_PROTECTED, bundle_rules.protected_entry)
        # REVIEW-0063 m9: the export's list is V1's list plus the bundle's own .corvin-export name (one function builds on the other)
        self.assertIs(bundle.NEVER_LIST, bundle_rules.export_left_out)

    def test_bundle_py_carries_no_protected_name_literal_of_its_own(self):
        src = Path(bundle.__file__).read_text(encoding="utf-8")
        for comp in registry.PROTECTED_COMPONENTS | {".mcp.json", "ENGINE.json", ".env"}:
            self.assertNotIn(f'"{comp}"', src, comp)
            self.assertNotIn(f"'{comp}'", src, comp)
        self.assertIn("bundle_rules", src)

    def test_a_valid_hand_made_bundle_passes_with_the_must_pass_cases(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as inner:
            inner.writestr("t.txt", JWT + MARKER)
        z = self.z(extra=[("projects/alpha/data/archive.zip", buf.getvalue()),
                          ("projects/alpha/sub/project.yaml", b"name: a docker-compose like file\nversion: 3\n"),
                          ("projects/alpha/docs/", b"")])
        rep = self.check(z, None)
        self.assertEqual(rep["projects"][0]["id"], "alpha")
        for pid in ("import", "trash", "files"):
            self.check(make_bundle(self.tmp / f"{pid}.zip", {pid: {"a.txt": b"x"}}), None)

    def test_an_unknown_template_is_a_warning_not_a_refusal(self):
        z = self.z(projects={"alpha": {"project.yaml": _manifest_bytes("alpha", template="mystery@9")}})
        rep = self.check(z, None)
        self.assertTrue(rep["projects"][0]["warnings"])
        self.assertTrue(any("mystery@9" in w for w in rep["projects"][0]["warnings"]))

    # AC2 - container
    def test_container_corpus(self):
        def zi(name, mode=None):
            i = zipfile.ZipInfo(name)
            if mode is not None:
                i.external_attr = mode << 16
            return i
        cases = {
            "zip slip": [("projects/alpha/../../x", b"x")],
            "absolute": [("/projects/alpha/x", b"x")],
            "backslash": [("projects\\alpha\\x", b"x")],
            "drive letter": [("C:/projects/alpha/x", b"x")],
            "symlink entry": [(zi("projects/alpha/link", _stat.S_IFLNK | 0o777), b"/etc/passwd")],
            "duplicate": [("projects/alpha/d.txt", b"1"), ("projects/alpha/d.txt", b"2")],
            "casefold duplicate": [("projects/alpha/A.txt", b"1"), ("projects/alpha/a.txt", b"2")],
            "NFC duplicate": [("projects/alpha/caf\u00e9.txt", b"1"), ("projects/alpha/cafe\u0301.txt", b"2")],
            "con.txt": [("projects/alpha/con.txt", b"x")],
            "aux": [("projects/alpha/aux", b"x")],
            "ADS colon": [("projects/alpha/a:b", b"x")],
            "trailing dot": [("projects/alpha/name.", b"x")],
            "outside projects": [("README.md", b"x")],
            "bad id dir": [("projects/Bad_Id/x", b"x")],
            "ratio bomb": [("projects/alpha/zeros.bin", b"\0" * (8 << 20))],
            "too deep": [("projects/alpha/" + "d/" * 70 + "x", b"x")],
        }
        for label, extra in cases.items():
            with self.subTest(label):
                self.check(self.z(name=f"c-{abs(hash(label))}.zip", extra=extra), "container",
                           contains="This file is not a valid project bundle (")

    def test_protected_entries_are_container_refusals_naming_the_entry(self):
        for rel in (".git/config", ".git/hooks/post-checkout", "sub/.git/config", ".GIT/config", ".corvin/local/x",
                    "session_ledger/x", "tenants/x", ".mcp.json", "ENGINE.json", "docs/kb/graph/entities.jsonl", ".env",
                    ".corvin-export/other", ".corvin-export/git/evil.bundle"):
            with self.subTest(rel):
                rep = self.check(self.z(name="p.zip", extra=[(f"projects/alpha/{rel}", MARKER.encode())]), "container")
                self.assertEqual(rep["message"], "This file is not a valid project bundle (it contains a protected entry: "
                                                 f"projects/alpha/{rel}).")

    def test_too_many_entries(self):
        p = self.tmp / "many.zip"
        with zipfile.ZipFile(p, "w", zipfile.ZIP_STORED) as zf:
            for i in range(50_001):
                zf.writestr(f"projects/alpha/f{i}", b"")
        self.check(p, "container")

    def test_not_a_zip_and_too_large_file(self):
        p = self.tmp / "nz.zip"
        p.write_bytes(b"not a zip " + MARKER.encode())
        self.check(p, "container")
        z = self.z()
        with mock.patch.dict(bundle.LIMITS_MUTABLE, {"max_compressed_bytes": 100}):
            self.check(z, "container")

    # AC2 - envelope
    def test_envelope_corpus(self):
        def drop(k):
            return lambda e: e.pop(k)
        cases = {
            "missing": dict(raw_envelope=None, extra=[], envelope_patch=None),
            "not json": dict(raw_envelope=b"{not json" + MARKER.encode()),
            "4 MiB + 1": dict(raw_envelope=os.urandom((4 << 20) + 1)),          # incompressible: not a ratio refusal
            "wrong format": dict(envelope_patch=lambda e: e.update(format="corvin.forge-bundle")),
            "version 2": dict(envelope_patch=lambda e: e.update(format_version=2)),
            "no projects": dict(envelope_patch=lambda e: e.update(projects=[])),
            "unknown key": dict(envelope_patch=lambda e: e.update(extra_key=1)),
            "no origin": dict(envelope_patch=drop("origin")),
            "bad uid": dict(envelope_patch=lambda e: e["projects"][0].update(source_project_uid="xyz")),
            "bad sha": dict(envelope_patch=lambda e: e["projects"][0]["files"][0].update(sha256="zz")),
            "counts lie": dict(envelope_patch=lambda e: e["projects"][0]["counts"].update(files=99)),
            "id not its directory": dict(envelope_patch=lambda e: e["projects"][0].update(id="beta")),
            "history without head": dict(envelope_patch=lambda e: e["projects"][0]["parts"][2].update(included=True)),
            "bad left_out reason": dict(envelope_patch=lambda e: e["projects"][0]["left_out"].append({"path": "x", "reason": "nope"})),
        }
        for label, kw in cases.items():
            with self.subTest(label):
                if label == "missing":
                    p = self.tmp / "m.zip"
                    with zipfile.ZipFile(p, "w") as zf:
                        zf.writestr("projects/alpha/project.yaml", _manifest_bytes("alpha"))
                else:
                    p = self.z(name="e.zip", **kw)
                self.check(p, "envelope")
        many = {f"p{i}": {"a.txt": b"x"} for i in range(21)}
        self.check(make_bundle(self.tmp / "21.zip", many), "envelope")

    # AC2 - integrity
    def test_integrity_corpus(self):
        def bad_hash(e):
            e["projects"][0]["files"][1]["sha256"] = "0" * 64

        def bad_size(e):
            e["projects"][0]["files"][1]["size"] += 1
            e["projects"][0]["counts"]["bytes"] += 1

        def missing(e):
            f = {"path": "projects/alpha/ghost.txt", "sha256": "0" * 64, "size": 1}
            e["projects"][0]["files"].append(f)
            e["projects"][0]["counts"] = {"files": len(e["projects"][0]["files"]),
                                          "bytes": sum(x["size"] for x in e["projects"][0]["files"])}
        for label, kw in {"unlisted": dict(extra=[("projects/alpha/extra.txt", b"x")], list_extra=False),
                          "hash mismatch": dict(envelope_patch=bad_hash), "size mismatch": dict(envelope_patch=bad_size),
                          "missing listed": dict(envelope_patch=missing)}.items():
            with self.subTest(label):
                self.check(self.z(name="i.zip", **kw), "integrity",
                           contains="The bundle's contents do not match its manifest.")

    # AC2 - manifest
    def test_manifest_corpus(self):
        cases = {
            "tenant key": {"alpha": {"project.yaml": _manifest_bytes("alpha", tenant="evil")}},
            "unknown docs_mode": {"alpha": {"project.yaml": _manifest_bytes("alpha", docs_mode="weird")}},
            "reserved id corvin": {"corvin": {"a.txt": b"x"}},
            "not yaml": {"alpha": {"project.yaml": b"::: [" + MARKER.encode()}},
        }
        for label, projects in cases.items():
            with self.subTest(label):
                self.check(make_bundle(self.tmp / "m.zip", projects), "manifest",
                           contains="The project description in the bundle is invalid.")
        rep = self.check(self.z(name="n.zip", extra=[("projects/alpha/sub/project.yaml", _manifest_bytes("inner", tenant="_default"))]),
                         "manifest")
        self.assertEqual(rep["message"], "The bundle contains a project inside the project; it was not imported.")

    # AC2 - secrets
    def test_planted_token_is_a_secrets_refusal_naming_the_path(self):
        rep = self.check(self.z(extra=[("projects/alpha/notes/creds.md", f"use {JWT} {MARKER}".encode())]), "secrets")
        self.assertEqual(rep["message"],
                         "projects/alpha/notes/creds.md in the bundle looks like it contains a secret; the bundle was not imported.")

    def test_stage_order_integrity_before_secrets(self):
        def bad(e):
            e["projects"][0]["files"][-1]["sha256"] = "0" * 64
        self.check(self.z(extra=[("projects/alpha/t.md", JWT.encode())], envelope_patch=bad), "integrity")

    # AC5
    @unittest.skipUnless(POSIX and shutil.which("git"), "resource.getrusage")
    def test_a_500_mib_bundle_validates_with_flat_memory_and_no_temp_file(self):
        big = self.tmp / "big.zip"
        chunk = b"\0" * (1 << 20)
        files = []
        with zipfile.ZipFile(big, "w", zipfile.ZIP_STORED, allowZip64=True) as zf:
            zf.writestr("projects/alpha/project.yaml", _manifest_bytes("alpha"))
            files.append(("projects/alpha/project.yaml", _manifest_bytes("alpha")))
            sizes = [53] * 8 + [40]
            for i, mb in enumerate(sizes):
                name = f"projects/alpha/blob{i}.bin" if i < 8 else "projects/alpha/text.txt"
                piece = chunk if i < 8 else (b"lorem ipsum dolor sit amet\n" * 40000)[: 1 << 20]
                h = hashlib.sha256()
                info = zipfile.ZipInfo(name)
                info.file_size = mb << 20
                with zf.open(info, "w", force_zip64=True) as w:
                    for _ in range(mb):
                        w.write(piece)
                        h.update(piece)
                files.append((name, (h.hexdigest(), mb << 20)))
            listed = [{"path": n, "sha256": hashlib.sha256(d).hexdigest(), "size": len(d)} if isinstance(d, bytes)
                      else {"path": n, "sha256": d[0], "size": d[1]} for n, d in files]
            env = {"format": bundle.FORMAT, "format_version": 1, "created_at": "2026-11-11T00:00:00Z",
                   "exporter": {"app": "corvinos", "version": "2.0.0"}, "origin": "unverified",
                   "projects": [{"id": "alpha", "title": "Alpha", "template": "plain@1", "role": "", "classification": "internal",
                                 "docs_mode": "plain", "features": {}, "export_id": bundle.new_ulid(), "source_project_uid": "cd" * 16,
                                 "parts": [{"name": n, "included": n == "files"} for n in bundle.PART_NAMES], "left_out": [],
                                 "files": listed, "counts": {"files": len(listed), "bytes": sum(x["size"] for x in listed)}}]}
            zf.writestr(bundle.ENVELOPE_NAME, json.dumps(env))
        self.assertGreater(big.stat().st_size, 450 << 20)
        self.assertLess(big.stat().st_size, 500 << 20)
        code = ("import json,resource,sys,os; sys.path.insert(0, os.getcwd()); from core.projects import bundle;"
                "b=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss; r=bundle.validate(__import__('pathlib').Path(sys.argv[1]), None);"
                "a=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss; print(json.dumps({'ok': r['ok'], 'delta_kib': a-b, 'before_kib': b, 'after_kib': a, 'files': r.get('counts'), 'r': r.get('message')}))")
        before = set(os.listdir(tempfile_dir())) if os.path.isdir(tempfile_dir()) else set()
        env = dict(os.environ, TMPDIR=str(self.tmp / "tmpdir"))
        (self.tmp / "tmpdir").mkdir()
        r = subprocess.run([sys.executable, "-c", code, str(big)], capture_output=True, text=True, env=env,
                           cwd=str(Path(bundle.__file__).resolve().parents[2]), timeout=600)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        out = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertTrue(out["ok"], out)
        self.assertLess(out["delta_kib"], 64 * 1024, out)
        self.assertEqual(os.listdir(self.tmp / "tmpdir"), [])
        self.assertEqual(out["files"]["files"], 10)                         # positive control: every entry was read
        print(f"\n500 MiB validate: peak RSS {out['before_kib']} -> {out['after_kib']} KiB (delta {out['delta_kib']} KiB), "
              f"{out['files']['bytes'] >> 20} MiB hashed")


def tempfile_dir() -> str:
    import tempfile
    return tempfile.gettempdir()


if __name__ == "__main__":
    unittest.main()
