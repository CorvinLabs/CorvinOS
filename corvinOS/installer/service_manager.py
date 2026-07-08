"""Cross-platform service manager abstraction.

Supports:
- Linux: systemd user units (~/.config/systemd/user/)
- macOS: launchd plist (~Library/LaunchAgents/)
- Windows: Task Scheduler (schtasks.exe)

All services run in user-space (no sudo/elevation required).
"""

import json
import os
import platform
import shlex
import subprocess
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional


class ServiceManager(ABC):
    """Abstract base for platform-specific service management."""

    @abstractmethod
    def install_service(
        self,
        name: str,
        command: str,
        description: str = "",
        auto_start: bool = True,
        env_vars: Optional[dict] = None,
    ) -> None:
        """Register and optionally start a service."""
        pass

    @abstractmethod
    def start_service(self, name: str) -> None:
        """Start a service."""
        pass

    @abstractmethod
    def stop_service(self, name: str) -> None:
        """Stop a service."""
        pass

    @abstractmethod
    def enable_autostart(self, name: str) -> None:
        """Enable auto-start on system reboot."""
        pass

    @abstractmethod
    def disable_autostart(self, name: str) -> None:
        """Disable auto-start."""
        pass

    @abstractmethod
    def uninstall_service(self, name: str) -> None:
        """Remove/unregister a service."""
        pass

    @abstractmethod
    def status(self, name: str) -> str:
        """Return service status: 'running', 'stopped', 'not_found'."""
        pass

    @abstractmethod
    def is_active(self, name: str) -> bool:
        """Return True if service is currently running."""
        pass


class LinuxServiceManager(ServiceManager):
    """Linux systemd user-space service manager."""

    def __init__(self):
        self.systemd_user_dir = Path.home() / ".config" / "systemd" / "user"
        self.systemd_user_dir.mkdir(parents=True, exist_ok=True)

    def _service_file(self, name: str) -> Path:
        """Return path to systemd unit file."""
        return self.systemd_user_dir / f"corvin-{name}.service"

    def _run_systemctl(self, *args: str) -> None:
        """Run systemctl --user command."""
        cmd = ["systemctl", "--user"] + list(args)
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"systemctl failed: {result.stderr}")
        # Reload after unit changes
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)

    def _enable_linger(self) -> None:
        """Enable systemd-logind linger for the current user.

        Without linger, a `systemctl --user` unit only (re)starts once the
        user logs back in after a reboot — the Scheduled-Task/LaunchAgent
        equivalents on Windows/macOS don't have this gap, so a fresh Linux
        install would otherwise be the one platform that silently doesn't
        survive a headless reboot (ADR-0184 Stufe-1 fix). Idempotent and
        best-effort: some restricted/container environments have no
        systemd-logind at all, so a failure here must not abort the install
        — the unit still works normally whenever the user IS logged in.
        """
        user = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
        if not user:
            return
        try:
            check = subprocess.run(
                ["loginctl", "show-user", user],
                capture_output=True, text=True,
            )
            if check.returncode == 0 and "Linger=yes" in check.stdout:
                return  # already enabled
            subprocess.run(
                ["loginctl", "enable-linger", user],
                capture_output=True, text=True,
            )
        except OSError:
            # `loginctl` doesn't exist at all (no systemd-logind — minimal
            # containers, some non-systemd distros, WSL1). Best-effort: the
            # unit still works correctly whenever the user is logged in, so
            # this must not break service registration.
            pass

    def install_service(
        self,
        name: str,
        command: str,
        description: str = "",
        auto_start: bool = True,
        env_vars: Optional[dict] = None,
    ) -> None:
        """Create and enable a systemd user unit."""
        unit_file = self._service_file(name)

        service_lines = [
            "Type=simple",
        ]
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
            f"Description={description or f'Corvin {name} service'}",
            "After=network-online.target",
            # Bounded restart (ADR-0184 Stufe-1): without a start-limit, a unit
            # stuck in a crash loop restarts every RestartSec forever. Cap it
            # at 5 restarts per 5-minute window — after that systemd stops
            # trying and the failure is visible in `systemctl --user status`
            # instead of spinning silently.
            "StartLimitIntervalSec=300",
            "StartLimitBurst=5",
            "",
            "[Service]",
            *service_lines,
            "",
            "[Install]",
            "WantedBy=default.target",
        ]

        unit_file.write_text("\n".join(unit_lines) + "\n")
        unit_file.chmod(0o644)

        self._run_systemctl("daemon-reload")
        if auto_start:
            self._enable_linger()
            self.enable_autostart(name)

    def start_service(self, name: str) -> None:
        """Start a systemd user service."""
        self._run_systemctl("start", f"corvin-{name}.service")

    def stop_service(self, name: str) -> None:
        """Stop a systemd user service."""
        self._run_systemctl("stop", f"corvin-{name}.service")

    def enable_autostart(self, name: str) -> None:
        """Enable systemd user service auto-start."""
        self._run_systemctl("enable", f"corvin-{name}.service")

    def disable_autostart(self, name: str) -> None:
        """Disable systemd user service auto-start."""
        self._run_systemctl("disable", f"corvin-{name}.service")

    def uninstall_service(self, name: str) -> None:
        """Remove a systemd user service."""
        self.stop_service(name)
        self.disable_autostart(name)
        unit_file = self._service_file(name)
        unit_file.unlink(missing_ok=True)
        self._run_systemctl("daemon-reload")

    def status(self, name: str) -> str:
        """Return service status."""
        result = subprocess.run(
            ["systemctl", "--user", "is-active", f"corvin-{name}.service"],
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()

    def is_active(self, name: str) -> bool:
        """Check if service is running."""
        return self.status(name) == "active"


class DarwinServiceManager(ServiceManager):
    """macOS launchd service manager."""

    def __init__(self):
        self.launchagents_dir = Path.home() / "Library" / "LaunchAgents"
        self.launchagents_dir.mkdir(parents=True, exist_ok=True)

    def _plist_path(self, name: str) -> Path:
        """Return path to launchd plist file."""
        return self.launchagents_dir / f"com.corvin.{name}.plist"

    def _generate_plist(
        self, name: str, command: str, description: str,
        env_vars: Optional[dict] = None,
    ) -> str:
        """Generate launchd plist XML."""
        # WA-5: shlex.split so a program path with spaces stays one token.
        parts = shlex.split(command)
        program = parts[0]
        arguments = parts[1:] if len(parts) > 1 else []

        plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.corvin.{name}</string>
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
    <string>{Path.home() / '.corvin/logs/launchd'}/{name}.out</string>
    <key>StandardErrorPath</key>
    <string>{Path.home() / '.corvin/logs/launchd'}/{name}.err</string>
</dict>
</plist>
"""
        return plist

    def install_service(
        self,
        name: str,
        command: str,
        description: str = "",
        auto_start: bool = True,
        env_vars: Optional[dict] = None,
    ) -> None:
        """Create and load a launchd plist."""
        plist_file = self._plist_path(name)
        plist_content = self._generate_plist(name, command, description, env_vars)
        plist_file.write_text(plist_content)
        plist_file.chmod(0o644)

        if auto_start:
            self.enable_autostart(name)

    def start_service(self, name: str) -> None:
        """Start a launchd service."""
        subprocess.run(
            ["launchctl", "start", f"com.corvin.{name}"],
            check=True,
            capture_output=True,
        )

    def stop_service(self, name: str) -> None:
        """Stop a launchd service."""
        subprocess.run(
            ["launchctl", "stop", f"com.corvin.{name}"],
            capture_output=True,  # May fail if not running
        )

    def enable_autostart(self, name: str) -> None:
        """Load a launchd plist."""
        plist_file = self._plist_path(name)
        subprocess.run(
            ["launchctl", "load", str(plist_file)],
            check=True,
            capture_output=True,
        )

    def disable_autostart(self, name: str) -> None:
        """Unload a launchd plist."""
        plist_file = self._plist_path(name)
        subprocess.run(
            ["launchctl", "unload", str(plist_file)],
            capture_output=True,  # May fail if not loaded
        )

    def uninstall_service(self, name: str) -> None:
        """Remove a launchd service."""
        self.stop_service(name)
        self.disable_autostart(name)
        plist_file = self._plist_path(name)
        plist_file.unlink(missing_ok=True)

    def status(self, name: str) -> str:
        """Return service status (launchctl list)."""
        result = subprocess.run(
            ["launchctl", "list"],
            capture_output=True,
            text=True,
        )
        if f"com.corvin.{name}" in result.stdout:
            return "loaded"
        return "not_found"

    def is_active(self, name: str) -> bool:
        """Check if service is loaded."""
        result = subprocess.run(
            ["launchctl", "list"],
            capture_output=True,
            text=True,
        )
        return f"com.corvin.{name}" in result.stdout


def _dequote(token: str) -> str:
    """Strip one layer of surrounding double quotes from a token.

    M1: commands are built with the executable/path components double-quoted
    (e.g. ``"C:\\Users\\John Doe\\python.exe" -m uvicorn``) so a spaced path
    survives ``shlex.split(..., posix=False)`` as ONE token — but posix=False
    RETAINS those quotes. Strip them off the program token before it's re-quoted
    for the schtasks ``/tr`` string, so the /tr program is the clean path."""
    if len(token) >= 2 and token[0] == '"' and token[-1] == '"':
        return token[1:-1]
    return token


class WindowsServiceManager(ServiceManager):
    """Windows Task Scheduler service manager."""

    def _task_name(self, name: str) -> str:
        """Return full Task Scheduler task name."""
        return f"CorvinOS\\{name}"

    def _run_schtasks(self, *args: str) -> None:
        """Run schtasks.exe command."""
        cmd = ["schtasks"] + list(args)
        result = subprocess.run(cmd, capture_output=True, text=True, shell=True)
        if result.returncode != 0:
            raise RuntimeError(f"schtasks failed: {result.stderr}")

    def install_service(
        self,
        name: str,
        command: str,
        description: str = "",
        auto_start: bool = True,
        env_vars: Optional[dict] = None,
    ) -> None:
        """Create a Task Scheduler task."""
        task_name = self._task_name(name)

        # Create folder first
        subprocess.run(
            ["schtasks", "/create", "/tn", "CorvinOS", "/f"],
            capture_output=True,
        )

        # Create task.
        # WA-5/M1: quote the executable (first token) inside the /tr value so a
        # program path with spaces (e.g. C:\Program Files\...\python.exe) isn't
        # torn apart by Task Scheduler. Argument tokens keep their own double
        # quotes (a spaced script/adapter path stays one arg).
        # The command is passed as a LIST (shell=False): the double quotes are
        # LITERAL characters in the /tr argv element that schtasks stores, so
        # cmd.exe never parses them — a path containing a cmd metacharacter
        # (`&`, `|`, `(`, `)`, `^`) can no longer break out or inject. (An
        # f-string under shell=True mixed `\"`-toggling with cmd metachars.)
        parts = shlex.split(command, posix=False) or [command]
        program = _dequote(parts[0])
        tr_value = " ".join([f'"{program}"'] + list(parts[1:]))
        trigger = "onstart" if auto_start else "ondemand"
        subprocess.run(
            ["schtasks", "/create", "/tn", task_name, "/tr", tr_value,
             "/sc", trigger, "/f", "/rl", "highest"],
            check=True, capture_output=True,
        )

    def start_service(self, name: str) -> None:
        """Start a Task Scheduler task."""
        task_name = self._task_name(name)
        subprocess.run(
            ["schtasks", "/run", "/tn", task_name],
            shell=True,
            check=True,
            capture_output=True,
        )

    def stop_service(self, name: str) -> None:
        """Stop a Task Scheduler task."""
        task_name = self._task_name(name)
        subprocess.run(
            ["schtasks", "/end", "/tn", task_name],
            shell=True,
            capture_output=True,  # May fail if not running
        )

    def enable_autostart(self, name: str) -> None:
        """Enable the boot task (it already carries the /sc onstart trigger from
        install_service). Use /change /enable — NOT /tr "onstart": /tr sets the
        task's RUN COMMAND, so passing "onstart" there would clobber the program
        with a bogus command and break the task entirely."""
        task_name = self._task_name(name)
        subprocess.run(
            ["schtasks", "/change", "/tn", task_name, "/enable"],
            capture_output=True,
        )

    def disable_autostart(self, name: str) -> None:
        """Disable the boot task without deleting it (keeps its onstart trigger
        for a later re-enable). /disable, NOT /tr "ondemand" (see enable_autostart)."""
        task_name = self._task_name(name)
        subprocess.run(
            ["schtasks", "/change", "/tn", task_name, "/disable"],
            capture_output=True,
        )

    def uninstall_service(self, name: str) -> None:
        """Remove a Task Scheduler task."""
        task_name = self._task_name(name)
        self.stop_service(name)
        subprocess.run(
            ["schtasks", "/delete", "/tn", task_name, "/f"],
            capture_output=True,
        )

    def status(self, name: str) -> str:
        """Return task status."""
        task_name = self._task_name(name)
        result = subprocess.run(
            ["schtasks", "/query", "/tn", task_name],
            shell=True,
            capture_output=True,
            text=True,
        )
        if "Ready" in result.stdout:
            return "ready"
        elif "Running" in result.stdout:
            return "running"
        return "not_found"

    def is_active(self, name: str) -> bool:
        """Check if task is currently running."""
        return "Running" in self.status(name)


def get_service_manager() -> ServiceManager:
    """Return platform-appropriate ServiceManager instance."""
    system = platform.system()
    if system == "Darwin":
        return DarwinServiceManager()
    elif system == "Windows":
        return WindowsServiceManager()
    else:
        return LinuxServiceManager()
