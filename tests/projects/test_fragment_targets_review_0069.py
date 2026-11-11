"""REVIEW-0069 (REVIEW-0066 m2, partial): a protected name is refused as ANY path component, the final one included.

The REVIEW-0066 fix refused ``.git/config`` but checked ``.git`` only as a folder on the way, so a fragment could still write a
FILE named ``.git`` (``gitdir: gd``) next to a hand-made git directory whose ``config`` sets ``core.fsmonitor``; the operator's
next plain ``git -C sub status`` ran the command. The same attack without a protected name is a bare-repository layout
(``HEAD`` + ``config`` with ``core.worktree = ..`` + ``objects/`` + ``refs/``) in a subfolder, which git also discovers
and then runs fsmonitor for; a template never needs a file
named ``HEAD``, so that name is refused too. The reproduction below drives the real loader, planner and writer and then the
real ``git``.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "e2e"))
import _projects_fixtures as fx  # noqa: E402

from core.projects import fragments as F  # noqa: E402

FINAL_COMPONENT = [".git", "x/.git", ".GIT.", "x/.Git ", ".claude", "sub/.claude", ".corvin", "a/.corvin", ".vscode",
                   ".corvin/forge", ".github/workflows", "kb/_meta", "HEAD", "sub/head", "sub/gd/HEAD"]
STILL_ALLOWED = [".corvin/forge/README.md", ".corvin/forge/notes/x.md", "docs/git.md", "x/.gitignore", "heads/up.md",
                 "config/app.yaml", "sub/config"]


def _write(src: Path, files: list[dict], texts: dict[str, str]) -> None:
    fx.write_fragment(src, "core", texts=texts, **{"files": files, "actions": ["git_init"]})
    fx.write_template(src, "demo", ["core"])


class FinalComponentTargets(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _load(self, dst: str):
        src = self.tmp / f"c{abs(hash(dst))}"
        _write(src, [{"src": "files/f", "dst": dst, "kind": "seed"}], {"files/f": "x\n"})
        return F.load_catalogue(src)

    def test_a_protected_name_is_refused_as_the_final_component(self):
        for dst in FINAL_COMPONENT:
            with self.subTest(dst=dst), self.assertRaises(F.FragmentError) as cm:
                self._load(dst)
            self.assertIn("protected", str(cm.exception))

    def test_ordinary_and_engine_owned_targets_still_load(self):
        for dst in STILL_ALLOWED:
            with self.subTest(dst=dst):
                self.assertEqual(self._load(dst).fragments["core"].files[0]["dst"], dst)


class WindowsAliasTargets(FinalComponentTargets):
    """REVIEW-0070 N3: on NTFS an 8.3 short name (GIT~1) and an alternate data stream / index allocation name (.git::$INDEX_ALLOCATION)
    reach .git without ever spelling it. Refused on every platform: the catalogue is the same file everywhere."""

    ALIASES = ["GIT~1/config", "x/GIT~1/config", ".git::$INDEX_ALLOCATION/config", "a:b", "notes/x.md:stream", "CLAUDE~1/settings.json"]

    def test_short_names_and_stream_names_are_refused(self):
        for dst in self.ALIASES:
            with self.subTest(dst=dst), self.assertRaises(F.FragmentError):
                self._load(dst)

    def test_a_tilde_without_a_digit_still_loads(self):
        self.assertEqual(self._load("docs/~draft.md").fragments["core"].files[0]["dst"], "docs/~draft.md")


class GitdirRedirectReproduction(unittest.TestCase):
    """The verifier's /tmp/r69/frag_git2.py as a test: a ``.git`` FILE plus an fsmonitor config under unprotected names."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _attack(self, files: list[dict], extra: str = "") -> Path:
        src, root, marker = self.tmp / "cat", self.tmp / "proj", self.tmp / "MARKER"
        cfg = f"[core]\n\trepositoryformatversion = 0\n\tbare = false\n{extra}\tfsmonitor = touch {marker}; false\n"
        _write(src, files, {"files/cfg": cfg, "files/head": "ref: refs/heads/main\n", "files/keep": "",
                            "files/gitfile": "gitdir: gd\n"})
        try:
            cat = F.load_catalogue(src)
        except F.FragmentError as exc:
            self.assertIn("protected", str(exc))
            return marker
        plan = F.build_plan(cat, root, ["core"], fx.ctx_for("proj", root), mode="create")

        def runner(op, ctx):
            if op.action == "git_init":
                subprocess.run(["git", "init", "-q", str(root)], check=True)
        F.apply_plan(plan, root, fx.ctx_for("proj", root), runner)
        subprocess.run(["git", "-C", str(root / "sub"), "status", "--short"], capture_output=True, text=True)
        subprocess.run(["git", "-C", str(root / "bare"), "status", "--short"], capture_output=True, text=True)
        return marker

    def test_a_gitdir_file_cannot_redirect_git_to_a_planted_config(self):
        marker = self._attack([{"src": "files/cfg", "dst": "sub/gd/config", "kind": "generated"},
                               {"src": "files/head", "dst": "sub/gd/HEAD", "kind": "generated"},
                               {"src": "files/keep", "dst": "sub/gd/objects/.keep", "kind": "generated"},
                               {"src": "files/keep", "dst": "sub/gd/refs/.keep", "kind": "generated"},
                               {"src": "files/gitfile", "dst": "sub/.git", "kind": "generated"}])
        self.assertFalse(marker.exists(), "the planted fsmonitor command ran")

    def test_a_bare_repository_layout_cannot_be_planted(self):
        marker = self._attack([{"src": "files/cfg", "dst": "bare/config", "kind": "generated"},
                               {"src": "files/head", "dst": "bare/HEAD", "kind": "generated"},
                               {"src": "files/keep", "dst": "bare/objects/.keep", "kind": "generated"},
                               {"src": "files/keep", "dst": "bare/refs/.keep", "kind": "generated"}],
                             extra="\tworktree = ..\n")
        self.assertFalse(marker.exists(), "the planted fsmonitor command ran")


if __name__ == "__main__":
    unittest.main()
