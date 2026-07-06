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

    def test_install_uses_np_s4u_logon_never_a_password_flag(self):
        """Regression: must never pass /rp (a password flag) — /np (S4U, no
        password) is the whole point of this design (ADR-0184)."""
        mgr = WindowsSystemServiceManager()
        with mock.patch(
            "corvinOS.installer.system_service_manager.is_elevated",
            return_value=True,
        ), mock.patch(
            "corvinOS.installer.system_service_manager.current_user",
            return_value="silvio",
        ), mock.patch("subprocess.run") as run:
            run.return_value = mock.Mock(returncode=0, stderr="")
            mgr.install_service(name="webui", command="C:\\corvin.exe")

        cmd = run.call_args.args[0]
        assert "/np" in cmd
        assert "/rp" not in cmd
        assert "/ru" in cmd and "silvio" in cmd
        assert "/sc" in cmd and "onstart" in cmd
        # Never runs as SYSTEM.
        assert "SYSTEM" not in cmd

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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
