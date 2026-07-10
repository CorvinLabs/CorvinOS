"""Unit tests for ServiceManager implementations."""

import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from corvinOS.installer.service_manager import (
    LinuxServiceManager,
    DarwinServiceManager,
    WindowsServiceManager,
    get_service_manager,
)


class TestLinuxServiceManager:
    """Tests for Linux systemd manager."""

    def test_init(self):
        """Test initialization creates systemd user dir."""
        mgr = LinuxServiceManager()
        assert mgr.systemd_user_dir.exists()

    def test_service_file_path(self):
        """Test service file path generation."""
        mgr = LinuxServiceManager()
        path = mgr._service_file("test")
        assert "corvin-test.service" in str(path)
        assert ".config/systemd/user" in str(path)

    def test_install_service_creates_unit(self):
        """Test that install_service creates a valid unit file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            mgr = LinuxServiceManager()
            mgr.systemd_user_dir = tmpdir_path

            mgr.install_service(
                name="test",
                command="/usr/bin/echo hello",
                description="Test service",
                auto_start=False,
            )

            unit_file = tmpdir_path / "corvin-test.service"
            assert unit_file.exists()

            content = unit_file.read_text()
            assert "[Unit]" in content
            assert "Description=Test service" in content
            assert "[Service]" in content
            assert "ExecStart=/usr/bin/echo hello" in content
            assert "[Install]" in content

    def test_install_service_permissions(self):
        """Test that unit file has correct permissions."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            mgr = LinuxServiceManager()
            mgr.systemd_user_dir = tmpdir_path

            mgr.install_service(
                name="test",
                command="/usr/bin/true",
                auto_start=False,
            )

            unit_file = tmpdir_path / "corvin-test.service"
            # File permissions should be 0o644
            mode = unit_file.stat().st_mode
            assert (mode & 0o644) == 0o644

    def test_install_service_has_bounded_restart(self):
        """ADR-0184 Stufe-1: a unit must not restart-loop forever."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = LinuxServiceManager()
            mgr.systemd_user_dir = Path(tmpdir)

            mgr.install_service(
                name="test", command="/usr/bin/true", auto_start=False,
            )

            content = (Path(tmpdir) / "corvin-test.service").read_text()
            assert "StartLimitIntervalSec=300" in content
            assert "StartLimitBurst=5" in content


class TestLinger:
    """ADR-0184 Stufe-1: systemd --user units must survive a headless
    reboot (no login ever), which requires `loginctl enable-linger`."""

    def test_enable_linger_calls_loginctl_when_not_already_enabled(self):
        mgr = LinuxServiceManager()
        with mock.patch.dict("os.environ", {"USER": "testuser"}), \
             mock.patch("subprocess.run") as run:
            run.side_effect = [
                mock.Mock(returncode=0, stdout="Linger=no\n"),  # show-user
                mock.Mock(returncode=0),  # enable-linger
            ]
            mgr._enable_linger()
            calls = [c.args[0] for c in run.call_args_list]
            assert ["loginctl", "show-user", "testuser"] in calls
            assert ["loginctl", "enable-linger", "testuser"] in calls

    def test_enable_linger_skips_when_already_enabled(self):
        mgr = LinuxServiceManager()
        with mock.patch.dict("os.environ", {"USER": "testuser"}), \
             mock.patch("subprocess.run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="Linger=yes\n")
            mgr._enable_linger()
            calls = [c.args[0] for c in run.call_args_list]
            assert ["loginctl", "enable-linger", "testuser"] not in calls

    def test_enable_linger_never_raises_when_loginctl_missing(self):
        """Best-effort: no systemd-logind (containers, minimal distros)
        must not break the install — a missing `loginctl` binary must not
        propagate and abort install_service() for every other caller."""
        mgr = LinuxServiceManager()
        with mock.patch.dict("os.environ", {"USER": "testuser"}), \
             mock.patch("subprocess.run", side_effect=FileNotFoundError()):
            mgr._enable_linger()  # must not raise

    def test_install_service_with_autostart_enables_linger(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = LinuxServiceManager()
            mgr.systemd_user_dir = Path(tmpdir)
            with mock.patch.object(mgr, "_enable_linger") as linger, \
                 mock.patch.object(mgr, "enable_autostart"), \
                 mock.patch.object(mgr, "_run_systemctl"):
                mgr.install_service(
                    name="test", command="/usr/bin/true", auto_start=True,
                )
                linger.assert_called_once()


class TestServiceNameContract:
    """Regression: the installer must register systemd units under the SAME
    names that bridge.sh and steps/validate.py control. service_manager prefixes
    `corvin-`, so the installer passes `voice-bridge-<channel>` (and
    `voice-bridge-adapter`) to land on `corvin-voice-bridge-<channel>.service`.
    A drift here silently orphans every service after install (the original
    `adapter` / `bridge-<x>` names produced `corvin-adapter.service` etc., which
    bridge.sh/validate.py never see)."""

    # Canonical units that bridge.sh manages and validate.py checks.
    CANONICAL = {
        "voice-bridge-adapter": "corvin-voice-bridge-adapter.service",
        "voice-bridge-whatsapp": "corvin-voice-bridge-whatsapp.service",
        "voice-bridge-telegram": "corvin-voice-bridge-telegram.service",
        "voice-bridge-discord": "corvin-voice-bridge-discord.service",
        "voice-bridge-slack": "corvin-voice-bridge-slack.service",
        "voice-bridge-email": "corvin-voice-bridge-email.service",
    }

    def test_service_file_matches_canonical_units(self):
        mgr = LinuxServiceManager()
        for name, unit in self.CANONICAL.items():
            assert mgr._service_file(name).name == unit

    def test_installer_core_uses_voice_bridge_names(self):
        """core.py must pass `voice-bridge-*` names (not the legacy
        `adapter` / `bridge-<x>` that orphaned the units)."""
        core_src = (
            Path(__file__).resolve().parent.parent
            / "corvinOS" / "installer" / "core.py"
        ).read_text()
        assert 'name="voice-bridge-adapter"' in core_src
        assert 'name=f"voice-bridge-{bridge}"' in core_src
        # The legacy names must be gone from service-name call sites.
        assert 'start_service("adapter")' not in core_src
        assert 'start_service(f"bridge-{bridge}")' not in core_src
        assert '["adapter"] + [f"bridge-{b}"' not in core_src

    def test_validate_expected_list_is_covered(self):
        """Every unit steps/validate.py checks must be derivable from a
        `voice-bridge-*` name the installer actually registers."""
        validate_src = (
            Path(__file__).resolve().parent.parent
            / "corvinOS" / "installer" / "steps" / "validate.py"
        ).read_text()
        for unit in self.CANONICAL.values():
            assert unit in validate_src


class TestLinuxServiceManagerPreExec:
    """WA-19: the systemd manager must run the auto-update check before the
    real command on every (re)start, without ever blocking the service."""

    def test_pre_exec_adds_execstartpre_with_failure_tolerant_prefix(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = LinuxServiceManager()
            mgr.systemd_user_dir = Path(tmpdir)
            mgr.install_service(
                name="test",
                command="/usr/bin/echo hello",
                auto_start=False,
                pre_exec="/usr/bin/python3 /opt/corvin/autoupdate.py",
            )
            content = (Path(tmpdir) / "corvin-test.service").read_text()
            # "-" prefix: a failed/offline check must never block ExecStart.
            assert "ExecStartPre=-/usr/bin/python3 /opt/corvin/autoupdate.py" in content
            assert "ExecStart=/usr/bin/echo hello" in content

    def test_no_pre_exec_means_no_execstartpre_line(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = LinuxServiceManager()
            mgr.systemd_user_dir = Path(tmpdir)
            mgr.install_service(name="test", command="/usr/bin/echo hello", auto_start=False)
            content = (Path(tmpdir) / "corvin-test.service").read_text()
            assert "ExecStartPre" not in content


class TestDarwinServiceManager:
    """Tests for macOS launchd manager."""

    def test_init(self):
        """Test initialization."""
        mgr = DarwinServiceManager()
        # Don't assert directory exists (may not exist on Linux)
        assert mgr.launchagents_dir == Path.home() / "Library" / "LaunchAgents"

    def test_plist_path(self):
        """Test plist path generation."""
        mgr = DarwinServiceManager()
        path = mgr._plist_path("test")
        assert "com.corvin.test.plist" in str(path)

    def test_generate_plist(self):
        """Test plist generation."""
        mgr = DarwinServiceManager()
        plist = mgr._generate_plist("test", "/usr/bin/echo hello", "Test service")

        assert '<?xml version="1.0"' in plist
        assert "<plist version=" in plist
        assert "com.corvin.test" in plist
        assert "<key>Program</key>" in plist
        assert "<string>/usr/bin/echo</string>" in plist
        assert "<key>RunAtLoad</key>" in plist
        assert "<true/>" in plist

    def test_generate_plist_with_args(self):
        """Test plist generation with multiple arguments."""
        mgr = DarwinServiceManager()
        plist = mgr._generate_plist(
            "test", "/usr/bin/python -m mymodule --flag value", "Test"
        )

        assert "<key>ProgramArguments</key>" in plist
        assert "<array>" in plist
        assert "<string>-m</string>" in plist
        assert "<string>mymodule</string>" in plist
        assert "<string>--flag</string>" in plist
        assert "<string>value</string>" in plist

    def test_generate_plist_with_pre_exec_wraps_in_shell(self):
        """WA-19: launchd has no ExecStartPre equivalent for user agents —
        the real command must be wrapped in a bash -c script that runs the
        update check then `exec`s the original program (KeepAlive still
        tracks exactly one long-running process)."""
        mgr = DarwinServiceManager()
        plist = mgr._generate_plist(
            "test", "/usr/bin/python3 -m mymodule", "Test",
            pre_exec="/usr/bin/python3 /opt/corvin/autoupdate.py",
        )
        assert "<string>/bin/bash</string>" in plist
        assert "<string>-c</string>" in plist
        assert "/opt/corvin/autoupdate.py; exec /usr/bin/python3 -m mymodule" in plist

    def test_generate_plist_without_pre_exec_runs_program_directly(self):
        mgr = DarwinServiceManager()
        plist = mgr._generate_plist("test", "/usr/bin/echo hello", "Test")
        assert "<string>/bin/bash</string>" not in plist
        assert "<string>/usr/bin/echo</string>" in plist


class TestWindowsServiceManager:
    """Tests for Windows Task Scheduler manager."""

    def test_task_name(self):
        """Test task name generation."""
        mgr = WindowsServiceManager()
        name = mgr._task_name("test")
        assert name == "CorvinOS\\test"

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only test")
    def test_install_service_windows(self):
        """Test Task Scheduler service creation on Windows."""
        mgr = WindowsServiceManager()
        # Don't actually install, just verify the method exists
        assert hasattr(mgr, "install_service")


class TestGetServiceManager:
    """Tests for platform detection."""

    def test_get_service_manager_returns_correct_type(self):
        """Test that get_service_manager returns correct type for platform."""
        mgr = get_service_manager()

        if sys.platform == "darwin":
            assert isinstance(mgr, DarwinServiceManager)
        elif sys.platform == "win32":
            assert isinstance(mgr, WindowsServiceManager)
        else:
            assert isinstance(mgr, LinuxServiceManager)

    def test_all_managers_implement_interface(self):
        """Test that all managers implement the required methods."""
        managers = [
            LinuxServiceManager(),
            DarwinServiceManager(),
            WindowsServiceManager(),
        ]

        required_methods = [
            "install_service",
            "start_service",
            "stop_service",
            "enable_autostart",
            "disable_autostart",
            "uninstall_service",
            "status",
            "is_active",
        ]

        for mgr in managers:
            for method in required_methods:
                assert hasattr(mgr, method), f"{mgr.__class__.__name__} missing {method}"
                assert callable(getattr(mgr, method))


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
