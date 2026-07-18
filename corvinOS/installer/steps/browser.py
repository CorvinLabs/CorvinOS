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
            print("      pip install 'corvinos[browser]' && playwright install chromium")
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
              "Finish later with:  playwright install chromium")
        return
    if proc.returncode == 0 and _chromium_present():
        print("  ✓ Chromium installed — agent browsing is ready")
    else:
        print("  ⚠ Chromium download failed (exit "
              f"{proc.returncode}). Finish later with:  playwright install chromium")
