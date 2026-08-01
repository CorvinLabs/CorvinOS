#!/usr/bin/env python3
"""Regression test for a real bug found 2026-08-01 via a live Windows-11 VM
run of the A2A sender: remote_trigger_sender.py and
remote_trigger_receiver.py each computed a module-level default directory
via ``Path(__file__).resolve().parents[2]`` with no guard. That is safe
inside the full repo tree (this file always sits 3 levels under the repo
root there), but a MINIMAL standalone deployment of these modules — e.g.
copying just the stdlib-only sender + its direct dependencies onto a bare
box to send one signed A2A envelope, exactly what a portable diagnostic
tool or a thin cross-platform bridge install might do — sits shallower.
``Path.parents[2]`` raised ``IndexError`` there, which crashed the WHOLE
MODULE at import time, before any caller-supplied ``endpoints_dir``/
``origins_dir`` ever got a chance to be used. Confirmed on real Windows
(win11-test VM, ADR-0265): a bare ``import remote_trigger_sender`` failed
with ``IndexError: 2`` at ``remote_trigger_sender.py:132``.

Fixed by walking up for a ``.corvin_repo``/``plugins`` marker (mirrors
paths.py::_repo_root()) with a same-directory fallback instead of raising.
This test proves BOTH modules import cleanly from a directory with zero
repo markers (real subprocess import, real shallow tmp dir — not a mock),
and that the in-repo resolution is byte-identical to before the fix.

2026-08-02: an adversarial re-review found the IDENTICAL unguarded
``parents[2]`` pattern in ``a2a_http_server.py`` (module-level
``_DEFAULT_COWORK_DIR`` and again inside ``build_server()``) — missed by
the original 2026-08-01 fix, which only touched the sender/receiver.
Fixed the same way and added to this suite's coverage below.

Run: python3 operator/bridges/shared/test_a2a_shallow_path_import.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent

# The minimal, stdlib-only file set a standalone A2A sender/receiver needs —
# same set verified against a real Windows-11 VM this session.
_MIN_FILES = (
    "remote_trigger_sender.py",
    "remote_trigger_receiver.py",
    "a2a_http_server.py",
    "audit.py",
    "instance_identity.py",
    "a2a_friendship.py",
    "a2a_attachments.py",
    "a2a_manifest.py",
    "paths.py",
)


class TestShallowStandaloneImport(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="a2a-shallow-"))
        # No .corvin_repo, no plugins/ dir anywhere above self.tmp within
        # this test's control — mirrors dropping the files onto a bare box.
        for name in _MIN_FILES:
            src = _HERE / name
            if src.is_file():
                shutil.copy2(src, self.tmp / name)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _import_in_subprocess(self, module: str) -> subprocess.CompletedProcess:
        # Real subprocess + real cwd = the shallow dir itself, so no repo
        # marker exists on any ancestor path the fallback walk would find
        # by accident inside this checkout.
        return subprocess.run(
            [sys.executable, "-c", f"import {module}"],
            cwd=str(self.tmp),
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_sender_imports_cleanly_from_shallow_standalone_dir(self):
        result = self._import_in_subprocess("remote_trigger_sender")
        self.assertEqual(
            result.returncode, 0,
            f"import crashed:\nstdout={result.stdout}\nstderr={result.stderr}",
        )
        self.assertNotIn("IndexError", result.stderr)

    def test_receiver_imports_cleanly_from_shallow_standalone_dir(self):
        result = self._import_in_subprocess("remote_trigger_receiver")
        self.assertEqual(
            result.returncode, 0,
            f"import crashed:\nstdout={result.stdout}\nstderr={result.stderr}",
        )
        self.assertNotIn("IndexError", result.stderr)

    def test_http_server_imports_cleanly_from_shallow_standalone_dir(self):
        """2026-08-02: a2a_http_server.py had the identical unguarded
        parents[2] pattern, missed by the 2026-08-01 fix that only covered
        the sender/receiver — this is the regression guard for it."""
        result = self._import_in_subprocess("a2a_http_server")
        self.assertEqual(
            result.returncode, 0,
            f"import crashed:\nstdout={result.stdout}\nstderr={result.stderr}",
        )
        self.assertNotIn("IndexError", result.stderr)

    def test_fallback_default_dir_is_under_shallow_root_not_crashing(self):
        script = (
            "import remote_trigger_sender as rts\n"
            "print(rts._REMOTE_ENDPOINTS_DEFAULT)\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(self.tmp), capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        printed = Path(result.stdout.strip())
        # Must resolve to SOMETHING sane under/near the shallow dir, never
        # raise — exact location is an implementation detail, non-crashing
        # is the invariant this test locks in.
        self.assertTrue(str(printed).endswith("remote_endpoints"))


class TestInRepoResolutionUnchanged(unittest.TestCase):
    """The fix must not change behavior for the real, shipped repo layout
    (the overwhelmingly common case) — same target path as the original
    unguarded parents[2] indexing produced."""

    def test_sender_default_matches_pre_fix_path(self):
        import remote_trigger_sender as rts
        repo_root = _HERE.parent.parent.parent  # shared -> bridges -> operator -> repo root
        self.assertEqual(
            rts._REMOTE_ENDPOINTS_DEFAULT,
            repo_root / "operator" / "cowork" / "remote_endpoints",
        )

    def test_receiver_defaults_match_pre_fix_paths(self):
        import remote_trigger_receiver as rtr
        repo_root = _HERE.parent.parent.parent
        self.assertEqual(
            rtr._REMOTE_ORIGINS_DEFAULT,
            repo_root / "operator" / "cowork" / "remote_origins",
        )
        self.assertEqual(
            rtr._REMOTE_ENDPOINTS_DEFAULT,
            repo_root / "operator" / "cowork" / "remote_endpoints",
        )
        self.assertEqual(
            rtr._A2A_NETWORK_PUBKEY_PATH,
            repo_root / "operator" / "license" / "a2a_network_pubkey.pem",
        )

    def test_http_server_default_matches_pre_fix_path(self):
        import a2a_http_server as a2ahs
        repo_root = _HERE.parent.parent.parent
        self.assertEqual(
            a2ahs._DEFAULT_COWORK_DIR,
            repo_root / "operator" / "cowork",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
