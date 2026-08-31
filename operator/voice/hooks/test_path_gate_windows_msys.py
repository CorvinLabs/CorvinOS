#!/usr/bin/env python3
"""Regression test for a Windows-specific path-gate bypass hypothesis raised
by a user-submitted diagnostic report from a live Windows CorvinOS instance
(2026-08-02). Not independently confirmed on real Windows (no Windows box
available in this environment) — this locks in the concrete, plausible
mechanism found by code review: Git-Bash/MSYS2 (the common Bash-tool runtime
on Windows) translates ``/c/Users/...`` to ``C:\\Users\\...`` at execution
time, but this hook is a separate Python subprocess with no MSYS runtime —
without translation, ``Path("/c/Users/.../forge/policy.json")`` resolves
relative to "root of the current drive" and never equals the real protected
path, so the write would sail through undetected.

Simulated via ``sys.platform`` mocking (same pattern as this session's other
Windows-parity tests) since real MSYS/Windows execution isn't available here.

Run: python3 operator/voice/hooks/test_path_gate_windows_msys.py
"""
from __future__ import annotations

import sys
import unittest
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import path_gate as pg  # type: ignore  # noqa: E402


class TestMsysDriveNormalization(unittest.TestCase):
    def test_noop_on_posix(self):
        with mock.patch.object(pg.sys, "platform", "linux"):
            self.assertEqual(
                pg._normalize_msys_drive("/c/Users/x/forge/policy.json"),
                "/c/Users/x/forge/policy.json",
            )

    def test_translates_msys_drive_on_windows(self):
        with mock.patch.object(pg.sys, "platform", "win32"):
            self.assertEqual(
                pg._normalize_msys_drive("/c/Users/x/forge/policy.json"),
                "C:/Users/x/forge/policy.json",
            )

    def test_uppercases_drive_letter_on_windows(self):
        with mock.patch.object(pg.sys, "platform", "win32"):
            self.assertEqual(
                pg._normalize_msys_drive("/d/repo/audit.jsonl"),
                "D:/repo/audit.jsonl",
            )

    def test_bare_drive_root_on_windows(self):
        with mock.patch.object(pg.sys, "platform", "win32"):
            self.assertEqual(pg._normalize_msys_drive("/c"), "C:/")

    def test_does_not_touch_a_real_posix_style_two_letter_segment(self):
        """A genuine multi-char first segment (not a single drive letter)
        must never be mistaken for an MSYS drive prefix, on any platform."""
        with mock.patch.object(pg.sys, "platform", "win32"):
            self.assertEqual(
                pg._normalize_msys_drive("/corvin/forge/policy.json"),
                "/corvin/forge/policy.json",
            )

    def test_relative_and_windows_native_paths_untouched(self):
        with mock.patch.object(pg.sys, "platform", "win32"):
            self.assertEqual(pg._normalize_msys_drive("relative/x.txt"), "relative/x.txt")
            self.assertEqual(
                pg._normalize_msys_drive(r"C:\Users\x\forge\policy.json"),
                r"C:\Users\x\forge\policy.json",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
