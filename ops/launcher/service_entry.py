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
    return (
        f"{sys.executable} -m uvicorn corvin_gateway.app:app "
        "--host 127.0.0.1 --port 8765 --log-level info"
    )


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
            )
        except ElevationRequired as exc:
            print(f"  {exc}")
            return 1
        except Exception as exc:  # noqa: BLE001
            print(f"  Failed: {exc}")
            return 1
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
        print("  Removed. CorvinOS falls back to Stufe-1 (start-at-login) only.")
        return 0

    # status
    print(f"  always-on service status: {manager.status(name)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
