"""corvin-service — ADR-0184 Stufe 2: opt-in always-on system service.

Registers CorvinOS as a system-level service (systemd system unit / macOS
LaunchDaemon / Windows boot Scheduled Task) so it stays reachable even
across a headless reboot with no login. Requires admin/root — this is a
deliberate, explicit opt-in, never a silent default. The regular install
(Stufe 1) already starts CorvinOS automatically at login without any of
this; only reach for `corvin-service` if you run a headless box you never
log into.
"""
from __future__ import annotations

import argparse
import sys


def _webui_command() -> str:
    # Quote the interpreter path: all three SystemServiceManagers re-tokenize
    # this string (shlex.split / systemd ExecStart) — an unquoted
    # "C:\Users\John Doe\...\python.exe" tears at the space and the always-on
    # service can never start (same M1/WA-5 class already fixed in core.py).
    return (
        f'"{sys.executable}" -m uvicorn corvin_gateway.app:app '
        "--host 127.0.0.1 --port 8765 --log-level info"
    )


def _webui_env_vars() -> dict:
    """CORVIN_HOME (+ source-tree PYTHONPATH) for the always-on unit.

    Without these the Stufe-2 service resolves a different data root than
    the rest of the installation (reader≠writer class) and source-tree
    installs cannot import corvin_gateway at all.
    """
    env: dict = {}
    try:
        from corvinOS.shared.paths import corvin_home
        env["CORVIN_HOME"] = str(corvin_home())
    except Exception:  # noqa: BLE001
        pass
    try:
        from corvinOS.installer.core import _IS_WHEEL_INSTALL, _REPO_ROOT
        if not _IS_WHEEL_INSTALL:
            sep = ";" if sys.platform == "win32" else ":"
            dirs = [
                "core/console", "core/gateway", "core/license",
                "core/compliance", "operator/forge", "operator/skill-forge",
            ]
            paths = [str(_REPO_ROOT / d) for d in dirs if (_REPO_ROOT / d).exists()]
            if paths:
                env["PYTHONPATH"] = sep.join(paths)
    except Exception:  # noqa: BLE001
        pass
    return env


def _quiesce_stage1(stop_running: bool = True) -> None:
    """Best-effort: disable the Stufe-1 login autostart so it doesn't fight
    the new always-on service for port 8765.

    Both tiers bind 127.0.0.1:8765; leaving Stufe 1 registered makes the
    loser crash-loop at every login (Linux/Windows burn their bounded
    restart budget, macOS KeepAlive loops forever).

    ``stop_running`` controls whether the CURRENTLY running Stufe-1 instance
    is also stopped now:
      * True  — the `corvin-service` CLI, a SEPARATE process: safe to stop
        the running console immediately.
      * False — the Console PUT /settings/service-tier route, which may BE
        the running Stufe-1 process: stopping it now would kill the very
        request in flight. Disable-for-next-login only; the running console
        keeps serving until the next restart, where Stufe 2 takes over.

    Runs elevated, so user-level commands are dispatched into the invoking
    user's session where needed. Failures are reported, never fatal.
    """
    import os
    import subprocess
    from pathlib import Path

    def _report(what: str, hint: str) -> None:
        print(f"  ⚠ Could not disable the Stufe-1 login autostart ({what}).")
        print(f"    Disable it manually to avoid a port-8765 conflict: {hint}")

    try:
        if sys.platform == "win32":
            for tn in ("CorvinOS-Console",):
                if stop_running:
                    subprocess.run(["schtasks", "/end", "/tn", tn],
                                   capture_output=True, check=False)
                r = subprocess.run(["schtasks", "/change", "/tn", tn, "/disable"],
                                   capture_output=True, check=False)
                if r.returncode == 0:
                    print(f"  ✓ Stufe-1 login autostart disabled ({tn})")
                else:
                    _report(tn, f"schtasks /change /tn {tn} /disable")
        elif sys.platform == "darwin":
            user = os.environ.get("SUDO_USER") or os.environ.get("USER") or ""
            home = Path(f"/Users/{user}") if user else Path.home()
            plist = home / "Library" / "LaunchAgents" / "com.corvin.webui.plist"
            if plist.exists():
                # These paths run ELEVATED (install_service requires root), so
                # os.getuid() is 0 — useless for a per-USER LaunchAgent. Resolve
                # the invoking user's uid (SUDO_USER) and target their GUI
                # domain, otherwise the disable/bootout hits gui/0 and the
                # Stufe-1 KeepAlive agent keeps relaunching at every login.
                target_uid = None
                if user:
                    try:
                        import pwd
                        target_uid = pwd.getpwnam(user).pw_uid
                    except Exception:  # noqa: BLE001
                        target_uid = None
                label = "com.corvin.webui"
                if stop_running:
                    # bootout stops the running instance in the user's GUI
                    # domain; fall back to legacy unload -w if uid unknown.
                    if target_uid is not None:
                        argv = ["launchctl", "bootout", f"gui/{target_uid}/{label}"]
                    else:
                        argv = ["launchctl", "unload", "-w", str(plist)]
                else:
                    domain = f"gui/{target_uid}" if target_uid is not None else "gui"
                    argv = ["launchctl", "disable", f"{domain}/{label}"]
                r = subprocess.run(argv, capture_output=True, check=False)
                if r.returncode == 0:
                    print("  ✓ Stufe-1 login autostart disabled (LaunchAgent)")
                else:
                    _report("LaunchAgent", f"{' '.join(argv)}")
        else:
            disable_argv = ["systemctl", "--user", "disable"]
            if stop_running:
                disable_argv.append("--now")
            disable_argv.append("corvin-webui.service")
            user = os.environ.get("SUDO_USER")
            if user and user != "root":
                import pwd
                uid = pwd.getpwnam(user).pw_uid
                # List form (no shell) — the username is never re-parsed by a
                # shell, so a name with metacharacters can't inject.
                r = subprocess.run(
                    ["sudo", "-u", user,
                     f"XDG_RUNTIME_DIR=/run/user/{uid}", *disable_argv],
                    capture_output=True, check=False)
            else:
                r = subprocess.run(disable_argv, capture_output=True, check=False)
            if r.returncode == 0:
                print("  ✓ Stufe-1 login autostart disabled (corvin-webui user unit)")
            else:
                _report("systemd user unit",
                        " ".join(disable_argv))
    except Exception as exc:  # noqa: BLE001
        _report(type(exc).__name__, "see docs (ADR-0184)")


def main() -> int:
    from corvinOS.installer.system_service_manager import (
        ElevationRequired,
        current_user,
        get_system_service_manager,
        is_elevated,
    )

    parser = argparse.ArgumentParser(
        prog="corvin-service",
        description=(
            "Opt-in ALWAYS-ON mode (ADR-0184 Stufe 2): registers CorvinOS as a "
            "system-level service that survives a reboot even if nobody ever "
            "logs in. Requires admin/root."
        ),
    )
    parser.add_argument("action", choices=["install", "uninstall", "status"])
    args = parser.parse_args()

    manager = get_system_service_manager()
    name = "webui"

    if args.action in ("install", "uninstall") and not is_elevated():
        print("  This command needs administrator/root privileges.")
        print("  Nothing was changed. Re-run as admin/root:")
        if sys.platform == "win32":
            print(f"    (open an elevated PowerShell) corvin-service {args.action}")
        else:
            print(f"    sudo corvin-service {args.action}")
        return 1

    if args.action == "install":
        print(f"Registering CorvinOS as an always-on system service (runs as {current_user()}) ...")
        try:
            manager.install_service(
                name=name,
                command=_webui_command(),
                description="CorvinOS WebUI — always-on (ADR-0184 Stufe 2)",
                env_vars=_webui_env_vars(),
            )
        except ElevationRequired as exc:
            print(f"  {exc}")
            return 1
        except Exception as exc:  # noqa: BLE001
            print(f"  Failed: {exc}")
            return 1
        _quiesce_stage1()
        print("  Done. CorvinOS will now start at boot even without a login.")
        print("  Undo with: corvin-service uninstall")
        return 0

    if args.action == "uninstall":
        try:
            manager.uninstall_service(name)
        except ElevationRequired as exc:
            print(f"  {exc}")
            return 1
        except Exception as exc:  # noqa: BLE001
            print(f"  Failed: {exc}")
            return 1
        print("  Removed. To restore the Stufe-1 (start-at-login) autostart")
        print("  that was disabled when always-on mode was installed:")
        if sys.platform == "win32":
            print("    schtasks /change /tn CorvinOS-Console /enable")
        elif sys.platform == "darwin":
            print("    launchctl load ~/Library/LaunchAgents/com.corvin.webui.plist")
        else:
            print("    systemctl --user enable --now corvin-webui.service")
        return 0

    # status
    print(f"  always-on service status: {manager.status(name)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
