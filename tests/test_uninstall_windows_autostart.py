"""Regression: `corvin-uninstall` must remove the Windows autostart Scheduled
Task registered by install.ps1 (adversarial review finding).

Before this fix, `CorvinInstaller.uninstall()` had no code path touching
Windows Scheduled Tasks at all — the only thing that knew how to remove the
"CorvinOS-Console" task was a dev-checkout-only script (`bridge.ps1
uninstall-autostart`), never shipped to anyone who installed via the
`install.ps1` one-liner. A user running the documented `corvin-uninstall`
command was left with a Scheduled Task that kept relaunching (and
self-upgrading) CorvinOS at every login, indefinitely.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest import mock

from corvinOS.installer.core import CorvinInstaller


def _make_installer(tmpdir: Path) -> CorvinInstaller:
    # Isolate EVERY root uninstall() deletes from. Regression: an earlier
    # version of this helper isolated only corvin_home/voice_config, so
    # uninstall(purge=True) deleted the LIVE dev checkout's in-repo .corvin
    # (running-bridge session state, audit chain), the user's systemd
    # corvin-*.service units, and the Claude Code plugin cache/marketplace
    # on every test run.
    installer = CorvinInstaller(interactive=False, repo_root=tmpdir / "repo")
    installer.corvin_home = tmpdir / "corvin_home"
    installer.voice_config = tmpdir / "voice_config"
    installer.systemd_user_dir = tmpdir / "systemd_user"
    installer.claude_plugins_dir = tmpdir / "claude_plugins"
    # Windows autostart shortcut roots — MUST be isolated too, else a
    # win32-mocked uninstall test on a real Windows dev box unlinks the
    # developer's actual Startup/Desktop CorvinOS.lnk (live-state-wipe class).
    installer.win_startup_dir = tmpdir / "win_startup"
    installer.win_desktop_dir = tmpdir / "win_desktop"
    installer.corvin_home.mkdir(parents=True, exist_ok=True)
    installer.voice_config.mkdir(parents=True, exist_ok=True)
    # Neutralise the real service manager so uninstall() doesn't touch any
    # actual OS service state on the machine running this test.
    installer.service_manager = mock.MagicMock()
    installer.bridge_manager = mock.MagicMock()
    return installer


class TestWindowsAutostartUninstall:
    def test_uninstall_removes_corvinos_console_scheduled_task_on_windows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            installer = _make_installer(Path(tmp))

            def fake_run(cmd, **kwargs):  # noqa: ANN001
                result = mock.MagicMock()
                if cmd[:3] == ["schtasks", "/query", "/tn"]:
                    result.returncode = 0  # task exists
                    result.stdout = "CorvinOS-Console"
                else:
                    result.returncode = 0
                    result.stderr = ""
                return result

            calls: list[list[str]] = []

            def recording_run(cmd, **kwargs):  # noqa: ANN001
                calls.append(list(cmd))
                return fake_run(cmd, **kwargs)

            with mock.patch("corvinOS.installer.core.sys.platform", "win32"), \
                 mock.patch("corvinOS.installer.core.shutil.which", return_value=None), \
                 mock.patch("corvinOS.installer.core.subprocess.run", side_effect=recording_run), \
                 mock.patch("builtins.input", return_value="n"):
                installer.uninstall(purge=True)

            delete_calls = [c for c in calls if c[:2] == ["schtasks", "/delete"]]
            assert delete_calls, f"no schtasks /delete call was made; calls={calls}"
            assert any("CorvinOS-Console" in c for c in delete_calls), delete_calls

    def test_uninstall_reports_no_task_found_without_erroring(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            installer = _make_installer(Path(tmp))

            def fake_run(cmd, **kwargs):  # noqa: ANN001
                result = mock.MagicMock()
                result.returncode = 1  # "query" says the task doesn't exist
                result.stdout = ""
                result.stderr = "ERROR: not found"
                return result

            with mock.patch("corvinOS.installer.core.sys.platform", "win32"), \
                 mock.patch("corvinOS.installer.core.shutil.which", return_value=None), \
                 mock.patch("corvinOS.installer.core.subprocess.run", side_effect=fake_run), \
                 mock.patch("builtins.input", return_value="n"):
                # Must not raise even when the task is already gone.
                installer.uninstall(purge=True)

    def test_uninstall_removes_startup_and_desktop_shortcuts_on_windows(self) -> None:
        """WA-9: install.ps1 falls back to a Startup-folder shortcut (non-admin
        accounts) and always creates a Desktop shortcut. uninstall() must remove
        both, or a "removed" CorvinOS keeps auto-starting via the shortcut."""
        with tempfile.TemporaryDirectory() as tmp:
            installer = _make_installer(Path(tmp))

            # Drive the injected shortcut roots (not os.environ) — this is the
            # exact contract that keeps uninstall away from real user paths.
            startup_dir = installer.win_startup_dir
            desktop_dir = installer.win_desktop_dir
            startup_dir.mkdir(parents=True)
            desktop_dir.mkdir(parents=True)
            startup_lnk = startup_dir / "CorvinOS.lnk"
            desktop_lnk = desktop_dir / "CorvinOS.lnk"
            startup_lnk.write_text("fake shortcut")
            desktop_lnk.write_text("fake shortcut")

            def fake_run(cmd, **kwargs):  # noqa: ANN001
                result = mock.MagicMock()
                result.returncode = 1  # no Scheduled Task registered (Startup-folder path)
                result.stdout = ""
                result.stderr = "ERROR: not found"
                return result

            with mock.patch("corvinOS.installer.core.sys.platform", "win32"), \
                 mock.patch("corvinOS.installer.core.shutil.which", return_value=None), \
                 mock.patch("corvinOS.installer.core.subprocess.run", side_effect=fake_run), \
                 mock.patch("builtins.input", return_value="n"):
                installer.uninstall(purge=True)

            assert not startup_lnk.exists(), "Startup-folder CorvinOS.lnk must be removed"
            assert not desktop_lnk.exists(), "Desktop CorvinOS.lnk must be removed"

    def test_uninstall_on_linux_does_not_attempt_schtasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            installer = _make_installer(Path(tmp))
            calls: list[list[str]] = []

            def recording_run(cmd, **kwargs):  # noqa: ANN001
                calls.append(list(cmd))
                result = mock.MagicMock()
                result.returncode = 0
                result.stdout = ""
                result.stderr = ""
                return result

            with mock.patch("corvinOS.installer.core.sys.platform", "linux"), \
                 mock.patch("corvinOS.installer.core.shutil.which", return_value=None), \
                 mock.patch("corvinOS.installer.core.subprocess.run", side_effect=recording_run), \
                 mock.patch("builtins.input", return_value="n"):
                installer.uninstall(purge=True)

            assert not any(c and c[0] == "schtasks" for c in calls)


class TestUninstallTouchesOnlyInjectedRoots:
    """uninstall(purge=True) must operate exclusively on the installer's
    injected roots — never on the module-global _REPO_ROOT or Path.home().

    Regression for the live-state wipe: uninstall's Step 10 deleted
    ``_REPO_ROOT / ".corvin"`` regardless of the corvin_home isolation the
    tests above set up, destroying the running bridge's session state,
    budgets, and hash-chained audit log on every pytest run.
    """

    def test_purge_deletes_injected_repo_corvin_but_not_real_one(self) -> None:
        import corvinOS.installer.core as core_mod

        real_repo_corvin = core_mod._REPO_ROOT / ".corvin"
        existed_before = real_repo_corvin.exists()

        with tempfile.TemporaryDirectory() as tmp:
            installer = _make_installer(Path(tmp))
            sandbox_corvin = installer.repo_root / ".corvin"
            sandbox_corvin.mkdir(parents=True)
            (sandbox_corvin / "sentinel").write_text("x")
            (installer.systemd_user_dir / "default.target.wants").mkdir(parents=True)
            sandbox_unit = installer.systemd_user_dir / "corvin-sandbox.service"
            sandbox_unit.write_text("[Unit]\n")

            def fake_run(cmd, **kwargs):  # noqa: ANN001
                result = mock.MagicMock()
                result.returncode = 0
                result.stdout = ""
                result.stderr = ""
                return result

            with mock.patch("corvinOS.installer.core.sys.platform", "linux"), \
                 mock.patch("corvinOS.installer.core.shutil.which", return_value=None), \
                 mock.patch("corvinOS.installer.core.subprocess.run", side_effect=fake_run), \
                 mock.patch("builtins.input", return_value="n"):
                installer.uninstall(purge=True)

            assert not sandbox_corvin.exists(), "purge must delete the injected repo .corvin"
            assert not sandbox_unit.exists(), "purge must sweep injected systemd units"

        assert real_repo_corvin.exists() == existed_before, (
            "uninstall(purge=True) touched the REAL repo .corvin — "
            "live bridge state would have been destroyed"
        )

    def test_win32_uninstall_never_reads_live_env_shortcut_roots(self) -> None:
        """A win32-mocked uninstall must resolve shortcut roots from the
        installer's INJECTED attributes, not os.environ. Regression for the
        review finding that tests 1-2 could unlink a real Windows dev's
        Startup/Desktop CorvinOS.lnk (4th incarnation of the wipe class).
        """
        import os as _os

        # A live APPDATA/USERPROFILE pointing at a REAL sentinel shortcut that
        # must survive because the installer uses its injected sandbox roots.
        with tempfile.TemporaryDirectory() as live, tempfile.TemporaryDirectory() as tmp:
            live_startup = Path(live) / "Start Menu/Programs/Startup"
            live_desktop = Path(live) / "Desktop"
            live_startup.mkdir(parents=True)
            live_desktop.mkdir(parents=True)
            live_startup_lnk = live_startup / "CorvinOS.lnk"
            live_desktop_lnk = live_desktop / "CorvinOS.lnk"
            live_startup_lnk.write_text("REAL user shortcut — must survive")
            live_desktop_lnk.write_text("REAL user shortcut — must survive")

            installer = _make_installer(Path(tmp))  # injects sandbox roots

            def fake_run(cmd, **kwargs):  # noqa: ANN001
                result = mock.MagicMock()
                result.returncode = 1
                result.stdout = ""
                result.stderr = "ERROR: not found"
                return result

            with mock.patch("corvinOS.installer.core.sys.platform", "win32"), \
                 mock.patch("corvinOS.installer.core.shutil.which", return_value=None), \
                 mock.patch("corvinOS.installer.core.subprocess.run", side_effect=fake_run), \
                 mock.patch.dict(
                     _os.environ,
                     {"APPDATA": str(Path(live)), "USERPROFILE": str(Path(live))},
                 ), \
                 mock.patch("builtins.input", return_value="n"):
                installer.uninstall(purge=True)

            assert live_startup_lnk.exists(), (
                "uninstall read a LIVE env shortcut root — real user shortcut deleted"
            )
            assert live_desktop_lnk.exists(), (
                "uninstall read a LIVE env shortcut root — real user shortcut deleted"
            )


if __name__ == "__main__":
    import sys as _sys

    t = TestWindowsAutostartUninstall()
    t.test_uninstall_removes_corvinos_console_scheduled_task_on_windows()
    t.test_uninstall_reports_no_task_found_without_erroring()
    t.test_uninstall_removes_startup_and_desktop_shortcuts_on_windows()
    t.test_uninstall_on_linux_does_not_attempt_schtasks()
    _tor = TestUninstallTouchesOnlyInjectedRoots()
    _tor.test_purge_deletes_injected_repo_corvin_but_not_real_one()
    _tor.test_win32_uninstall_never_reads_live_env_shortcut_roots()
    print("all tests passed")
    _sys.exit(0)
