#!/usr/bin/env python3
"""Backend for the Claude Code slash command /corvin:update.

Three steps, nothing reimplemented:

1. Version check — `git fetch` the checkout's origin and compare HEAD with
   origin/main (commits behind/ahead, pyproject version on both sides).
   `--check` stops here and changes nothing.
2. Update — run the checkout's own `update.sh` (Windows: `update.ps1`). That
   script pulls, reinstalls, rebuilds the console, restarts, proves the new
   build is served and rolls back on failure. Its exit code is passed through.
3. Verify — the same real HTTP health check `/corvin:install` uses.

Configuration is not migrated here because the update never touches it:
`<checkout>/.corvin/` is git-ignored (a pull or stash leaves it alone, the
updater carries it across a tarball swap) and `~/.config/corvin-voice` is
outside the checkout; format changes are handled by the code that reads it.

Exit codes: 0 updated (or already current), 1 update failed and was rolled
back / no checkout / fetch failed, 2 rollback failed too, 3 another
install/update holds the lock, 4 (`--check` only) an update is available.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import corvin_install_command as cic  # noqa: E402  (shared checkout + health primitives)

_FETCH_TIMEOUT_S = 120.0
_UPDATE_TIMEOUT_S = 1800.0
BRANCH = "main"


@dataclass
class VersionStatus:
    branch: str
    head: str
    remote: str
    behind: int
    ahead: int
    local_version: str
    remote_version: str


def _git(checkout: Path, *args: str, timeout: float = 30.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(checkout), *args],
        capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL,
    )


def _pyproject_version(text: str) -> str:
    in_project = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            in_project = stripped == "[project]"
        elif in_project and stripped.startswith("version") and "=" in stripped:
            return stripped.split("=", 1)[1].strip().strip('"').strip("'")
    return "?"


def check_version(checkout: Path) -> tuple[VersionStatus | None, str]:
    fetch = _git(checkout, "fetch", "--quiet", "origin", BRANCH, timeout=_FETCH_TIMEOUT_S)
    if fetch.returncode != 0:
        return None, f"git fetch failed: {(fetch.stderr or fetch.stdout).strip()[:300]}"
    remote_ref = f"origin/{BRANCH}"
    counts = _git(checkout, "rev-list", "--left-right", "--count", f"{remote_ref}...HEAD")
    if counts.returncode != 0:
        return None, f"cannot compare with {remote_ref}: {counts.stderr.strip()[:300]}"
    behind, ahead = (int(n) for n in counts.stdout.split())
    head = _git(checkout, "rev-parse", "--short", "HEAD").stdout.strip()
    remote = _git(checkout, "rev-parse", "--short", remote_ref).stdout.strip()
    branch = _git(checkout, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    local_text = (checkout / "pyproject.toml").read_text(encoding="utf-8", errors="replace")
    remote_text = _git(checkout, "show", f"{remote_ref}:pyproject.toml").stdout
    return VersionStatus(
        branch=branch, head=head, remote=remote, behind=behind, ahead=ahead,
        local_version=_pyproject_version(local_text),
        remote_version=_pyproject_version(remote_text),
    ), ""


def describe(status: VersionStatus) -> str:
    line = (f"checkout {status.head} ({status.local_version}) on {status.branch}, "
            f"origin/{BRANCH} {status.remote} ({status.remote_version})")
    if status.behind == 0:
        return line + " — up to date"
    return line + f" — {status.behind} commit(s) behind"


def updater_command(checkout: Path) -> list[str]:
    if sys.platform == "win32":
        # A subprocess has no interactive session whose policy it could inherit.
        return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(checkout / "update.ps1")]
    return ["sh", str(checkout / "update.sh")]


def run_updater(checkout: Path, subprocess_run=subprocess.run) -> int:
    cmd = updater_command(checkout)
    print(f"→ running: {' '.join(cmd)}  (in {checkout})")
    try:
        proc = subprocess_run(cmd, cwd=str(checkout), stdin=subprocess.DEVNULL,
                              timeout=_UPDATE_TIMEOUT_S, check=False)
    except subprocess.TimeoutExpired:
        print(f"✗ the updater did not finish within {_UPDATE_TIMEOUT_S:.0f}s")
        return 1
    return proc.returncode


def marketplace_name(checkout: Path) -> str:
    import json
    try:
        return json.loads((checkout / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))["name"]
    except Exception:  # noqa: BLE001 — only used in a hint
        return "<marketplace>"


_UPDATER_FAILURES = {
    1: "update failed and was rolled back — the previous version is running",
    2: "update failed and the rollback failed too — repair by re-running the installer from the checkout",
    3: "another CorvinOS install/update is running — try again when it has finished",
}


def main(argv: list[str] | None = None, subprocess_run=subprocess.run) -> int:
    parser = argparse.ArgumentParser(prog="corvin_update_command")
    parser.add_argument("--check", action="store_true", help="only report whether an update is available")
    parser.add_argument("--force", action="store_true", help="run the updater even when already up to date")
    parser.add_argument("--checkout", type=Path, help="CorvinOS checkout (default: auto-detect)")
    args = parser.parse_args(argv)

    checkout = args.checkout.resolve() if args.checkout else cic.find_checkout()
    if checkout is None or not cic._is_checkout(checkout):
        print("✗ no CorvinOS checkout found.\n" + cic.CLONE_HINT)
        return 1

    status, error = check_version(checkout)
    if status is None:
        print(f"✗ version check failed: {error}")
        return 1
    print(describe(status))

    if args.check:
        return 0 if status.behind == 0 else 4

    if status.branch != BRANCH:
        print(f"⚠ the checkout is on '{status.branch}', not {BRANCH}: the updater leaves its code "
              "alone and only refreshes dependencies, console and services")
    if status.behind == 0 and not args.force:
        ok, detail = cic.wait_for_real_console(total_budget_s=10.0)
        print(f"✓ nothing to update; console {'is live' if ok else 'is NOT healthy: ' + detail}")
        return 0 if ok else 2

    rc = run_updater(checkout, subprocess_run=subprocess_run)
    if rc != 0:
        print(f"✗ {_UPDATER_FAILURES.get(rc, f'updater exited {rc} — see its output above')}")
        return rc if rc in _UPDATER_FAILURES else 1

    ok, detail = cic.wait_for_real_console()
    if not ok:
        print(f"✗ the updater reported success, but the console is not healthy: {detail}")
        return 2
    after = _git(checkout, "rev-parse", "--short", "HEAD").stdout.strip()
    print(f"✓ updated {status.head} → {after} ({status.local_version} → {status.remote_version}); console is live")
    print("  Reload open console tabs with Ctrl+Shift+R. Configuration was kept as is.")
    print("  This command's own files changed with the checkout — refresh Claude Code's copy with "
          f"/plugin marketplace update {marketplace_name(checkout)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
