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
import shlex
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
    """The installing user's login name — the account the service must run as.

    WA-1: under ``sudo corvin-service install`` the process EUID is 0, so a bare
    ``getpass.getuser()`` resolves to ``root`` — which would write ``User=root``
    / ``UserName=root`` into the unit, exactly the "run as root" outcome
    ADR-0184 forbids. When elevated on POSIX, recover the *invoking* user from
    ``SUDO_USER`` / ``PKEXEC_USER`` instead; if that still resolves to root
    (e.g. a direct root login with no sudo context), REFUSE rather than silently
    register a root-owned always-on service.
    """
    try:
        euid = os.geteuid()
    except AttributeError:
        euid = None  # Windows: no EUID concept — getpass.getuser() is correct.
    if euid == 0:
        for var in ("SUDO_USER", "PKEXEC_USER"):
            candidate = (os.environ.get(var) or "").strip()
            if candidate and candidate != "root":
                return candidate
        raise RuntimeError(
            "Refusing to register an always-on service as root (ADR-0184 forbids "
            "running CorvinOS as root/SYSTEM). Run `corvin-service install` via "
            "`sudo` from your normal user account so SUDO_USER is set — do not run "
            "it directly as the root user."
        )
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
        pre_exec: Optional[str] = None,
    ) -> None:
        """``pre_exec``, when given, runs once before ``command`` on every
        (re)start — best-effort, never blocks the service. Used for the
        WA-19 PyPI auto-update check: an always-on service is exactly the
        case a user never manually restarts, so without this it can run a
        stale release indefinitely."""
        ...

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
        pre_exec: Optional[str] = None,
    ) -> None:
        if not is_elevated():
            raise ElevationRequired(
                "Registering a system-wide systemd unit requires root. "
                "Re-run with: sudo corvin-service install"
            )
        user = current_user()
        service_lines = ["Type=simple", f"User={user}"]
        for key, value in (env_vars or {}).items():
            # Quote the value: systemd splits an unquoted Environment=
            # assignment at whitespace, so a repo/home path with a space
            # (PYTHONPATH, CORVIN_HOME) would be truncated. Escape any
            # embedded quote/backslash per systemd's rules.
            _esc = value.replace("\\", "\\\\").replace('"', '\\"')
            service_lines.append(f'Environment="{key}={_esc}"')
        if pre_exec:
            # WA-19: "-" prefix so a failed/offline check never blocks ExecStart.
            service_lines.append(f"ExecStartPre=-{pre_exec}")
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
        pre_exec: Optional[str] = None,
    ) -> None:
        if not is_elevated():
            raise ElevationRequired(
                "Registering a LaunchDaemon requires root. "
                "Re-run with: sudo corvin-service install"
            )
        user = current_user()
        # WA-5: shlex.split (not str.split) so a Program path containing spaces
        # stays one token instead of being torn into bogus ProgramArguments.
        parts = shlex.split(command)
        program = parts[0]
        arguments = parts[1:] if len(parts) > 1 else []

        if pre_exec:
            # WA-19: LaunchDaemon has no ExecStartPre equivalent — wrap into a
            # shell that runs the (best-effort) update check then execs the
            # real command, same pattern as the Stufe-1 LaunchAgent.
            quoted_cmd = " ".join(shlex.quote(p) for p in ([program] + arguments))
            program = "/bin/bash"
            arguments = ["-c", f"{pre_exec}; exec {quoted_cmd}"]

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


def _ps_single_quote(s: str) -> str:
    """Wrap *s* in a PowerShell single-quoted literal (doubling any embedded
    single quotes). Single-quoted PS strings are fully literal — no `$`/backtick
    interpolation — so this is the safe way to embed a path or arg that may
    contain spaces or metacharacters (WA-5)."""
    return "'" + s.replace("'", "''") + "'"


def _dequote(token: str) -> str:
    """Strip one layer of surrounding double quotes from a token.

    M1: commands are built with the executable/path components double-quoted
    (e.g. ``"C:\\Users\\John Doe\\python.exe" -m uvicorn``) so a spaced path
    survives ``shlex.split(..., posix=False)`` as ONE token — but posix=False
    RETAINS those quotes in the token. Strip them here before the path is
    re-quoted for PowerShell, so the ``-Execute`` value is the clean path (not
    a path with literal quote characters baked in)."""
    if len(token) >= 2 and token[0] == '"' and token[-1] == '"':
        return token[1:-1]
    return token


class WindowsSystemServiceManager(SystemServiceManager):
    """Windows boot-time Scheduled Task, registered via PowerShell
    ``Register-ScheduledTask`` so it can carry a BOUNDED restart-on-failure
    policy (``-RestartCount``/``-RestartInterval``) — the systemd
    ``Restart=on-failure`` / launchd ``KeepAlive`` equivalent (WA-4). Plain
    ``schtasks /create`` has no restart mechanism at all.

    It runs as the installing user via an S4U principal
    (``-LogonType S4U``) — never storing or seeing a password, and never as
    SYSTEM (ADR-0184). ``-RunLevel Limited`` (not Highest) keeps the runtime
    unelevated (WA-6): the task starts at boot but the CorvinOS process itself
    holds no admin token. Registration still requires an elevated session.
    """

    def _task_name(self, name: str) -> str:
        return f"CorvinOS-AlwaysOn-{name}"

    def install_service(
        self,
        name: str,
        command: str,
        description: str = "",
        env_vars: Optional[dict] = None,
        pre_exec: Optional[str] = None,  # noqa: ARG002 — see docstring
    ) -> None:
        """``pre_exec`` (WA-19 auto-update check) is accepted for interface
        parity but not yet wired here: a ScheduledTaskAction has exactly one
        -Execute/-Argument pair with no ExecStartPre/exec-chaining
        equivalent, so honoring it needs a generated wrapper script (the same
        shape as corvin-supervisor.ps1) rather than a one-line change — left
        as a follow-up. This is the opt-in "Stufe 2" path only; the default
        Stufe-1 Windows autostart (install.ps1 + corvin-supervisor.ps1)
        already runs its own one-shot `uv tool upgrade corvinos` per logon."""
        if not is_elevated():
            raise ElevationRequired(
                "Registering a boot-time Scheduled Task requires an elevated "
                "PowerShell. Re-run from an admin PowerShell: corvin-service install"
            )
        # Windows Scheduled Task actions have no native per-task environment
        # variable mechanism (unlike systemd Environment= / launchd
        # EnvironmentVariables) — env_vars is accepted for interface parity
        # with the other managers but not yet applied here (WA-9, deferred: a
        # future iteration could wrap `command` in a generated .cmd that sets
        # them before exec, mirroring corvin-supervisor.ps1's approach).
        user = current_user()
        task_name = self._task_name(name)

        # WA-5/M1: split the command into [program, *args] so a program path
        # containing spaces survives as ONE -Execute token, then re-quote each
        # piece as a PS single-quoted literal. posix=False (this path is
        # Windows-only) preserves backslashes in Windows paths regardless of the
        # host OS actually running this code. The command is built with path
        # components double-quoted so the split keeps them intact; posix=False
        # RETAINS those quotes, so _dequote strips them off the program token
        # before it's re-quoted for PowerShell. Argument tokens keep their own
        # double quotes so a spaced script/adapter path is still one arg to the
        # program's own command-line parser (inside the single-quoted -Argument).
        parts = shlex.split(command, posix=False) or [command]
        program = _dequote(parts[0])
        arguments = parts[1:]
        desc = description or f"CorvinOS {name} service (always-on)"

        action_arg = ""
        if arguments:
            arg_str = " ".join(arguments)
            action_arg = f" -Argument {_ps_single_quote(arg_str)}"

        # WA-4: -RestartCount 5 / -RestartInterval 1min = bounded restart-on-
        # failure (mirrors Linux Restart=on-failure + StartLimitBurst=5).
        ps = (
            f"$ErrorActionPreference='Stop';"
            f"$Action=New-ScheduledTaskAction -Execute {_ps_single_quote(program)}{action_arg};"
            f"$Trigger=New-ScheduledTaskTrigger -AtStartup;"
            f"$Settings=New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries "
            f"-DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) "
            f"-RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1) "
            f"-MultipleInstances IgnoreNew;"
            f"$Principal=New-ScheduledTaskPrincipal -UserId {_ps_single_quote(user)} "
            f"-LogonType S4U -RunLevel Limited;"
            f"Register-ScheduledTask -TaskName {_ps_single_quote(task_name)} "
            f"-Action $Action -Trigger $Trigger -Settings $Settings "
            f"-Principal $Principal -Description {_ps_single_quote(desc)} -Force"
        )
        cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"Register-ScheduledTask failed: {result.stderr or result.stdout}"
            )
        # Start it NOW, matching Linux (`enable --now`) and macOS (load +
        # RunAtLoad): -AtStartup alone means "works after the next reboot",
        # which on the headless box this feature targets reads as "install
        # did nothing" (review finding). Best-effort — the registration
        # above already succeeded.
        subprocess.run(
            ["schtasks", "/run", "/tn", self._task_name(name)],
            capture_output=True,
        )

    def uninstall_service(self, name: str) -> None:
        if not is_elevated():
            raise ElevationRequired(
                "Removing this Scheduled Task requires an elevated "
                "PowerShell. Re-run from an admin PowerShell: corvin-service uninstall"
            )
        # End the running instance first — /delete alone leaves the already-
        # started process holding port 8765 until reboot, blocking the
        # Stufe-1 fallback from binding (review finding).
        subprocess.run(
            ["schtasks", "/end", "/tn", self._task_name(name)],
            capture_output=True,
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
