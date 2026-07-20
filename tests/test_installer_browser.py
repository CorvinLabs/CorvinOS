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

import re
import subprocess
import sys
from unittest import mock

import pytest

from corvinOS.installer.steps import browser


def _assert_remedies_are_runnable(out: str) -> None:
    """The canonical install (`uv tool install 'corvinos[browser]'`) exposes
    ONLY corvinos' own entry points on the user's PATH — bare `playwright …`
    and bare `pip …` are 'command not found' there. Any printed remedy must
    therefore use either a corvinos entry point (corvin-install --browser),
    `uv tool install …`, or an explicit `<python> -m …` interpreter form."""
    assert re.search(r"(?<!-m )\bplaywright install", out) is None, out
    assert re.search(r"(?<!-m )\bpip install", out) is None, out


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
    """A failed download must NOT raise and MUST tell the user how to finish —
    with a command that actually exists on the canonical uv-tool PATH."""
    with mock.patch.object(browser, "_chromium_present", return_value=False), \
         mock.patch.object(browser.subprocess, "run",
                           return_value=subprocess.CompletedProcess([], 1)):
        browser.ensure_browser(interactive=False)   # must not raise
    out = capsys.readouterr().out
    assert "corvin-install --browser" in out
    _assert_remedies_are_runnable(out)


def test_download_timeout_is_fail_soft(capsys):
    with mock.patch.object(browser, "_chromium_present", return_value=False), \
         mock.patch.object(browser.subprocess, "run",
                           side_effect=subprocess.TimeoutExpired("x", 600)):
        browser.ensure_browser(interactive=False)   # must not raise
    out = capsys.readouterr().out
    assert "corvin-install --browser" in out
    _assert_remedies_are_runnable(out)


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


# ── I1 (2026-07-20): remedies must exist on the canonical install path ────────
# `curl | sh` → `uv tool install 'corvinos[browser]'` exposes only corvinos'
# own entry points in ~/.local/bin. `playwright` and `pip` are NOT on the user
# PATH there (empirically verified), so the old remedies (`playwright install
# chromium`, `pip install 'corvinos[browser]'`) were dead advice.


def test_pip_failure_remedy_names_uv_receipt_reinstall_on_uv_tool_installs(capsys):
    """In a uv-tool venv (no pip) the only durable fix for a missing playwright
    package is reinstalling the extra INTO THE RECEIPT via uv."""
    with mock.patch.dict(sys.modules, {"playwright": None}), \
         mock.patch.object(browser, "_pip_install", return_value=False), \
         mock.patch.object(browser, "_is_uv_tool_install", return_value=True):
        browser.ensure_browser(interactive=False)
    out = capsys.readouterr().out
    assert "uv tool install --force 'corvinos[browser]'" in out
    _assert_remedies_are_runnable(out)


def test_pip_failure_remedy_uses_interpreter_module_form_on_pip_installs(capsys):
    """On a plain pip install, `pip` may still not be a PATH command — the
    remedy must use the explicit `<python> -m pip` interpreter form."""
    with mock.patch.dict(sys.modules, {"playwright": None}), \
         mock.patch.object(browser, "_pip_install", return_value=False), \
         mock.patch.object(browser, "_is_uv_tool_install", return_value=False):
        browser.ensure_browser(interactive=False)
    out = capsys.readouterr().out
    assert sys.executable in out
    assert "-m pip install" in out
    _assert_remedies_are_runnable(out)


def test_linux_install_deps_hint_uses_the_tool_venv_interpreter(capsys):
    """`sudo playwright install-deps chromium` is not runnable (no `playwright`
    on PATH, let alone root's) — the hint must name the venv python."""
    presence = iter([False, True])
    with mock.patch.object(browser, "_chromium_present",
                           side_effect=lambda: next(presence)), \
         mock.patch.object(browser.subprocess, "run",
                           return_value=subprocess.CompletedProcess([], 0)), \
         mock.patch.object(browser.sys, "platform", "linux"):
        browser.ensure_browser(interactive=False)
    out = capsys.readouterr().out
    assert f'sudo "{sys.executable}" -m playwright install-deps chromium' in out
    _assert_remedies_are_runnable(out)


def test_cli_browser_flag_runs_only_the_browser_step(monkeypatch):
    """`corvin-install --browser` is the one-line remedy the messages print —
    it must re-run browser provisioning WITHOUT the full install wizard."""
    from corvinOS.installer import __main__ as inst_main

    ran = []
    monkeypatch.setattr(browser, "ensure_browser",
                        lambda interactive=True: ran.append(interactive))
    monkeypatch.setattr(
        inst_main, "CorvinInstaller",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("the full installer must not run for --browser")),
    )
    # main_install() rewrites argv to ["install", …] — simulate the result.
    monkeypatch.setattr(sys, "argv", ["corvin-install", "install", "--browser"])
    inst_main.main()
    assert len(ran) == 1
