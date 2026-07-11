"""Unit tests for ADR-0184 Stufe-2 (opt-in always-on system service).

Covers the invariants the ADR's "Must NOT do" section pins down:
- Never registers anything without elevation (raises ElevationRequired).
- Every platform's generated unit/plist/task runs the process as the
  INSTALLING user, never root/SYSTEM.
- Windows never stores or transmits a password (uses `/np` S4U logon).
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest import mock

import pytest

from corvinOS.installer.system_service_manager import (
    DarwinSystemServiceManager,
    ElevationRequired,
    LinuxSystemServiceManager,
    WindowsSystemServiceManager,
    current_user,
    get_system_service_manager,
    is_elevated,
)


class TestStage2AutoUpdateWiring:
    def test_autoupdate_pre_exec_resolves_the_real_entrypoint(self):
        # H1: the always-on (Stufe-2) callers must be able to build a non-None
        # pre_exec so the WA-19 auto-update actually runs. The helper resolves
        # the shipped ops/launcher/corvin/_autoupdate_entrypoint.py.
        from ops.launcher.service_entry import _autoupdate_pre_exec
        pre = _autoupdate_pre_exec()
        assert pre is not None
        assert "_autoupdate_entrypoint.py" in pre
        # Interpreter + script are both quoted (space-safe re-tokenization).
        assert pre.count('"') >= 4


class TestIsElevated:
    def test_posix_root_is_elevated(self):
        with mock.patch("os.geteuid", return_value=0, create=True):
            assert is_elevated() is True

    def test_posix_non_root_is_not_elevated(self):
        with mock.patch("os.geteuid", return_value=1000, create=True):
            assert is_elevated() is False


class TestLinuxSystemServiceManager:
    def test_install_without_elevation_raises_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = LinuxSystemServiceManager()
            mgr.UNIT_DIR = Path(tmp)
            with mock.patch(
                "corvinOS.installer.system_service_manager.is_elevated",
                return_value=False,
            ):
                with pytest.raises(ElevationRequired):
                    mgr.install_service(name="webui", command="/usr/bin/true")
            assert list(Path(tmp).iterdir()) == []

    def test_install_with_elevation_writes_unit_running_as_installing_user(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = LinuxSystemServiceManager()
            mgr.UNIT_DIR = Path(tmp)
            with mock.patch(
                "corvinOS.installer.system_service_manager.is_elevated",
                return_value=True,
            ), mock.patch(
                "corvinOS.installer.system_service_manager.current_user",
                return_value="silvio",
            ), mock.patch("subprocess.run") as run:
                run.return_value = mock.Mock(returncode=0)
                mgr.install_service(name="webui", command="/usr/bin/true")

            content = (Path(tmp) / "corvin-webui.service").read_text()
            assert "User=silvio" in content
            assert "root" not in content.lower()
            assert "StartLimitIntervalSec=300" in content
            assert "StartLimitBurst=5" in content
            assert "WantedBy=multi-user.target" in content

    def test_pre_exec_adds_execstartpre_and_widened_start_timeout(self):
        # H1/H2: the Stufe-2 always-on unit must run the WA-19 auto-update
        # ExecStartPre AND widen the start timeout past systemd's 90s default,
        # else a slow boot-time upgrade aborts the start job and the unit
        # crash-loops into the StartLimitBurst lockout.
        with tempfile.TemporaryDirectory() as tmp:
            mgr = LinuxSystemServiceManager()
            mgr.UNIT_DIR = Path(tmp)
            with mock.patch(
                "corvinOS.installer.system_service_manager.is_elevated",
                return_value=True,
            ), mock.patch(
                "corvinOS.installer.system_service_manager.current_user",
                return_value="silvio",
            ), mock.patch("subprocess.run") as run:
                run.return_value = mock.Mock(returncode=0)
                mgr.install_service(
                    name="webui", command="/usr/bin/true",
                    pre_exec='"/py" "/auto.py"',
                )
            content = (Path(tmp) / "corvin-webui.service").read_text()
            assert 'ExecStartPre=-"/py" "/auto.py"' in content
            assert "TimeoutStartSec=300" in content

    def test_no_pre_exec_omits_execstartpre(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = LinuxSystemServiceManager()
            mgr.UNIT_DIR = Path(tmp)
            with mock.patch(
                "corvinOS.installer.system_service_manager.is_elevated",
                return_value=True,
            ), mock.patch(
                "corvinOS.installer.system_service_manager.current_user",
                return_value="silvio",
            ), mock.patch("subprocess.run") as run:
                run.return_value = mock.Mock(returncode=0)
                mgr.install_service(name="webui", command="/usr/bin/true")
            content = (Path(tmp) / "corvin-webui.service").read_text()
            assert "ExecStartPre" not in content
            assert "TimeoutStartSec" not in content

    def test_uninstall_without_elevation_raises(self):
        mgr = LinuxSystemServiceManager()
        with mock.patch(
            "corvinOS.installer.system_service_manager.is_elevated",
            return_value=False,
        ):
            with pytest.raises(ElevationRequired):
                mgr.uninstall_service("webui")


class TestDarwinSystemServiceManager:
    def test_install_without_elevation_raises(self):
        mgr = DarwinSystemServiceManager()
        with mock.patch(
            "corvinOS.installer.system_service_manager.is_elevated",
            return_value=False,
        ):
            with pytest.raises(ElevationRequired):
                mgr.install_service(name="webui", command="/usr/bin/true")

    def test_install_with_elevation_writes_plist_with_username_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = DarwinSystemServiceManager()
            mgr.DAEMON_DIR = Path(tmp)
            with mock.patch(
                "corvinOS.installer.system_service_manager.is_elevated",
                return_value=True,
            ), mock.patch(
                "corvinOS.installer.system_service_manager.current_user",
                return_value="silvio",
            ), mock.patch("subprocess.run") as run:
                run.return_value = mock.Mock(returncode=0)
                mgr.install_service(name="webui", command="/usr/bin/true")

            plist_files = list(Path(tmp).glob("*.plist"))
            assert len(plist_files) == 1
            content = plist_files[0].read_text()
            assert "<key>UserName</key>" in content
            assert "<string>silvio</string>" in content
            assert "ThrottleInterval" in content


class TestWindowsSystemServiceManager:
    def test_install_without_elevation_raises(self):
        mgr = WindowsSystemServiceManager()
        with mock.patch(
            "corvinOS.installer.system_service_manager.is_elevated",
            return_value=False,
        ):
            with pytest.raises(ElevationRequired):
                mgr.install_service(name="webui", command="C:\\corvin.exe")

    def test_install_uses_s4u_logon_never_a_password_and_bounded_restart(self):
        """WA-4/WA-5/WA-6 + ADR-0184: registered via PowerShell
        Register-ScheduledTask with an S4U principal (no password, never
        SYSTEM), RunLevel Limited (unelevated runtime), and a BOUNDED
        restart-on-failure policy (the systemd Restart=on-failure equivalent
        the old bare `schtasks /sc onstart` lacked)."""
        mgr = WindowsSystemServiceManager()
        with mock.patch(
            "corvinOS.installer.system_service_manager.is_elevated",
            return_value=True,
        ), mock.patch(
            "corvinOS.installer.system_service_manager.current_user",
            return_value="silvio",
        ), mock.patch("subprocess.run") as run:
            run.return_value = mock.Mock(returncode=0, stderr="", stdout="")
            mgr.install_service(name="webui", command="C:\\corvin.exe -m uvicorn app")

        # Call 1 registers the task; call 2 starts it immediately (parity
        # with Linux `enable --now` / macOS RunAtLoad -- review finding: a
        # registered-but-not-started always-on service reads as a no-op
        # until the next reboot).
        argv = run.call_args_list[0].args[0]
        start_argv = run.call_args_list[1].args[0]
        assert start_argv[:2] == ["schtasks", "/run"]
        assert argv[0] == "powershell"
        ps = argv[-1]
        # S4U logon — no password ever, and the invoking user (not SYSTEM).
        assert "-LogonType S4U" in ps
        assert "-UserId 'silvio'" in ps
        assert "-Password" not in ps
        assert "SYSTEM" not in ps
        # WA-6: unelevated runtime.
        assert "-RunLevel Limited" in ps
        assert "-RunLevel Highest" not in ps
        # WA-4: bounded restart-on-failure.
        assert "-RestartCount 5" in ps
        assert "-RestartInterval (New-TimeSpan -Minutes 1)" in ps
        assert "-AtStartup" in ps
        # WA-5: the executable is its own quoted -Execute token (not torn apart
        # from its args), and args are carried separately.
        assert "-Execute 'C:\\corvin.exe'" in ps
        assert "-Argument '-m uvicorn app'" in ps

    def test_install_refuses_to_register_as_root_via_current_user(self):
        """WA-1: current_user() refuses root, so an install run directly as
        root (no SUDO_USER) never writes a root-owned always-on task."""
        import os as _os
        mgr = WindowsSystemServiceManager()
        # Simulate POSIX-root with no sudo context reaching current_user().
        with mock.patch(
            "corvinOS.installer.system_service_manager.is_elevated",
            return_value=True,
        ), mock.patch("os.geteuid", return_value=0, create=True), \
             mock.patch.dict(_os.environ, {"SUDO_USER": "", "PKEXEC_USER": ""}, clear=False), \
             mock.patch("subprocess.run") as run:
            run.return_value = mock.Mock(returncode=0, stderr="", stdout="")
            with pytest.raises(RuntimeError):
                mgr.install_service(name="webui", command="C:\\corvin.exe")

    def test_install_raises_runtime_error_on_schtasks_failure(self):
        mgr = WindowsSystemServiceManager()
        with mock.patch(
            "corvinOS.installer.system_service_manager.is_elevated",
            return_value=True,
        ), mock.patch("subprocess.run") as run:
            run.return_value = mock.Mock(returncode=1, stderr="access denied")
            with pytest.raises(RuntimeError):
                mgr.install_service(name="webui", command="C:\\corvin.exe")


class TestFactory:
    def test_returns_a_system_service_manager_for_this_platform(self):
        mgr = get_system_service_manager()
        assert hasattr(mgr, "install_service")
        assert hasattr(mgr, "uninstall_service")
        assert hasattr(mgr, "status")

    def test_current_user_is_non_empty(self):
        assert current_user()


class TestCurrentUserRootHandling:
    """WA-1: never resolve the service account to root under sudo."""

    def test_recovers_invoking_user_from_sudo_user_when_root(self):
        with mock.patch("os.geteuid", return_value=0, create=True), \
             mock.patch.dict("os.environ", {"SUDO_USER": "silvio"}, clear=False):
            assert current_user() == "silvio"

    def test_recovers_from_pkexec_user_when_root(self):
        with mock.patch("os.geteuid", return_value=0, create=True), \
             mock.patch.dict(
                 "os.environ", {"SUDO_USER": "", "PKEXEC_USER": "silvio"}, clear=False
             ):
            assert current_user() == "silvio"

    def test_refuses_when_root_with_no_invoking_user(self):
        with mock.patch("os.geteuid", return_value=0, create=True), \
             mock.patch.dict(
                 "os.environ", {"SUDO_USER": "", "PKEXEC_USER": ""}, clear=False
             ):
            with pytest.raises(RuntimeError):
                current_user()

    def test_refuses_when_sudo_user_itself_is_root(self):
        with mock.patch("os.geteuid", return_value=0, create=True), \
             mock.patch.dict(
                 "os.environ", {"SUDO_USER": "root", "PKEXEC_USER": ""}, clear=False
             ):
            with pytest.raises(RuntimeError):
                current_user()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
