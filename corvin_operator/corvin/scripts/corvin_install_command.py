#!/usr/bin/env python3
"""Backend for the Claude Code slash command /corvin:install.

Wraps the existing `corvin-install` CLI (corvinOS.installer.core.CorvinInstaller,
19 steps) with the two guarantees that CLI does not give on its own — this
script never reimplements the installer, it drives it to a verified end-state:

1. A REAL health check after startup. `corvin-install`'s own step_17 only
   polls "is TCP port 8765 open" for up to 15s. A console that booted before
   `core/console/corvin_console/web-next/dist/` existed mounts a PERMANENT
   503 "build failed" fallback route — `mount_static()` decides this once at
   boot (see CLAUDE.md "Console Frontend — Prove the NEW Build Is What
   Loads" / "Restart rule"). Port-open therefore does not mean "the console
   works", and 15s does not mean "never coming up" (a cold `npm install &&
   npm run build` can take minutes). This script polls the real HTTP
   response with its own, longer, backed-off budget and only declares
   success on a genuine 200 — and recognizes the build-failed state
   immediately instead of waiting out the full budget on a state that
   cannot self-heal.
2. Opening the browser. `step_18_finalise()` prints the console URL but
   never calls `webbrowser.open()` — that is the literal gap this command
   closes.

Idempotent: if a previous run already completed (onboarding flag) AND the
console is live, this does not re-run the 19-step installer — it only
re-verifies and opens the browser. If the flag says done but the console is
unhealthy, it tries the cheaper `corvin-installer restore` before falling
through to a full (re)install.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

# Reuse the installer's OWN path-resolution helpers rather than re-deriving
# the onboarding-flag path independently — two modules independently
# computing "the same" path is exactly the bug class that cost hours of
# live debugging before (see memory
# feedback-corvinos-dual-host-and-path-divergence): it only looks identical
# inside a repo checkout and silently diverges in an installed (uv-tool)
# environment.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
try:
    from corvinOS.shared.paths import voice_config_dir as _voice_config_dir
except ImportError:
    _voice_config_dir = None

CONSOLE_HOST = "127.0.0.1"
CONSOLE_PORT = 8765
CONSOLE_URL = f"http://{CONSOLE_HOST}:{CONSOLE_PORT}/console/"

# Generous on purpose: the operator asked for "runs to the stable end-state,
# doesn't abort before it" — these are ceilings against a genuine hang, not
# targets to race against.
_VERIFY_TOTAL_BUDGET_S = 300.0
_VERIFY_STEP_CAP_S = 5.0
_INSTALL_SUBPROCESS_TIMEOUT_S = 1800.0


class ConsoleState:
    HEALTHY = "healthy"
    BUILD_FAILED = "build_failed"  # live process, permanent 503 fallback
    UNREACHABLE = "unreachable"    # nothing listening / connection refused / other error


def probe_console(url: str = CONSOLE_URL) -> tuple[str, int | None, str]:
    """One HTTP probe. Returns (state, status_code|None, detail)."""
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            if resp.status == 200:
                return ConsoleState.HEALTHY, resp.status, ""
            return ConsoleState.UNREACHABLE, resp.status, ""
    except urllib.error.HTTPError as e:
        try:
            body = e.read(4096).decode("utf-8", errors="replace")
        except Exception:
            body = ""
        if e.code == 503 and "build failed" in body:
            return ConsoleState.BUILD_FAILED, e.code, body[:200]
        return ConsoleState.UNREACHABLE, e.code, body[:200]
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        return ConsoleState.UNREACHABLE, None, str(e)


def wait_for_real_console(
    total_budget_s: float = _VERIFY_TOTAL_BUDGET_S,
    url: str = CONSOLE_URL,
    _sleep=time.sleep,
    _clock=time.monotonic,
) -> tuple[bool, str]:
    """The ONE retry/backoff primitive. Every call site (fresh install,
    idempotent re-check, post-restore re-check) goes through this function
    instead of its own ad-hoc sleep loop — see memory
    feedback-recurring-race-fix-the-primitive-not-the-callsite: a bug class
    found across 2-3 independent call sites belongs in the primitive, not
    patched at each site. Returns (ok, detail_message)."""
    deadline = _clock() + total_budget_s
    delay = 1.0
    attempt = 0
    last_detail = ""
    while _clock() < deadline:
        attempt += 1
        state, _code, detail = probe_console(url)
        if state == ConsoleState.HEALTHY:
            return True, f"responded 200 after {attempt} attempt(s)"
        if state == ConsoleState.BUILD_FAILED:
            # Known-permanent-until-restart (mount_static decides once at
            # boot) — no amount of waiting fixes this. Stop early with the
            # precise remedy instead of burning the whole budget.
            return False, (
                "console is reachable but serving the permanent 503 "
                "'build failed' fallback (it booted before dist/ existed) — "
                "a rebuild alone will NOT fix this, only a restart will: "
                "systemctl --user restart corvin-webui"
            )
        last_detail = detail
        _sleep(delay)
        delay = min(delay * 1.5, _VERIFY_STEP_CAP_S)
    return False, (
        f"no healthy response within {total_budget_s:.0f}s "
        f"(last: {last_detail or 'connection refused'})"
    )


def onboarding_flag_path() -> Path | None:
    if _voice_config_dir is None:
        return None
    try:
        return _voice_config_dir() / ".corvin_setup_complete"
    except Exception:
        return None


def already_onboarded() -> bool:
    flag = onboarding_flag_path()
    return bool(flag and flag.exists())


def find_corvin_install() -> str | None:
    return shutil.which("corvin-install")


def find_corvin_installer() -> str | None:
    return shutil.which("corvin-installer")


def find_repo_install_sh() -> Path | None:
    """Walk up from THIS script looking for the checkout's own install.sh.
    This plugin ships inside the CorvinOS repo
    (corvin_operator/corvin/scripts/...), so the common cold-start case is:
    repo cloned, this marketplace added from it, `corvinos` not yet
    `uv tool install`-ed. Checked against both install.sh AND the corvinOS/
    package directory (not just pyproject.toml, which plenty of unrelated
    repos also have) to avoid a false-positive match on an unrelated parent
    checkout."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "install.sh"
        if candidate.exists() and (parent / "corvinOS").is_dir():
            return candidate
    return None


def run_installer(subprocess_run=subprocess.run) -> tuple[bool, str]:
    """Invoke the real, existing installer. Never reimplements its steps —
    one install mechanism, not a second parallel one."""
    corvin_install = find_corvin_install()
    if corvin_install:
        print(f"→ running: {corvin_install} --yes")
        try:
            proc = subprocess_run(
                [corvin_install, "--yes"],
                timeout=_INSTALL_SUBPROCESS_TIMEOUT_S,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return False, (
                f"corvin-install did not finish within "
                f"{_INSTALL_SUBPROCESS_TIMEOUT_S:.0f}s — check the install "
                f"log and re-run /corvin:install"
            )
        if proc.returncode != 0:
            return False, f"corvin-install exited {proc.returncode} — see its output above"
        return True, "corvin-install completed"

    install_sh = find_repo_install_sh()
    if install_sh:
        print(f"→ running: sh {install_sh} --yes")
        try:
            proc = subprocess_run(
                ["sh", str(install_sh), "--yes"],
                timeout=_INSTALL_SUBPROCESS_TIMEOUT_S,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return False, (
                f"install.sh did not finish within "
                f"{_INSTALL_SUBPROCESS_TIMEOUT_S:.0f}s"
            )
        if proc.returncode != 0:
            return False, f"install.sh exited {proc.returncode} — see its output above"
        return True, "install.sh completed"

    return False, (
        "neither 'corvin-install' nor a repo-local install.sh was found — "
        "bootstrap CorvinOS first:  curl -fsSL https://corvin-labs.com/install.sh | sh"
    )


def open_console_browser(url: str = CONSOLE_URL) -> bool:
    try:
        return bool(webbrowser.open(url))
    except Exception:
        return False


def _report_open(opened: bool, url: str = CONSOLE_URL) -> None:
    if opened:
        print(f"✓ opened {url} in your browser")
    else:
        print("⚠ could not auto-open a browser from this environment "
              "(no display reachable, e.g. a headless bridge session)")
        print(f"  open this yourself: {url}")


def main(subprocess_run=subprocess.run) -> int:
    print("CorvinOS setup (/corvin:install)")
    print("=" * 60)

    if already_onboarded():
        print("✓ onboarding flag present — verifying the live console instead of reinstalling")
        ok, detail = wait_for_real_console(total_budget_s=30.0)
        if ok:
            print(f"✓ console already up ({detail})")
            _report_open(open_console_browser())
            return 0

        print(f"⚠ onboarding flag says done, but the console is not healthy: {detail}")
        restore_bin = find_corvin_installer()
        if restore_bin:
            print(f"→ running: {restore_bin} restore")
            subprocess_run(
                [restore_bin, "restore"], check=False,
                timeout=_INSTALL_SUBPROCESS_TIMEOUT_S,
            )
            ok, detail = wait_for_real_console()
            if ok:
                print(f"✓ console recovered via restore ({detail})")
                _report_open(open_console_browser())
                return 0
            print(f"  restore did not recover it ({detail}) — falling through to a full (re)install")
        else:
            print("  'corvin-installer' not on PATH — falling through to a full (re)install")

    ok, detail = run_installer(subprocess_run=subprocess_run)
    if not ok:
        print(f"✗ install failed: {detail}")
        return 1

    print("→ verifying the console actually serves the real console (not just 'port open')...")
    ok, detail = wait_for_real_console()
    if not ok:
        print(f"✗ console never became healthy: {detail}")
        return 2

    print(f"✓ console is live ({detail})")
    _report_open(open_console_browser())
    print("=" * 60)
    print("✓ CorvinOS setup complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
