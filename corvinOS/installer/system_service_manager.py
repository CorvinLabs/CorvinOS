"""System-level (root/admin) service registration — ADR-0184 Stufe 2.

Opt-in "always-on, survives a headless reboot with zero login" mode. This is
a DIFFERENT tier from ``service_manager.py`` (Stufe 1: user-level, no
elevation, on by default) — see ADR-0184 (Corvin-ADR/decisions/0184-*.md)
for the two-tier model and why they must never be conflated.

Must NOT do (ADR-0184):
- Never register a system-level service without the caller having gone
  through an explicit, visible consent step (the ``--always-on`` install
  flag or the standalone ``corvin-service install`` command). This module
  is never invoked as part of the default install flow.
- Never run CorvinOS as root/SYSTEM under this mode. Every implementation
  here configures the OS to launch the process AS THE INSTALLING USER
  (``current_user()``), so ``~/.corvin`` / ``~/.config/corvin-voice``
  keep their existing per-user ownership (ADR-0007).
"""
from __future__ import annotations

import getpass
import os
import platform
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional


def is_elevated() -> bool:
    """True if the current process has admin (Windows) / root (POSIX) rights."""
    system = platform.system()
    if system == "Windows":
        try:
            import ctypes
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:  # noqa: BLE001
            return False
    try:
        return os.geteuid() == 0
    except AttributeError:
        return False


def current_user() -> str:
    """The installing user's login name — the account the service must run as."""
    return getpass.getuser()


class ElevationRequired(RuntimeError):
    """Raised when Stufe-2 registration/removal is attempted without elevation."""


class SystemServiceManager(ABC):
    """Abstract base for platform-specific ALWAYS-ON (system-level) services.

    Every ``install_service()`` implementation must configure the service to
    run as ``current_user()`` — never as root/SYSTEM/LocalSystem.
    """

    @abstractmethod
    def install_service(
        self,
        name: str,
        command: str,
        description: str = "",
        env_vars: Optional[dict] = None,
    ) -> None: ...

    @abstractmethod
    def uninstall_service(self, name: str) -> None: ...

    @abstractmethod
    def status(self, name: str) -> str: ...


class LinuxSystemServiceManager(SystemServiceManager):
    """Linux systemd SYSTEM unit under /etc/systemd/system, User=<installing user>.

    Unlike the Stufe-1 ``LinuxServiceManager`` (systemd --user), this unit is
    owned by root and starts at boot regardless of any login — but the
    ``User=`` directive makes systemd fork the actual process under the
    installing user's UID/GID, so it still only ever touches that user's
    ``~/.corvin`` with normal (non-root) permissions.
    """

    UNIT_DIR = Path("/etc/systemd/system")

    def _unit_file(self, name: str) -> Path:
        return self.UNIT_DIR / f"corvin-{name}.service"

    def install_service(
        self,
        name: str,
        command: str,
        description: str = "",
        env_vars: Optional[dict] = None,
    ) -> None:
        if not is_elevated():
            raise ElevationRequired(
                "Registering a system-wide systemd unit requires root. "
                "Re-run with: sudo corvin-service install"
            )
        user = current_user()
        service_lines = ["Type=simple", f"User={user}"]
        for key, value in (env_vars or {}).items():
            service_lines.append(f"Environment={key}={value}")
        service_lines += [
            f"ExecStart={command}",
            "Restart=on-failure",
            "RestartSec=10",
            "StandardOutput=journal",
            "StandardError=journal",
        ]
        unit_lines = [
            "[Unit]",
            f"Description={description or f'Corvin {name} service (always-on)'}",
            "After=network-online.target",
            # ADR-0184 Stufe-1's bounded-restart cap, applied here too — same
            # 5-per-300s shape as the user-level unit in service_manager.py.
            "StartLimitIntervalSec=300",
            "StartLimitBurst=5",
            "",
            "[Service]",
            *service_lines,
            "",
            "[Install]",
            "WantedBy=multi-user.target",
        ]
        unit_file = self._unit_file(name)
        unit_file.write_text("\n".join(unit_lines) + "\n")
        unit_file.chmod(0o644)
        subprocess.run(["systemctl", "daemon-reload"], check=True)
        subprocess.run(
            ["systemctl", "enable", "--now", f"corvin-{name}.service"], check=True,
        )

    def uninstall_service(self, name: str) -> None:
        if not is_elevated():
            raise ElevationRequired(
                "Removing a system-wide systemd unit requires root. "
                "Re-run with: sudo corvin-service uninstall"
            )
        subprocess.run(
            ["systemctl", "disable", "--now", f"corvin-{name}.service"],
            capture_output=True,
        )
        self._unit_file(name).unlink(missing_ok=True)
        subprocess.run(["systemctl", "daemon-reload"], capture_output=True)

    def status(self, name: str) -> str:
        result = subprocess.run(
            ["systemctl", "is-active", f"corvin-{name}.service"],
            capture_output=True, text=True,
        )
        return result.stdout.strip() or "not_found"


class DarwinSystemServiceManager(SystemServiceManager):
    """macOS LaunchDaemon under /Library/LaunchDaemons, UserName=<installing user>.

    Unlike the Stufe-1 ``DarwinServiceManager`` (a per-user LaunchAgent,
    inherently gated on that user logging in), a LaunchDaemon is loaded by
    launchd at boot regardless of any login. The ``UserName`` key makes
    launchd setuid() to the installing user before running the program —
    launchd itself stays root, the actual CorvinOS process does not.
    """

    DAEMON_DIR = Path("/Library/LaunchDaemons")

    def _plist_path(self, name: str) -> Path:
        return self.DAEMON_DIR / f"com.corvin.{name}.always-on.plist"

    def install_service(
        self,
        name: str,
        command: str,
        description: str = "",
        env_vars: Optional[dict] = None,
    ) -> None:
        if not is_elevated():
            raise ElevationRequired(
                "Registering a LaunchDaemon requires root. "
                "Re-run with: sudo corvin-service install"
            )
        user = current_user()
        parts = command.split()
        program = parts[0]
        arguments = parts[1:] if len(parts) > 1 else []

        plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.corvin.{name}.always-on</string>
    <key>UserName</key>
    <string>{user}</string>
    <key>Program</key>
    <string>{program}</string>
"""
        if arguments:
            plist += "    <key>ProgramArguments</key>\n    <array>\n"
            plist += f"        <string>{program}</string>\n"
            for arg in arguments:
                plist += f"        <string>{arg}</string>\n"
            plist += "    </array>\n"
        if env_vars:
            plist += "    <key>EnvironmentVariables</key>\n    <dict>\n"
            for key, value in env_vars.items():
                plist += f"        <key>{key}</key>\n        <string>{value}</string>\n"
            plist += "    </dict>\n"
        plist += f"""    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>ThrottleInterval</key>
    <integer>60</integer>
    <key>StandardOutPath</key>
    <string>/var/log/corvin-{name}-always-on.out.log</string>
    <key>StandardErrorPath</key>
    <string>/var/log/corvin-{name}-always-on.err.log</string>
</dict>
</plist>
"""
        plist_file = self._plist_path(name)
        plist_file.write_text(plist)
        plist_file.chmod(0o644)
        subprocess.run(["launchctl", "load", str(plist_file)], check=True)

    def uninstall_service(self, name: str) -> None:
        if not is_elevated():
            raise ElevationRequired(
                "Removing a LaunchDaemon requires root. "
                "Re-run with: sudo corvin-service uninstall"
            )
        plist_file = self._plist_path(name)
        subprocess.run(["launchctl", "unload", str(plist_file)], capture_output=True)
        plist_file.unlink(missing_ok=True)

    def status(self, name: str) -> str:
        result = subprocess.run(["launchctl", "list"], capture_output=True, text=True)
        return "loaded" if f"com.corvin.{name}.always-on" in result.stdout else "not_found"


class WindowsSystemServiceManager(SystemServiceManager):
    """Windows Scheduled Task at boot (`/sc onstart`), running as the
    installing user via S4U logon (`/np` — "no password").

    `/np` deliberately avoids the two bad options a naive implementation
    would otherwise face: storing the user's password ourselves, or running
    as SYSTEM (which ADR-0184 explicitly forbids). S4U grants the task
    access to local resources under that user's identity without needing —
    or ever seeing — a password, which is exactly what "start before any
    interactive logon, as this specific user" needs. Registering an
    onstart task requires an elevated (Run as Administrator) session.
    """

    def _task_name(self, name: str) -> str:
        return f"CorvinOS-AlwaysOn-{name}"

    def install_service(
        self,
        name: str,
        command: str,
        description: str = "",
        env_vars: Optional[dict] = None,
    ) -> None:
        if not is_elevated():
            raise ElevationRequired(
                "Registering a boot-time Scheduled Task requires an elevated "
                "PowerShell. Re-run from an admin PowerShell: corvin-service install"
            )
        # Windows Scheduled Task actions have no native per-task environment
        # variable mechanism (unlike systemd Environment= / launchd
        # EnvironmentVariables) — env_vars is accepted for interface parity
        # with the other managers but not yet applied here. A future
        # iteration could wrap `command` in a small generated .cmd/.ps1 that
        # sets them before exec, mirroring corvin-supervisor.ps1's approach.
        user = current_user()
        task_name = self._task_name(name)
        cmd = [
            "schtasks", "/create", "/tn", task_name,
            "/tr", command, "/sc", "onstart",
            "/ru", user, "/np", "/rl", "highest", "/f",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"schtasks failed: {result.stderr}")

    def uninstall_service(self, name: str) -> None:
        if not is_elevated():
            raise ElevationRequired(
                "Removing this Scheduled Task requires an elevated "
                "PowerShell. Re-run from an admin PowerShell: corvin-service uninstall"
            )
        subprocess.run(
            ["schtasks", "/delete", "/tn", self._task_name(name), "/f"],
            capture_output=True,
        )

    def status(self, name: str) -> str:
        result = subprocess.run(
            ["schtasks", "/query", "/tn", self._task_name(name)],
            capture_output=True, text=True,
        )
        return "registered" if result.returncode == 0 else "not_found"


def get_system_service_manager() -> SystemServiceManager:
    """Return the platform-appropriate always-on SystemServiceManager."""
    system = platform.system()
    if system == "Darwin":
        return DarwinSystemServiceManager()
    elif system == "Windows":
        return WindowsSystemServiceManager()
    else:
        return LinuxSystemServiceManager()
