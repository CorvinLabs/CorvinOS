"""Browser automation (ADR-0182): Playwright + its Chromium browser binary.

Why this step exists — reported 2026-07-18: on a fresh install "give me the text
from this page" failed with the model looping "der corvin-browser Dienst ist
nicht verfügbar → alternativer Playwright-Browser" and never producing anything.

Root cause: BOTH browser backends (the corvin-browser MCP server, which proxies
to the console's own Chromium, and the fallback Playwright MCP) depend on the
same Playwright Chromium binary. That binary is NOT present on a normal install:
`playwright` is an OPTIONAL extra (`corvinos[browser]`) so a plain
`pip install corvinos` never ships it, and even with the extra the ~150 MB
browser binary must be fetched with a separate `playwright install chromium`
that nothing ran. So the browser feature was dead on arrival, with an opaque
error. This step provisions both, mirroring how ensure_stt / ensure_piper make
voice work out of the box.

Fail-SOFT throughout: the browser is a bonus, not the product's core promise, and
its binary is a large network download. A failure here prints an actionable
manual command and never aborts the install.
"""
from __future__ import annotations

import subprocess
import sys

from .dependencies import pip_install as _pip_install

# ── Remedy commands (I1, 2026-07-20) ──────────────────────────────────────────
# The canonical install path is `curl | sh` → `uv tool install 'corvinos[browser]'`,
# which exposes ONLY corvinos' own entry points in ~/.local/bin. Bare
# `playwright …` and `pip …` are "command not found" on that PATH, so every
# printed remedy must be either a corvinos entry point, a `uv tool …` command,
# or an explicit `<venv-python> -m …` interpreter form.

# One-line re-run of exactly this provisioning step (added alongside this fix).
_REMEDY_PROVISION = "corvin-install --browser"


def _is_uv_tool_install() -> bool:
    """True when running from a ``uv tool install`` managed venv (which has no
    pip and exposes no `playwright` shim on the user PATH)."""
    probe = str(sys.prefix).replace("\\", "/").lower()
    return "/uv/tools/" in probe or probe.rstrip("/").endswith("/tools/corvinos")


def _remedy_reinstall_extra() -> str:
    """The durable way to (re)install the `[browser]` extra for this flavour:
    into the uv receipt for uv-tool installs (survives `uv tool upgrade`),
    via the interpreter's own pip module otherwise."""
    if _is_uv_tool_install():
        return "uv tool install --force 'corvinos[browser]'"
    return f'"{sys.executable}" -m pip install "corvinos[browser]"'


def _remedy_system_deps() -> str:
    """Chromium's system libraries need root — name the venv interpreter
    explicitly, since root's PATH has neither `playwright` nor this venv."""
    return f'sudo "{sys.executable}" -m playwright install-deps chromium'


def _chromium_present() -> bool:
    """True iff Playwright can already resolve a Chromium executable — so a
    re-run of the installer skips the ~150 MB download instead of repeating it."""
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except Exception:
        return False
    try:
        with sync_playwright() as pw:
            path = pw.chromium.executable_path
        import os
        return bool(path) and os.path.exists(path)
    except Exception:
        return False


def ensure_browser(interactive: bool = True) -> None:
    """Install Playwright and download its Chromium binary so agent-driven
    browsing (ADR-0182) works out of the box. Fail-soft — never aborts install."""
    print("  Browser automation (Playwright + Chromium)...")

    # 1. The Playwright Python package (the `browser` extra). Idempotent — pip
    #    is a no-op when it's already satisfied.
    try:
        import playwright  # type: ignore  # noqa: F401
        have_pkg = True
    except Exception:
        have_pkg = False
    if not have_pkg:
        if not _pip_install("playwright>=1.40"):
            print("  ⚠ could not install the 'playwright' package — browser "
                  "automation stays off. Finish later with:")
            print(f"      {_remedy_reinstall_extra()}")
            print(f"      {_REMEDY_PROVISION}")
            return

    # 2. The Chromium BINARY — the part a bare pip install never fetches. Skip if
    #    already resolvable so a re-run doesn't re-download ~150 MB.
    if _chromium_present():
        print("  ✓ Chromium already present")
        return

    print("  Downloading Chromium (~150 MB, one-time)...")
    try:
        # Use the SAME interpreter the console runs on (sys.executable) so the
        # binary lands in the browsers path that interpreter's Playwright reads.
        # No capture: Playwright prints its own download progress, matching the
        # other steps' visible-progress convention.
        proc = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            timeout=600,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        print(f"  ⚠ Chromium download did not finish ({type(exc).__name__}). "
              f"Finish later with:  {_REMEDY_PROVISION}")
        return
    if proc.returncode == 0 and _chromium_present():
        print("  ✓ Chromium installed — agent browsing is ready")
        # The download alone is not sufficient on minimal Linux images: Chromium
        # needs system libraries (libnss3, libatk, libasound2, ...) that only a
        # root-level package manager can provide. We cannot (and must not) sudo
        # from here, so surface the one command that closes the gap if launch
        # fails later.
        if sys.platform.startswith("linux"):
            print("  ℹ if the browser fails to launch with 'missing dependencies',"
                  f" run:  {_remedy_system_deps()}")
    else:
        print("  ⚠ Chromium download failed (exit "
              f"{proc.returncode}). Finish later with:  {_REMEDY_PROVISION}")
