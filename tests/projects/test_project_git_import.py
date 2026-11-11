"""ADR-2323 Decision 13 step 6 / T-0411 - the git step of an import: ``bundle.git_import(tree, bundle, head)``.

Pure: temp dirs, real git, HOME (and so the global git config) sandboxed inside the test's temp dir. The source repositories
are built with the test's own git; the code under test is never allowed to clone, check out, reset, switch or override a
protocol, and every one of its git calls carries ``changes.neutral_git_args`` (a subprocess spy proves it).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0] / "e2e"))
from _projects_fixtures import sandbox_home_env  # noqa: E402

from core.projects import bundle, changes  # noqa: E402

POSIX = os.name != "nt"
FIXED = [f"{k}={v}" for k, v in changes._FIXED]
FORBIDDEN_WORDS = {"clone", "checkout", "reset", "switch", "restore"}


def git(repo: Path, *a: str, check: bool = True, env_extra: dict | None = None) -> str:
    """The TEST's own git (the user's): global config of the sandbox HOME applies, no neutralisation."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@example.invalid", GIT_CONFIG_NOSYSTEM="1", **(env_extra or {}))
    r = subprocess.run(["git", "-C", str(repo), "-c", "commit.gpgSign=false", *a], capture_output=True, text=True, env=env)
    if check and r.returncode != 0:
        raise AssertionError(f"git {a}: {r.stderr}")
    return r.stdout


class GitSpy:
    """Every ``subprocess.run`` / ``Popen`` argv of a git call while active."""

    def __init__(self):
        self.calls: list[list[str]] = []

    def __enter__(self):
        real_run, real_popen = subprocess.run, subprocess.Popen
        spy = self

        def run(argv, *a, **k):
            if isinstance(argv, (list, tuple)) and argv and os.path.basename(str(argv[0])) in ("git", "git.exe"):
                spy.calls.append([str(x) for x in argv])
            return real_run(argv, *a, **k)

        class P(real_popen):  # type: ignore[misc,valid-type]
            def __init__(self, argv, *a, **k):
                if isinstance(argv, (list, tuple)) and argv and os.path.basename(str(argv[0])) in ("git", "git.exe"):
                    spy.calls.append([str(x) for x in argv])
                super().__init__(argv, *a, **k)
        self._p = [mock.patch.object(subprocess, "run", run), mock.patch.object(subprocess, "Popen", P)]
        for p in self._p:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in self._p:
            p.stop()
        return False

    def assert_neutral(self, tc: unittest.TestCase, *, some: bool = True) -> None:
        if some:
            tc.assertTrue(self.calls, "positive control: the spy saw git calls")
        for argv in self.calls:
            joined = " ".join(argv)
            for fx in FIXED:
                tc.assertIn(fx, argv, f"missing -c {fx}: {joined}")
            words = set(argv[1:])
            tc.assertFalse(words & FORBIDDEN_WORDS, joined)
            protos = [a for a in argv if a.startswith("protocol.") and a != "protocol.allow=never"]
            tc.assertEqual(protos, [], joined)

    def subcommands(self) -> list[str]:
        out = []
        for argv in self.calls:
            rest = argv[1:]
            i = 0
            while i < len(rest):
                if rest[i] in ("-c", "-C"):
                    i += 2
                    continue
                if rest[i].startswith("-"):
                    i += 1
                    continue
                out.append(rest[i])
                break
        return out


class GitImportBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._env = sandbox_home_env(self.tmp)
        self._env.__enter__()
        self.marker = self.tmp / "FILTER-RAN"
        # a planted global config of the sandbox HOME: the filter driver runs a program that leaves a marker file
        prog = self.tmp / "touch-marker.sh"
        prog.write_text(f"#!/bin/sh\ntouch {self.marker}\nexec cat\n")
        prog.chmod(0o755)
        self.prog = prog
        (self.tmp / ".gitconfig").write_text(
            f"[filter \"touch-marker\"]\n\tsmudge = {prog}\n\tclean = {prog}\n"
            f"[core]\n\thooksPath = {self.tmp / 'hooks'}\n", encoding="utf-8")
        (self.tmp / "hooks").mkdir()
        hook = self.tmp / "hooks" / "post-checkout"
        hook.write_text(f"#!/bin/sh\ntouch {self.marker}\n")
        hook.chmod(0o755)
        self.parent = self.tmp / "place"
        self.parent.mkdir()

    def tearDown(self):
        self._env.__exit__(None, None, None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def source(self, name="src") -> Path:
        src = self.tmp / name
        src.mkdir()
        git(src, "init", "-q", "-b", "main")
        (src / "README.md").write_text("# readme\n")
        (src / "notes.txt").write_text("notes\n")
        (src / ".gitattributes").write_text("*.txt filter=touch-marker\n")
        if POSIX:
            os.symlink("/etc/passwd", src / "link")
        (src / ".env").write_text("A=1\n")                       # tracked never-list file: not in the tree
        git(src, "add", "-A")
        git(src, "commit", "-q", "-m", "one")
        (src / "README.md").write_text("# readme 2\n")
        git(src, "commit", "-q", "-am", "two")
        git(src, "branch", "feature")
        git(src, "tag", "v1")
        # the source repository's own config defines the driver too
        git(src, "config", "filter.touch-marker.smudge", str(self.prog))
        if self.marker.exists():
            self.marker.unlink()                                  # the test's own commits ran it; start clean
        return src

    def bundle_of(self, src: Path, *spec: str, name="h.bundle") -> Path:
        out = self.tmp / name
        git(src, "bundle", "create", str(out), *(spec or ("--branches", "--tags")))
        return out

    def tree_from(self, src: Path, name="proj") -> Path:
        """What extraction leaves: the regular files minus links, .git and the never-list."""
        tree = self.parent / name
        tree.mkdir()
        for p in src.rglob("*"):
            rel = p.relative_to(src)
            if rel.parts[0] == ".git" or p.is_symlink() or rel.name == ".env":
                continue
            if p.is_dir():
                (tree / rel).mkdir(parents=True, exist_ok=True)
            else:
                (tree / rel).parent.mkdir(parents=True, exist_ok=True)
                (tree / rel).write_bytes(p.read_bytes())
        (tree / ".corvin-export" / "git").mkdir(parents=True)
        return tree

    def neutral_status(self, tree: Path) -> str:
        env = changes.neutral_git_env(tree, changes._env())
        r = subprocess.run(["git", "-C", str(tree), *changes.neutral_git_args(tree), "status", "--porcelain"],
                           capture_output=True, text=True, env=env)
        return r.stdout


class GitImport(GitImportBase):
    # AC1, AC3
    @unittest.skipUnless(POSIX, "a tracked symlink needs a POSIX source")
    def test_hostile_link_and_filter_import_with_no_link_no_filter_no_remote_and_equal_log(self):
        src = self.source()
        b = self.bundle_of(src)
        tree = self.tree_from(src)
        shutil.copy(b, tree / ".corvin-export" / "git" / "history.bundle")
        with GitSpy() as spy:
            res = bundle.import_git(tree, {"head": "refs/heads/main"})
        self.assertTrue(res.ok, res)
        self.assertIsNone(res.warning)
        self.assertFalse((tree / ".corvin-export").exists())
        spy.assert_neutral(self)
        subs = spy.subcommands()
        for needed in ("init", "bundle", "fsck", "update-ref", "symbolic-ref", "read-tree", "config"):
            self.assertIn(needed, subs)
        self.assertFalse(self.marker.exists(), "a filter or hook ran")
        self.assertFalse(os.path.lexists(tree / "link"))
        self.assertFalse(any(p.is_symlink() for p in tree.rglob("*")))
        cfg = git(tree, "config", "--local", "--list")
        self.assertNotIn("remote.", cfg)
        self.assertEqual(git(tree, "config", "--local", "core.symlinks").strip(), "false")
        for ref in ("main", "feature", "v1"):
            self.assertEqual(git(tree, "log", "--oneline", ref), git(src, "log", "--oneline", ref))
        self.assertEqual(git(tree, "symbolic-ref", "HEAD").strip(), "refs/heads/main")
        self.assertEqual(sorted(self.neutral_status(tree).splitlines()), [" D .env", " D link"])
        self.assertFalse(self.marker.exists())
        self.assertTrue(any("link" in n for n in res.notes), res.notes)
        self.assertFalse(list(self.parent.glob("*.gitimport-*")))
        # no hooks from a template, no config from the bundle
        self.assertFalse((tree / ".git" / "hooks").exists() and any((tree / ".git" / "hooks").iterdir()))
        # the user's own git afterwards: a tracked link comes back as a text file (REVIEW-0062 N6)
        git(tree, "checkout", "--", "link")
        self.assertFalse((tree / "link").is_symlink())
        self.assertEqual((tree / "link").read_text(), "/etc/passwd")

    # AC2
    def test_thin_foreign_ref_and_corrupt_bundles_drop_the_part_and_leave_the_files(self):
        src = self.source()
        clone = self.tmp / "clone"
        git(self.tmp, "clone", "-q", str(src), str(clone))
        thin = self.bundle_of(src, "main~1..main", name="thin.bundle")
        foreign = self.bundle_of(clone, "--all", name="foreign.bundle")
        good = self.bundle_of(src, name="good.bundle")
        data = bytearray(good.read_bytes())
        for i in range(len(data) - 60, len(data) - 30):
            data[i] ^= 0xFF
        corrupt = self.tmp / "corrupt.bundle"
        corrupt.write_bytes(bytes(data))
        for n, (label, b) in enumerate((("thin", thin), ("remotes", foreign), ("corrupt", corrupt))):
            with self.subTest(label):
                tree = self.tree_from(src, f"p{n}")
                with GitSpy() as spy:
                    res = bundle.git_import(tree, b, "refs/heads/main")
                self.assertFalse(res.ok)
                self.assertEqual(res.warning, bundle.GIT_IMPORT_WARNING)
                self.assertFalse((tree / ".git").exists())
                self.assertTrue((tree / "README.md").is_file())
                self.assertFalse(list(self.parent.glob("*.gitimport-*")))
                spy.assert_neutral(self, some=label != "thin")      # a thin bundle is refused by its header, before git runs

    def test_a_head_that_is_not_listed_drops_the_part(self):
        src = self.source()
        tree = self.tree_from(src)
        res = bundle.git_import(tree, self.bundle_of(src), "refs/heads/nope")
        self.assertFalse(res.ok)
        self.assertFalse((tree / ".git").exists())

    # AC6 (REVIEW-0062 N7)
    def test_more_than_10000_refs_drop_the_part_before_any_update_ref(self):
        src = self.tmp / "many"
        src.mkdir()
        git(src, "init", "-q", "-b", "main")
        (src / "a.md").write_text("a\n")
        git(src, "add", "-A")
        git(src, "commit", "-q", "-m", "a")
        sha = git(src, "rev-parse", "HEAD").strip()
        lines = "".join(f"create refs/tags/t{i} {sha}\n" for i in range(10_001))
        subprocess.run(["git", "-C", str(src), "update-ref", "--stdin"], input=lines, text=True, check=True)
        b = self.bundle_of(src)
        tree = self.tree_from(src)
        with GitSpy() as spy:
            res = bundle.git_import(tree, b, "refs/heads/main")
        self.assertFalse(res.ok)
        self.assertEqual(res.warning, bundle.GIT_IMPORT_WARNING)
        self.assertNotIn("update-ref", spy.subcommands())
        self.assertNotIn("unbundle", " ".join(" ".join(c) for c in spy.calls))

    # AC5 (REVIEW-0062 N4): a clone's export imports with history
    def test_a_cloned_repository_exported_with_branches_and_tags_keeps_every_branch(self):
        up = self.source("upstream")
        clone = self.tmp / "clone"
        git(self.tmp, "clone", "-q", str(up), str(clone))
        git(clone, "branch", "local-work")
        (clone / "w.txt").write_text("stash me\n")
        git(clone, "add", "w.txt")
        git(clone, "stash", "-q")
        git(clone, "notes", "add", "-m", "a note", "HEAD")
        b = self.bundle_of(clone)                                 # what bundle.build writes: --branches --tags
        tree = self.tree_from(clone)
        res = bundle.git_import(tree, b, "refs/heads/main")
        self.assertTrue(res.ok, res)
        refs = git(tree, "for-each-ref", "--format=%(refname)").split()
        self.assertEqual(sorted(refs), ["refs/heads/local-work", "refs/heads/main", "refs/tags/v1"])
        for ref in ("main", "local-work", "v1"):
            self.assertEqual(git(tree, "log", "--oneline", ref), git(clone, "log", "--oneline", ref))

    def test_ref_name_rule(self):
        ok = ["refs/heads/main", "refs/heads/feature/x-1", "refs/tags/v1.2.3"]
        bad = ["refs/remotes/origin/main", "refs/notes/commits", "refs/stash", "refs/heads/../x", "refs/heads/a..b",
               "refs/heads/x.lock", "refs/heads/.hidden", "refs/heads/a@{1}", "refs/heads/a b", "refs/heads/a~1",
               "refs/heads/", "refs/heads/a/", "refs/heads/a.", "refs/heads/a\\b", "refs/heads/a:b", "HEAD",
               "refs/heads/a//b", "refs/heads/@"]
        for r in ok:
            self.assertTrue(bundle.import_ref_ok(r), r)
        for r in bad:
            self.assertFalse(bundle.import_ref_ok(r), r)


if __name__ == "__main__":
    unittest.main()
