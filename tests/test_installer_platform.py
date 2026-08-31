"""Tests for the OS / package-manager / WSL / systemd detection matrix.

``corvinOS/installer/steps/platform.py::detect()`` is the single branch point
every later installer step relies on: it decides which package manager
``step_3_system_dependencies`` shells out to (apt vs dnf vs pacman, in that
priority order), whether the box is classified as WSL vs plain Linux (which
changes the warnings shown and downstream audio/TTS behavior), and whether
``has_systemd`` gates systemd-unit registration in later steps.

Before this file, nothing drove ``detect()``/``pkg_install()`` under any
OS/pkg-mgr/WSL/systemd combination — every assertion here exercises real
branch logic via mocked ``subprocess.run`` / file reads, not tautologies.
"""
from __future__ import annotations

import subprocess
from unittest import mock

import pytest

from corvinOS.installer.steps import platform as _platform
from corvinOS.installer.steps.platform import OS, PkgMgr, PlatformInfo


def _run_result(returncode: int, stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


def _cmd_exists_side_effect(available: set[str]):
    """Build a fake ``subprocess.run`` that answers ``which <name>``/``where <name>``."""

    def _fake_run(cmd, **kwargs):
        name = cmd[-1]
        if cmd[0] in ("which", "where"):
            return _run_result(0 if name in available else 1)
        # uname -m / systemctl / anything else defaults to "succeeds silently"
        return _run_result(0, stdout="x86_64")

    return _fake_run


class TestDetectLinuxPkgMgrPriority:
    """apt > dnf > pacman priority, and the "none found" fallback warning."""

    def test_prefers_apt_when_apt_and_dnf_both_present(self):
        with mock.patch.object(_platform.sys, "platform", "linux"), \
             mock.patch.object(_platform, "_is_wsl", return_value=False), \
             mock.patch.object(_platform, "_has_systemd", return_value=True), \
             mock.patch("subprocess.run", side_effect=_cmd_exists_side_effect({"apt", "dnf"})):
            info = _platform.detect()

        assert info.os_kind == OS.LINUX
        assert info.pkg_mgr == PkgMgr.APT
        assert info.has_systemd is True
        assert info.warnings == []

    def test_falls_back_to_dnf_when_apt_absent(self):
        with mock.patch.object(_platform.sys, "platform", "linux"), \
             mock.patch.object(_platform, "_is_wsl", return_value=False), \
             mock.patch.object(_platform, "_has_systemd", return_value=True), \
             mock.patch("subprocess.run", side_effect=_cmd_exists_side_effect({"dnf", "pacman"})):
            info = _platform.detect()

        assert info.pkg_mgr == PkgMgr.DNF

    def test_falls_back_to_pacman_when_apt_and_dnf_absent(self):
        with mock.patch.object(_platform.sys, "platform", "linux"), \
             mock.patch.object(_platform, "_is_wsl", return_value=False), \
             mock.patch.object(_platform, "_has_systemd", return_value=True), \
             mock.patch("subprocess.run", side_effect=_cmd_exists_side_effect({"pacman"})):
            info = _platform.detect()

        assert info.pkg_mgr == PkgMgr.PACMAN

    def test_no_known_pkg_mgr_warns_and_leaves_none(self):
        with mock.patch.object(_platform.sys, "platform", "linux"), \
             mock.patch.object(_platform, "_is_wsl", return_value=False), \
             mock.patch.object(_platform, "_has_systemd", return_value=True), \
             mock.patch("subprocess.run", side_effect=_cmd_exists_side_effect(set())):
            info = _platform.detect()

        assert info.pkg_mgr == PkgMgr.NONE
        assert any("package manager" in w for w in info.warnings)


class TestDetectWSL:
    """WSL classification changes os_kind AND adds the audio warning."""

    def test_wsl_positive_sets_os_kind_wsl_and_warns(self):
        with mock.patch.object(_platform.sys, "platform", "linux"), \
             mock.patch.object(_platform, "_is_wsl", return_value=True), \
             mock.patch.object(_platform, "_has_systemd", return_value=False), \
             mock.patch("subprocess.run", side_effect=_cmd_exists_side_effect({"apt"})):
            info = _platform.detect()

        assert info.os_kind == OS.WSL
        assert any("TTS read-aloud will be silent" in w for w in info.warnings)

    def test_wsl_negative_sets_plain_linux_without_audio_warning(self):
        with mock.patch.object(_platform.sys, "platform", "linux"), \
             mock.patch.object(_platform, "_is_wsl", return_value=False), \
             mock.patch.object(_platform, "_has_systemd", return_value=False), \
             mock.patch("subprocess.run", side_effect=_cmd_exists_side_effect({"apt"})):
            info = _platform.detect()

        assert info.os_kind == OS.LINUX
        assert not any("TTS read-aloud" in w for w in info.warnings)

    @pytest.mark.parametrize(
        "proc_version_content,expected",
        [
            ("Linux version 5.15.0 (Microsoft@Microsoft.com)", True),
            ("Linux version 5.15.90.1-microsoft-standard-WSL2", True),
            ("Linux version 6.2.0-generic (buildd@lcy02-amd64)", False),
        ],
    )
    def test_is_wsl_reads_proc_version_content(self, proc_version_content, expected):
        m = mock.mock_open(read_data=proc_version_content)
        with mock.patch("builtins.open", m):
            assert _platform._is_wsl() is expected

    def test_is_wsl_returns_false_when_proc_version_missing(self):
        with mock.patch("builtins.open", side_effect=OSError("no such file")):
            assert _platform._is_wsl() is False


class TestDetectWindowsAndMacOS:
    def test_windows_detects_winget(self):
        with mock.patch.object(_platform.sys, "platform", "win32"), \
             mock.patch("subprocess.run", side_effect=_cmd_exists_side_effect({"winget"})):
            info = _platform.detect()

        assert info.os_kind == OS.WINDOWS
        assert info.pkg_mgr == PkgMgr.WINGET

    def test_windows_without_winget_leaves_pkg_mgr_none(self):
        with mock.patch.object(_platform.sys, "platform", "win32"), \
             mock.patch("subprocess.run", side_effect=_cmd_exists_side_effect(set())):
            info = _platform.detect()

        assert info.os_kind == OS.WINDOWS
        assert info.pkg_mgr == PkgMgr.NONE

    def test_macos_detects_brew_and_sets_pkg_mgr(self):
        with mock.patch.object(_platform.sys, "platform", "darwin"), \
             mock.patch.object(_platform, "_detect_brew", return_value=PkgMgr.BREW) as m_brew:
            info = _platform.detect()

        assert info.os_kind == OS.MACOS
        assert info.pkg_mgr == PkgMgr.BREW
        m_brew.assert_called_once()

    def test_macos_without_brew_leaves_pkg_mgr_none(self):
        with mock.patch.object(_platform.sys, "platform", "darwin"), \
             mock.patch.object(_platform, "_detect_brew", return_value=PkgMgr.NONE):
            info = _platform.detect()

        assert info.pkg_mgr == PkgMgr.NONE


class TestHasSystemd:
    def test_returncode_zero_means_systemd_present(self):
        with mock.patch("subprocess.run", return_value=_run_result(0)):
            assert _platform._has_systemd() is True

    def test_nonzero_returncode_means_no_systemd(self):
        with mock.patch("subprocess.run", return_value=_run_result(1)):
            assert _platform._has_systemd() is False

    def test_exception_from_subprocess_is_swallowed_to_false(self):
        with mock.patch("subprocess.run", side_effect=FileNotFoundError("no systemctl")):
            assert _platform._has_systemd() is False


class TestDetectBrewPrefix:
    def test_no_brew_binary_anywhere_returns_none(self):
        info = PlatformInfo()
        with mock.patch("os.path.isfile", return_value=False):
            assert _platform._detect_brew(info) == PkgMgr.NONE
        assert info.brew_prefix == ""

    def test_apple_silicon_prefix_found_sources_path(self):
        info = PlatformInfo()

        def _isfile(path):
            return path == "/opt/homebrew/bin/brew"

        with mock.patch("os.path.isfile", side_effect=_isfile), \
             mock.patch(
                 "subprocess.run",
                 return_value=_run_result(0, stdout="/opt/homebrew\n"),
             ), \
             mock.patch.dict("os.environ", {"PATH": "/usr/bin"}, clear=False):
            result = _platform._detect_brew(info)
            path_after = _platform.os.environ["PATH"]

        assert result == PkgMgr.BREW
        assert info.brew_prefix == "/opt/homebrew"
        assert "/opt/homebrew/bin" in path_after

    def test_intel_mac_prefix_found_when_apple_silicon_path_absent(self):
        info = PlatformInfo()

        def _isfile(path):
            return path == "/usr/local/bin/brew"

        with mock.patch("os.path.isfile", side_effect=_isfile), \
             mock.patch(
                 "subprocess.run",
                 return_value=_run_result(0, stdout="/usr/local\n"),
             ):
            result = _platform._detect_brew(info)

        assert result == PkgMgr.BREW
        assert info.brew_prefix == "/usr/local"

    def test_brew_prefix_subprocess_failure_still_returns_brew(self):
        """If `brew --prefix` itself fails, we still report BREW (binary exists)."""
        info = PlatformInfo()

        def _isfile(path):
            return path == "/opt/homebrew/bin/brew"

        with mock.patch("os.path.isfile", side_effect=_isfile), \
             mock.patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "brew")):
            result = _platform._detect_brew(info)

        assert result == PkgMgr.BREW
        assert info.brew_prefix == ""


class TestCmdExists:
    def test_uses_which_on_posix(self):
        captured = {}

        def _fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return _run_result(0)

        with mock.patch.object(_platform.sys, "platform", "linux"), \
             mock.patch("subprocess.run", side_effect=_fake_run):
            assert _platform._cmd_exists("apt") is True
        assert captured["cmd"] == ["which", "apt"]

    def test_uses_where_on_windows(self):
        captured = {}

        def _fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return _run_result(0)

        with mock.patch.object(_platform.sys, "platform", "win32"), \
             mock.patch("subprocess.run", side_effect=_fake_run):
            assert _platform._cmd_exists("winget") is True
        assert captured["cmd"] == ["where", "winget"]

    def test_exception_from_subprocess_is_swallowed_to_false(self):
        with mock.patch("subprocess.run", side_effect=OSError("boom")):
            assert _platform._cmd_exists("apt") is False


class TestPkgInstall:
    def test_no_packages_is_a_noop_success(self):
        info = PlatformInfo(pkg_mgr=PkgMgr.APT)
        with mock.patch("subprocess.run") as m_run:
            assert _platform.pkg_install(info) is True
        m_run.assert_not_called()

    def test_none_pkg_mgr_prints_warning_and_fails(self, capsys):
        info = PlatformInfo(pkg_mgr=PkgMgr.NONE)
        with mock.patch("subprocess.run") as m_run:
            result = _platform.pkg_install(info, "foo")
        assert result is False
        m_run.assert_not_called()
        assert "foo" in capsys.readouterr().out

    def test_apt_runs_update_before_install(self):
        info = PlatformInfo(pkg_mgr=PkgMgr.APT)
        calls = []

        def _fake_run(cmd, **kwargs):
            calls.append(cmd)
            return _run_result(0)

        with mock.patch("subprocess.run", side_effect=_fake_run):
            result = _platform.pkg_install(info, "curl", "git")

        assert result is True
        assert calls[0] == ["sudo", "apt", "update", "-qq"]
        assert calls[1] == ["sudo", "apt", "install", "-y", "curl", "git"]

    def test_dnf_does_not_run_update_first(self):
        info = PlatformInfo(pkg_mgr=PkgMgr.DNF)
        calls = []

        def _fake_run(cmd, **kwargs):
            calls.append(cmd)
            return _run_result(0)

        with mock.patch("subprocess.run", side_effect=_fake_run):
            _platform.pkg_install(info, "curl")

        assert calls == [["sudo", "dnf", "install", "-y", "curl"]]

    def test_pacman_uses_noconfirm_flag(self):
        info = PlatformInfo(pkg_mgr=PkgMgr.PACMAN)
        calls = []

        def _fake_run(cmd, **kwargs):
            calls.append(cmd)
            return _run_result(0)

        with mock.patch("subprocess.run", side_effect=_fake_run):
            _platform.pkg_install(info, "curl")

        assert calls == [["sudo", "pacman", "-S", "--noconfirm", "curl"]]

    def test_nonzero_returncode_propagates_as_failure(self):
        info = PlatformInfo(pkg_mgr=PkgMgr.APT)
        with mock.patch("subprocess.run", return_value=_run_result(1)):
            assert _platform.pkg_install(info, "curl") is False
