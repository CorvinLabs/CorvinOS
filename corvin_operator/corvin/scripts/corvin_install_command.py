#!/usr/bin/env python3
"""Backend for the Claude Code slash command /corvin:install.

CorvinOS installs only from a local clone of the repository. This command
finds that clone (walking up from this script, then from the current
directory) and runs ITS installer — `install.sh` on Linux/macOS, `install.ps1`
on Windows. It never downloads an installer and never reimplements one.

What it adds on top of the installer:

1. A REAL health check. A console that booted before
   `core/console/corvin_console/web-next/dist/` existed mounts a PERMANENT
   503 "build failed" fallback route — `mount_static()` decides this once at
   boot (CLAUDE.md "Console Frontend — Restart rule"). This script polls the
   real HTTP response with a backed-off budget, declares success only on a
   genuine 200, and recognises the build-failed state immediately instead of
   waiting out the whole budget on a state that cannot self-heal.
2. Idempotency. If a previous install completed (onboarding flag) AND the
   console is live, the installer is not re-run — the console is only
   re-verified and opened. If the flag says done but the console is
   unhealthy, the cheaper `corvin-restore` is tried before a full re-install.

The installers open the browser themselves once the console is up, so after
a fresh install this command does not open a second tab.
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


def find_corvin_restore() -> str | None:
    return shutil.which("corvin-restore")


def _is_checkout(path: Path) -> bool:
    return all((path / name).is_file() for name in (".corvin_repo", "pyproject.toml", "install.sh"))


def find_checkout(start_points: list[Path] | None = None) -> Path | None:
    """The CorvinOS clone to install from. This script normally sits inside
    it (corvin_operator/corvin/scripts/); when Claude Code runs the plugin
    from its own plugin cache instead, the current directory is the clone the
    user opened Claude Code in."""
    if start_points is None:
        start_points = [Path(__file__).resolve(), Path.cwd().resolve()]
    for start in start_points:
        for candidate in (start, *start.parents):
            if _is_checkout(candidate):
                return candidate
    return None


def installer_command(checkout: Path) -> list[str]:
    if sys.platform == "win32":
        return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(checkout / "install.ps1")]
    return ["sh", str(checkout / "install.sh")]


CLONE_HINT = (
    "CorvinOS installs only from a local clone of the repository:\n"
    "  git clone https://github.com/CorvinLabs/CorvinOS.git\n"
    "  cd CorvinOS\n"
    "then start Claude Code in that directory and run /corvin:install again."
)


def run_installer(subprocess_run=subprocess.run, checkout: Path | None = None) -> tuple[bool, str]:
    """Run the checkout's own installer. Never downloads one."""
    checkout = checkout or find_checkout()
    if checkout is None:
        return False, "no CorvinOS checkout found.\n" + CLONE_HINT
    cmd = installer_command(checkout)
    print(f"→ running: {' '.join(cmd)}  (in {checkout})")
    try:
        proc = subprocess_run(
            cmd, cwd=str(checkout), stdin=subprocess.DEVNULL,
            timeout=_INSTALL_SUBPROCESS_TIMEOUT_S, check=False,
        )
    except subprocess.TimeoutExpired:
        return False, (
            f"the installer did not finish within {_INSTALL_SUBPROCESS_TIMEOUT_S:.0f}s "
            "— check its log and re-run /corvin:install"
        )
    if proc.returncode != 0:
        return False, f"the installer exited {proc.returncode} — see its output above"
    return True, "installer completed"


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


def main(subprocess_run=subprocess.run, argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv

    if "--check" in argv:
        # Read-only preview: a single short-budget probe of the console that
        # is ALREADY there (or isn't), never touches the installer and never
        # opens a browser. Exists so the real entry point (this script,
        # invoked as a subprocess exactly like Claude Code invokes it) can be
        # exercised end-to-end in a test without ever triggering a real
        # install. Exit codes: 0 = console is live right now, 3 = it is not
        # (distinct from the 1/2 failure codes of a real install run, so a
        # caller can tell "nothing to do" apart from "something broke").
        ok, detail = wait_for_real_console(total_budget_s=5.0)
        if ok:
            print(f"✓ console is live ({detail})")
            return 0
        print(f"… console not yet healthy: {detail}")
        return 3

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
        restore_bin = find_corvin_restore()
        if restore_bin:
            print(f"→ running: {restore_bin}")
            subprocess_run(
                [restore_bin], check=False, stdin=subprocess.DEVNULL,
                timeout=_INSTALL_SUBPROCESS_TIMEOUT_S,
            )
            ok, detail = wait_for_real_console()
            if ok:
                print(f"✓ console recovered via restore ({detail})")
                _report_open(open_console_browser())
                return 0
            print(f"  restore did not recover it ({detail}) — falling through to a full (re)install")
        else:
            print("  'corvin-restore' not on PATH — falling through to a full (re)install")

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
    print(f"  {CONSOLE_URL}  (the installer opened it in your browser when a display was available)")
    print("=" * 60)
    print("✓ CorvinOS setup complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
