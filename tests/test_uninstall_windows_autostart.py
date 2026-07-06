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
    installer = CorvinInstaller(interactive=False)
    installer.corvin_home = tmpdir / "corvin_home"
    installer.voice_config = tmpdir / "voice_config"
    installer.corvin_home.mkdir(parents=True, exist_ok=True)
    installer.voice_config.mkdir(parents=True, exist_ok=True)
    # Neutralise the real service manager so uninstall() doesn't touch any
    # actual OS service state on the machine running this test.
    installer.service_manager = mock.MagicMock()
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


if __name__ == "__main__":
    import sys as _sys

    t = TestWindowsAutostartUninstall()
    t.test_uninstall_removes_corvinos_console_scheduled_task_on_windows()
    t.test_uninstall_reports_no_task_found_without_erroring()
    t.test_uninstall_on_linux_does_not_attempt_schtasks()
    print("all tests passed")
    _sys.exit(0)
