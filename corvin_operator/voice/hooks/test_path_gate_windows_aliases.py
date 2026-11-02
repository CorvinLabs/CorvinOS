#!/usr/bin/env python3
"""Windows path aliases must not slip past is_protected_path.

On a live Windows install every one of these named the hash-chained audit log
(or a protected tenant file) yet matched no protected root, because the gate
compared plain ``str()`` prefixes: the Win32 extended-length form ``\\\\?\\C:\\``
and the loopback admin share ``\\\\localhost\\C$\\`` survive ``resolve()``
verbatim, a CORVIN_HOME spelled in another case never prefix-matched, and a
not-yet-existing leaf keeps its case, trailing dot or ``::$DATA`` suffix (all of
which Win32 drops when it opens the file). Runs only on Windows: the aliases
mean nothing to a POSIX filesystem.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import path_gate as pg  # type: ignore  # noqa: E402

B = "\\"


@unittest.skipUnless(sys.platform.startswith("win"), "Windows path aliases")
class TestWindowsAliases(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name).resolve() / ".corvin"
        (self.home / "global" / "forge").mkdir(parents=True)
        (self.home / "global" / "forge" / "audit.jsonl").write_text("x")
        (self.home / "tenants" / "t1").mkdir(parents=True)
        self.forge = str(self.home / "global" / "forge")
        self.t1 = str(self.home / "tenants" / "t1")
        env = mock.patch.dict(os.environ, {"CORVIN_HOME": str(self.home)})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(self._tmp.cleanup)

    def assertProtected(self, p):
        self.assertTrue(pg.is_protected_path(p), p)

    def test_extended_length_and_device_prefixes(self):
        self.assertProtected(B + B + "?" + B + self.forge + B + "audit.jsonl")
        self.assertProtected(B + B + "." + B + self.forge + B + "new.txt")

    def test_loopback_admin_share(self):
        unc = self.forge.replace(":", "$", 1)
        self.assertProtected(B + B + "localhost" + B + unc + B + "audit.jsonl")
        self.assertProtected(B + B + "127.0.0.1" + B + unc + B + "new.txt")
        self.assertProtected(B + B + "?" + B + "UNC" + B + "localhost" + B + unc + B + "x")

    def test_home_given_in_other_case(self):
        with mock.patch.dict(os.environ, {"CORVIN_HOME": str(self.home).lower()}):
            self.assertProtected(self.forge + B + "audit.jsonl")
            self.assertProtected(self.t1 + B + "audit.jsonl")

    def test_new_leaf_spellings_win32_normalises(self):
        self.assertProtected(self.t1 + B + "AUDIT.JSONL")
        self.assertProtected(self.t1 + B + "audit.jsonl.")
        self.assertProtected(self.t1 + B + "audit.jsonl ")
        self.assertProtected(self.t1 + B + "tenant.corvin.yaml::$DATA")

    def test_ordinary_paths_stay_writable(self):
        self.assertFalse(pg.is_protected_path(self.t1 + B + "notes.txt"))
        self.assertFalse(pg.is_protected_path(str(self.home.parent / "x.txt")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
