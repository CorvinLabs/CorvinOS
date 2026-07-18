"""Regression tests for corvinOS/installer/steps/browser.py.

Reported 2026-07-18: on a fresh install "give me the text from this page" looped
"corvin-browser Dienst nicht verfügbar → Playwright fallback" and produced
nothing. Both browser backends need Playwright's Chromium binary, which a normal
install never provisions: `playwright` is an optional extra (`corvinos[browser]`)
and the ~150 MB `playwright install chromium` was never run. This step fixes that,
mirroring ensure_stt / ensure_piper. It must be idempotent (skip the download on
re-run) and fail-SOFT (a failed large download prints a manual command, never
aborts the install).
"""
from __future__ import annotations

import subprocess
import sys
from unittest import mock

import pytest

from corvinOS.installer.steps import browser


def test_skips_download_when_chromium_already_present(capsys):
    """Re-running the installer must not re-fetch ~150 MB."""
    ran = []
    with mock.patch.object(browser, "_chromium_present", return_value=True), \
         mock.patch.object(browser.subprocess, "run",
                           side_effect=lambda *a, **k: ran.append(a)):
        browser.ensure_browser(interactive=False)
    assert ran == [], "chromium already present → must NOT invoke a download"
    assert "already present" in capsys.readouterr().out


def test_downloads_chromium_with_the_console_interpreter(capsys):
    """The binary must be fetched with sys.executable so it lands where the
    console's Playwright looks."""
    calls = []

    def _fake_run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    # present() is False before the download, True after (simulates success).
    presence = iter([False, True])
    with mock.patch.object(browser, "_chromium_present",
                           side_effect=lambda: next(presence)), \
         mock.patch.object(browser.subprocess, "run", _fake_run):
        browser.ensure_browser(interactive=False)

    assert calls, "a missing chromium must trigger a download"
    cmd = calls[0]
    assert cmd[0] == sys.executable          # the console's own interpreter
    assert cmd[1:] == ["-m", "playwright", "install", "chromium"]
    assert "ready" in capsys.readouterr().out


def test_download_failure_is_fail_soft_with_a_manual_command(capsys):
    """A failed download must NOT raise and MUST tell the user how to finish."""
    with mock.patch.object(browser, "_chromium_present", return_value=False), \
         mock.patch.object(browser.subprocess, "run",
                           return_value=subprocess.CompletedProcess([], 1)):
        browser.ensure_browser(interactive=False)   # must not raise
    out = capsys.readouterr().out
    assert "playwright install chromium" in out


def test_download_timeout_is_fail_soft(capsys):
    with mock.patch.object(browser, "_chromium_present", return_value=False), \
         mock.patch.object(browser.subprocess, "run",
                           side_effect=subprocess.TimeoutExpired("x", 600)):
        browser.ensure_browser(interactive=False)   # must not raise
    assert "playwright install chromium" in capsys.readouterr().out


def test_missing_playwright_package_is_installed_first(capsys):
    """When the package itself is absent, pip installs it before the binary."""
    installed = []
    with mock.patch.dict(sys.modules, {"playwright": None}), \
         mock.patch.object(browser, "_pip_install",
                           side_effect=lambda *a, **k: installed.append(a) or True), \
         mock.patch.object(browser, "_chromium_present", return_value=True):
        browser.ensure_browser(interactive=False)
    assert installed and "playwright" in installed[0][0]


def test_pip_failure_is_fail_soft_with_a_manual_command(capsys):
    with mock.patch.dict(sys.modules, {"playwright": None}), \
         mock.patch.object(browser, "_pip_install", return_value=False):
        browser.ensure_browser(interactive=False)   # must not raise
    out = capsys.readouterr().out
    assert "corvinos[browser]" in out
