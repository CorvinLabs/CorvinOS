"""REVIEW-0063 - the export path of the project bundle (ADR-2323): the races and gaps the security review found.

Every race is made deterministic: the swap or the commit is injected through a hook at the exact point between the two steps
the review named (no inotify, no timing). Same sandbox as ``test_project_bundle.py``.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import unittest
import zipfile
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from test_project_bundle import JWT, MARKER, POSIX, BundleBase, _commit_all, _gitc  # noqa: E402

from core.projects import bundle, bundle_rules  # noqa: E402

AWS = "AKIA" + "IOSFODNN7EXAMPLE"


def _ident(p: Path) -> tuple[int, int]:
    st = os.lstat(p)
    return st.st_dev, st.st_ino


class _SwapOnListing:
    """A ``_scandir`` replacement that swaps ``victim`` for a link to ``outside`` the moment the walk lists it - after the walk
    checked it, before (old code) or after (fd walk) it was opened. Accepts a path string or a directory fd."""

    def __init__(self, victim: Path, outside: Path, parked: Path):
        self.victim, self.outside, self.parked = victim, outside, parked
        self.ident = _ident(victim)
        self.swapped = False
        self.real = os.scandir

    def _is_victim(self, arg) -> bool:
        if isinstance(arg, int):
            st = os.fstat(arg)
            return (st.st_dev, st.st_ino) == self.ident
        return os.path.abspath(arg) == str(self.victim)

    def __call__(self, arg):
        if not self.swapped and self._is_victim(arg):
            os.rename(self.victim, self.parked)
            os.symlink(self.outside, self.victim)
            self.swapped = True
        return self.real(arg)


class ExportWalkRace(BundleBase):
    """M1: a directory swapped for a symlink between its check and its listing never makes the export read through the link."""

    def _setup(self):
        pid, root = self.project()
        (root / "a_sub").mkdir()
        (root / "a_sub" / "inner.txt").write_text("inside\n")
        outside = self.tmp / "outside"
        outside.mkdir()
        (outside / "stolen.txt").write_text(MARKER + "\n")
        return pid, root, outside

    def _assert_nothing_outside(self, z: Path, pid: str) -> None:
        names = self.names(z)
        self.assertNotIn(f"projects/{pid}/a_sub/stolen.txt", names)
        with zipfile.ZipFile(z) as zf:
            for n in zf.namelist():
                self.assertNotIn(MARKER.encode(), zf.read(n), n)

    @unittest.skipUnless(POSIX, "symlinks without privileges")
    def test_a_directory_swapped_for_a_link_after_its_check_is_never_listed_through_the_link(self):
        self.assertTrue(bundle._fd_walk(), "the descriptor walk is the POSIX path")
        pid, root, outside = self._setup()
        swap = _SwapOnListing(root / "a_sub", outside, self.tmp / "parked")
        with mock.patch.object(bundle, "_scandir", swap):
            z, _ = self.build([pid], git_history=False)
        self.assertTrue(swap.swapped, "positive control: the swap happened during the walk")
        self._assert_nothing_outside(z, pid)
        # the files listed through the real directory are read through the path again, which is a link now: left out
        self.assertEqual(self.left_out(self.envelope(z), pid).get("a_sub/inner.txt"), "link")

    @unittest.skipUnless(POSIX, "simulates the Windows path walk on a POSIX host")
    def test_the_path_walk_fallback_drops_a_directory_that_changed_while_it_was_listed(self):
        pid, root, outside = self._setup()
        swap = _SwapOnListing(root / "a_sub", outside, self.tmp / "parked")
        with mock.patch.object(bundle, "_scandir", swap), mock.patch.object(bundle, "_IS_WINDOWS", True), \
                mock.patch.object(bundle, "_O_NOFOLLOW", 0):
            z, _ = self.build([pid], git_history=False)
        self.assertTrue(swap.swapped)
        self._assert_nothing_outside(z, pid)
        self.assertEqual(self.left_out(self.envelope(z), pid).get("a_sub"), "link")

    @unittest.skipUnless(POSIX, "symlinks without privileges")
    def test_a_parent_directory_swapped_before_a_file_is_read_leaves_the_file_out(self):
        pid, root, outside = self._setup()
        (outside / "inner.txt").write_text(MARKER + "\n")
        parked = self.tmp / "parked"
        calls: list[str] = []

        def hook(stage: str, rel: str) -> None:
            calls.append(f"{stage}:{rel}")
            if stage == "file" and rel == "a_sub/inner.txt" and not (root / "a_sub").is_symlink():
                os.rename(root / "a_sub", parked)
                os.symlink(outside, root / "a_sub")
        with mock.patch.object(bundle, "_race_hook", hook):
            z, _ = self.build([pid], git_history=False)
        self.assertIn("file:a_sub/inner.txt", calls)
        self._assert_nothing_outside(z, pid)
        self.assertEqual(self.left_out(self.envelope(z), pid).get("a_sub/inner.txt"), "link")


def _unshare_ok() -> bool:
    if not POSIX or shutil.which("unshare") is None:
        return False
    try:
        return subprocess.run(["unshare", "-Urm", "true"], capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


class OtherFilesystem(BundleBase):
    """m3: a regular file whose st_dev differs from the project root's (a bind mount) is left out as ``mount``."""

    @unittest.skipUnless(_unshare_ok(), "needs an unprivileged user + mount namespace (unshare -Urm)")
    def test_a_file_bind_mounted_from_another_filesystem_is_left_out(self):
        pid, root = self.project()
        (root / "data.txt").write_text("inside\n")
        shm = Path("/dev/shm") if Path("/dev/shm").is_dir() else None
        if shm is None or os.stat(shm).st_dev == os.stat(root).st_dev:
            self.skipTest("no second filesystem to bind from")
        src = shm / f"r63-{os.getpid()}.txt"
        src.write_text(MARKER + "\n")
        self.addCleanup(lambda: src.unlink(missing_ok=True))
        out = self.out / "m3.zip"
        code = ("import json, subprocess, sys\n"
                "from core.projects import bundle\n"
                "r = subprocess.run(['mount', '--bind', sys.argv[1], sys.argv[2]], capture_output=True, text=True)\n"
                "assert r.returncode == 0, r.stderr\n"
                "bundle.build('_default', [sys.argv[3]], bundle.Parts(git_history=False), out=__import__('pathlib').Path(sys.argv[4]))\n"
                "print('built')\n")
        repo = str(Path(bundle.__file__).resolve().parents[2])
        env = {**os.environ, "PYTHONPATH": os.pathsep.join([repo, str(Path(repo) / "core" / "console")])}
        r = subprocess.run(["unshare", "-Urm", sys.executable, "-c", code, str(src), str(root / "data.txt"), pid, str(out)],
                           capture_output=True, text=True, env=env, cwd=repo, timeout=300)
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        self.assertNotIn(f"projects/{pid}/data.txt", self.names(out))
        self.assertEqual(self.left_out(self.envelope(out), pid).get("data.txt"), "mount")
        with zipfile.ZipFile(out) as zf:
            for n in zf.namelist():
                self.assertNotIn(MARKER.encode(), zf.read(n), n)


if __name__ == "__main__":
    unittest.main()
