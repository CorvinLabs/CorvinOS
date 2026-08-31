#!/usr/bin/env python3
"""Tests for agents.claude_code._resolve_claude_bin's Windows fallback
locations (ADR-0265 P1). npm's global installer drops claude.cmd/
claude.exe under %APPDATA%\\npm on Windows — the Windows equivalent of
~/.local/bin, which the pre-existing POSIX fallback list already
covers. Without a Windows-specific candidate list, a bare "claude"
spawn on a Windows box whose PATH lacks the npm global bin dir raises
FileNotFoundError even though the CLI is installed.

Run with: python3 operator/bridges/shared/agents/test_claude_bin_resolution.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
SHARED = HERE.parent
sys.path.insert(0, str(SHARED))

from agents import claude_code  # noqa: E402


class WindowsBinFallbacksTests(unittest.TestCase):
    def setUp(self) -> None:
        self._snap = {k: os.environ.get(k) for k in
                      ("APPDATA", "USERPROFILE", "CORVIN_CLAUDE_BIN_FALLBACKS", "PATH")}

    def tearDown(self) -> None:
        for k, v in self._snap.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_windows_bin_fallbacks_reads_appdata_and_userprofile(self) -> None:
        os.environ["APPDATA"] = r"C:\Users\op\AppData\Roaming"
        os.environ["USERPROFILE"] = r"C:\Users\op"
        cands = claude_code._windows_bin_fallbacks()
        self.assertIn(os.path.join(r"C:\Users\op\AppData\Roaming", "npm", "claude.cmd"), cands)
        self.assertIn(os.path.join(r"C:\Users\op\AppData\Roaming", "npm", "claude.exe"), cands)
        self.assertIn(os.path.join(r"C:\Users\op", ".local", "bin", "claude.exe"), cands)

    def test_windows_bin_fallbacks_empty_without_env(self) -> None:
        os.environ.pop("APPDATA", None)
        os.environ.pop("USERPROFILE", None)
        self.assertEqual(claude_code._windows_bin_fallbacks(), ())

    def test_resolve_claude_bin_finds_windows_npm_shim_when_platform_is_windows(self) -> None:
        """The actual regression this closes: on a real Windows box with
        claude.cmd under %APPDATA%\\npm but that dir missing from PATH,
        a bare "claude" spawn must still resolve — not raise
        FileNotFoundError. Simulated via sys.platform + shutil.which
        mocks (real subprocess spawn isn't exercised, matching the
        pattern used for terminate_process_tree's Windows tests)."""
        with tempfile.TemporaryDirectory() as d:
            appdata = Path(d) / "AppData" / "Roaming"
            npm_dir = appdata / "npm"
            npm_dir.mkdir(parents=True)
            shim = npm_dir / "claude.cmd"
            shim.write_text("@echo off\n")
            os.environ["APPDATA"] = str(appdata)
            os.environ.pop("USERPROFILE", None)
            os.environ.pop("CORVIN_CLAUDE_BIN_FALLBACKS", None)
            with mock.patch.object(claude_code.sys, "platform", "win32"), \
                 mock.patch.object(claude_code.shutil, "which", return_value=None), \
                 mock.patch.object(claude_code.os, "access", return_value=True):
                resolved = claude_code._resolve_claude_bin("claude")
            self.assertEqual(resolved, str(shim))

    def test_resolve_claude_bin_ignores_windows_fallbacks_on_posix(self) -> None:
        """Guards against the Windows candidates leaking into POSIX
        resolution — a file that only exists under a %APPDATA%-shaped
        path must never be returned when sys.platform is not win32."""
        with tempfile.TemporaryDirectory() as d:
            appdata = Path(d) / "AppData" / "Roaming"
            npm_dir = appdata / "npm"
            npm_dir.mkdir(parents=True)
            shim = npm_dir / "claude.cmd"
            shim.write_text("@echo off\n")
            os.environ["APPDATA"] = str(appdata)
            os.environ.pop("CORVIN_CLAUDE_BIN_FALLBACKS", None)
            with mock.patch.object(claude_code.sys, "platform", "linux"), \
                 mock.patch.object(claude_code.shutil, "which", return_value=None):
                resolved = claude_code._resolve_claude_bin("claude")
            self.assertNotEqual(resolved, str(shim))

    def test_format_binary_not_found_error_lists_windows_fallbacks(self) -> None:
        os.environ["APPDATA"] = r"C:\Users\op\AppData\Roaming"
        with mock.patch.object(claude_code.sys, "platform", "win32"):
            msg = claude_code._format_binary_not_found_error("claude")
        self.assertIn("npm", msg)
        self.assertIn("claude.cmd", msg)


if __name__ == "__main__":
    unittest.main(verbosity=2)
