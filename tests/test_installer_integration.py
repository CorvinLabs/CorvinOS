"""Integration tests for the installer."""

import contextlib
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from corvinOS.installer.core import CorvinInstaller
from corvinOS.shared.paths import corvin_home

# Exact step sequence install() runs — kept in sync with core.py's install().
# A test asserts this order is what actually fires; if this list and install()
# drift apart, that test (not just this constant) will fail.
_INSTALL_STEPS = [
    "step_1_detect_platform",
    "step_2_create_directories",
    "step_3_system_dependencies",
    "step_4_install_claude_code",
    "step_5_claude_login",
    "step_6_bootstrap_hermes",
    "step_7_setup_stt",
    "step_8_setup_piper",
    "step_9_api_keys",
    "step_10_select_bridges",
    "step_11_install_bridges",
    "step_12_configure_bridges",
    "step_13_web_console",
    "step_14_register_services",
    "step_15_start_services",
    "step_16_register_plugins",
    "step_17_start_console",
    "step_18_finalise",
    "step_19_validate",
]


class TestCorvinInstallerIntegration:
    """Integration tests for CorvinInstaller."""

    def test_installer_initialization(self):
        """Test that installer initializes without errors."""
        installer = CorvinInstaller(interactive=False)
        assert installer.corvin_home
        assert installer.voice_config
        assert installer.service_manager
        assert installer.bridge_manager

    def test_installer_creates_directories(self):
        """Test that installer creates required directories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            installer = CorvinInstaller(interactive=False)

            # Override paths for testing
            with mock.patch.object(installer, "corvin_home", tmpdir_path):
                with mock.patch.object(installer, "voice_config", tmpdir_path / "config"):
                    installer.step_2_create_directories()

                    assert (tmpdir_path / "logs").exists()
                    assert (tmpdir_path / "sessions").exists()
                    assert (tmpdir_path / "bridges").exists()
                    assert (tmpdir_path / "tenants" / "_default" / "global").exists()

    def test_installer_bridge_selection(self):
        """A non-interactive install selects NO bridges by default.

        Auto-selecting all five registered + started token-less messenger
        services that crash-loop on a fresh box; bridges are configured later
        from the console once tokens exist. Any selected value must still be a
        known bridge.
        """
        installer = CorvinInstaller(interactive=False)
        installer.step_10_select_bridges()

        assert installer.selected_bridges == []
        assert all(b in CorvinInstaller.BRIDGES for b in installer.selected_bridges)

    def test_installer_saves_config(self):
        """Test that installer saves configuration to JSON."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            installer = CorvinInstaller(interactive=False)

            installer.selected_bridges = ["discord", "whatsapp"]
            with mock.patch.object(installer, "voice_config", tmpdir_path):
                tmpdir_path.mkdir(exist_ok=True)
                installer.step_18_finalise()

                config_path = tmpdir_path / "installer.json"
                assert config_path.exists()

                config = json.loads(config_path.read_text())
                assert config["installed_bridges"] == ["discord", "whatsapp"]
                assert config["version"] == "0.1.0"

    def test_installer_config_permissions(self):
        """Test that config file has restrictive permissions on Unix."""
        import sys

        if sys.platform == "win32":
            pytest.skip("Unix-only test")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            installer = CorvinInstaller(interactive=False)

            installer.selected_bridges = ["discord"]
            with mock.patch.object(installer, "voice_config", tmpdir_path):
                tmpdir_path.mkdir(exist_ok=True)
                installer.step_18_finalise()

                config_path = tmpdir_path / "installer.json"
                mode = config_path.stat().st_mode
                # Should be 0o600 (rw-------)
                assert (mode & 0o077) == 0  # No group/other permissions

    def test_bridge_venv_creation(self):
        """Test that bridge venvs are created correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            with mock.patch("corvinOS.shared.paths.bridges_home") as mock_bridges_home:
                mock_bridges_home.return_value = tmpdir_path

                installer = CorvinInstaller(interactive=False)
                installer.selected_bridges = ["discord"]

                # Create venv (without running step_4, just test bridge_manager)
                installer.bridge_manager.create_venv("discord")

                venv_dir = tmpdir_path / "discord" / "venv"
                # On Unix, check for bin; on Windows, check for Scripts
                if sys.platform == "win32":
                    assert (venv_dir / "Scripts").exists()
                else:
                    assert (venv_dir / "bin").exists()

    def test_installer_non_interactive_mode(self):
        """Test that non-interactive mode doesn't prompt."""
        installer = CorvinInstaller(interactive=False)
        # This should not raise or block
        assert not installer.interactive


class TestStep14AutoUpdatePreExec:
    """WA-19: the WebUI service registration must wire the PyPI auto-update
    check as pre_exec — otherwise autostart never upgrades, only a manual
    CLI invocation does (the gap this whole feature exists to close)."""

    def test_webui_registration_passes_pre_exec(self):
        installer = CorvinInstaller(interactive=False)
        installer.selected_bridges = []
        calls = []
        with mock.patch.object(sys, "platform", "linux"):
            with mock.patch.object(
                installer.service_manager, "install_service",
                side_effect=lambda **kw: calls.append(kw),
            ):
                installer.step_14_register_services()

        webui_calls = [c for c in calls if c["name"] == "webui"]
        assert len(webui_calls) == 1
        pre_exec = webui_calls[0].get("pre_exec")
        assert pre_exec, "webui service must set pre_exec (WA-19 auto-update check)"
        assert "_autoupdate_entrypoint.py" in pre_exec

    def test_autoupdate_entrypoint_script_exists_at_the_path_core_py_references(self):
        """A pre_exec pointing at a nonexistent file would silently no-op
        forever (ExecStartPre="-..." swallows the failure) — assert the file
        this session's fix actually points at is really there."""
        installer = CorvinInstaller(interactive=False)
        script = (
            installer.repo_root / "ops" / "launcher" / "corvin"
            / "_autoupdate_entrypoint.py"
        )
        assert script.is_file()


class TestCorvinInstallerWithPathsMocked:
    """Tests with mocked paths."""

    def test_installer_with_custom_paths(self):
        """Test installer with custom CORVIN_HOME."""
        import os

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            with mock.patch.dict(os.environ, {"CORVIN_HOME": str(tmpdir_path)}):
                from importlib import reload
                import corvinOS.shared.paths as paths_module

                reload(paths_module)
                assert paths_module.corvin_home() == tmpdir_path


class TestInstallOrchestration:
    """install() (core.py lines ~641-671) runs all 19 steps in a single
    try/except with sys.exit(1) on failure and no rollback. These tests drive
    the full install() call sequence rather than individual step_N methods."""

    def test_install_calls_all_steps_in_exact_order(self):
        """A refactor that reorders/drops/duplicates a step must fail a test."""
        installer = CorvinInstaller(interactive=False)
        call_order: list[str] = []

        with contextlib.ExitStack() as stack:
            for name in _INSTALL_STEPS:
                stack.enter_context(
                    mock.patch.object(
                        installer, name,
                        side_effect=lambda n=name: call_order.append(n),
                    )
                )
            installer.install()

        assert call_order == _INSTALL_STEPS

    def test_install_mid_flow_exception_exits_1_without_retry_or_rollback(self):
        """step_9_api_keys raising must: (a) sys.exit(1), (b) run every step
        BEFORE it exactly once (no retry), (c) never run any step AFTER it."""
        installer = CorvinInstaller(interactive=False)
        call_order: list[str] = []

        def _boom():
            call_order.append("step_9_api_keys")
            raise RuntimeError("simulated step_9 failure")

        with contextlib.ExitStack() as stack:
            for name in _INSTALL_STEPS:
                if name == "step_9_api_keys":
                    stack.enter_context(
                        mock.patch.object(installer, name, side_effect=_boom)
                    )
                else:
                    stack.enter_context(
                        mock.patch.object(
                            installer, name,
                            side_effect=lambda n=name: call_order.append(n),
                        )
                    )

            with pytest.raises(SystemExit) as exc_info:
                installer.install()

        assert exc_info.value.code == 1

        idx = _INSTALL_STEPS.index("step_9_api_keys")
        before, after = _INSTALL_STEPS[:idx], _INSTALL_STEPS[idx + 1:]
        # every earlier step ran, exactly once, in order — no silent retry
        assert call_order == before + ["step_9_api_keys"]
        # nothing after the failing step ever ran
        for name in after:
            assert name not in call_order

    def test_install_rerun_is_idempotent_on_directories_and_profile_defaults(self):
        """Re-running install() after a crash is the real-world recovery path.
        A second run must not clobber the seeded profile.json defaults nor
        corrupt installer.json — only step_2 (dirs+profile seed) and step_18
        (config save) touch real state; everything else is mocked out."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            corvin_home_dir = tmpdir_path / "home"
            voice_config_dir = tmpdir_path / "config"
            installer = CorvinInstaller(interactive=False)
            installer.selected_bridges = ["discord"]

            steps_to_mock = [s for s in _INSTALL_STEPS
                              if s not in ("step_2_create_directories", "step_18_finalise")]

            with mock.patch.object(installer, "corvin_home", corvin_home_dir), \
                 mock.patch.object(installer, "voice_config", voice_config_dir), \
                 contextlib.ExitStack() as stack:
                for name in steps_to_mock:
                    stack.enter_context(mock.patch.object(installer, name, side_effect=lambda: None))

                installer.install()

                profile_path = voice_config_dir / "profile.json"
                config_path = voice_config_dir / "installer.json"
                first_profile = json.loads(profile_path.read_text())
                first_config = json.loads(config_path.read_text())
                assert first_profile == {
                    "voice_audience_metaphors": "on", "voice_audience_learning": 3,
                }

                # Simulate a user mutating profile.json between runs (e.g. via
                # /profile set) — a correct re-run must NOT clobber it, since
                # step_2 only seeds defaults "if not profile_file.exists()".
                mutated = {"voice_audience_metaphors": "off", "voice_audience_learning": 5}
                profile_path.write_text(json.dumps(mutated))

                installer.install()

                second_profile = json.loads(profile_path.read_text())
                second_config = json.loads(config_path.read_text())

        # profile.json must be left exactly as the user set it — a second
        # install() run is not allowed to silently reset it to defaults.
        assert second_profile == mutated
        # installer.json is still valid, well-formed JSON with the same shape.
        assert second_config["installed_bridges"] == first_config["installed_bridges"] == ["discord"]
        assert second_config["version"] == first_config["version"]


class TestRestore:
    """restore() (core.py lines ~673-788) is the corvin-restore recovery path:
    reload the bridge manifest, stop services, rebuild the console frontend,
    and restart everything. It had zero test coverage before this suite."""

    def _make_installer(self, tmpdir_path: Path) -> CorvinInstaller:
        installer = CorvinInstaller(interactive=False)
        installer.voice_config = tmpdir_path / "config"
        installer.voice_config.mkdir(parents=True, exist_ok=True)
        # Point the systemd-unit-discovery scan at an empty sandbox dir so this
        # test doesn't pick up real corvin-voice-bridge-*.service units that
        # may exist on the machine actually running the test suite.
        installer.systemd_user_dir = tmpdir_path / "systemd-user-empty"
        return installer

    def test_restore_falls_back_to_all_bridges_on_corrupted_manifest(self):
        """A malformed (not merely absent) installer.json must still trigger
        the 'assume everything is installed' fallback, not crash restore()."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            installer = self._make_installer(tmpdir_path)
            (installer.voice_config / "installer.json").write_text("{not valid json!!")

            with mock.patch.object(installer.service_manager, "stop_service"), \
                 mock.patch.object(installer.service_manager, "start_service"), \
                 mock.patch("corvinOS.installer.core.subprocess.run",
                            return_value=SimpleNamespace(returncode=1)), \
                 mock.patch("corvinOS.installer.core._IS_WHEEL_INSTALL", True), \
                 mock.patch("corvinOS.installer.core._console.start_server"), \
                 mock.patch("corvinOS.installer.steps.console._kill_port"):
                installer.restore()

        assert installer.selected_bridges == CorvinInstaller.BRIDGES
        assert installer.selected_bridges is not CorvinInstaller.BRIDGES  # must be a copy

    def test_restore_kills_port_and_rebuilds_frontend_cleanly_when_not_wheel(self):
        """When not a wheel install, restore() must remove dist/ THEN rebuild,
        and always kill port 8765 regardless of whether systemd managed webui."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            installer = self._make_installer(tmpdir_path)
            installer.repo_root = tmpdir_path / "repo"
            dist_dir = (installer.repo_root / "core" / "console" / "corvin_console"
                        / "web-next" / "dist")
            dist_dir.mkdir(parents=True)
            (dist_dir / "index.html").write_text("<html></html>")

            with mock.patch.object(installer.service_manager, "stop_service"), \
                 mock.patch.object(installer.service_manager, "start_service"), \
                 mock.patch("corvinOS.installer.core.subprocess.run",
                            return_value=SimpleNamespace(returncode=1)), \
                 mock.patch("corvinOS.installer.core._IS_WHEEL_INSTALL", False), \
                 mock.patch("corvinOS.installer.core._console.build_frontend",
                            return_value=True) as mock_build, \
                 mock.patch("corvinOS.installer.core._console.start_server"), \
                 mock.patch("corvinOS.installer.steps.console._kill_port") as mock_kill:
                installer.restore()

            mock_kill.assert_called_once_with(8765)
            mock_build.assert_called_once_with(installer.repo_root)
            # dist/ must already be gone by the time build_frontend runs —
            # otherwise this isn't a genuine clean rebuild.
            assert not dist_dir.exists()

    def test_restore_skips_foreground_start_when_systemd_restart_succeeds(self):
        """Successful systemd stop + successful systemd start must NOT also
        foreground-launch the console — that would double-start uvicorn."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            installer = self._make_installer(tmpdir_path)

            def _fake_run(cmd, **kwargs):
                # Both "stop" and "start" systemctl invocations succeed.
                return SimpleNamespace(returncode=0)

            with mock.patch.object(installer.service_manager, "stop_service"), \
                 mock.patch.object(installer.service_manager, "start_service"), \
                 mock.patch("corvinOS.installer.core.subprocess.run", side_effect=_fake_run), \
                 mock.patch("corvinOS.installer.core._IS_WHEEL_INSTALL", True), \
                 mock.patch("corvinOS.installer.core._console.start_server") as mock_start, \
                 mock.patch("corvinOS.installer.steps.console._kill_port"):
                installer.restore()

            mock_start.assert_not_called()

    def test_restore_falls_back_to_foreground_start_when_systemd_start_fails(self):
        """Stopped via systemd, but the restart fails — restore() must still
        bring the console back up via the foreground fallback."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            installer = self._make_installer(tmpdir_path)

            def _fake_run(cmd, **kwargs):
                if "stop" in cmd:
                    return SimpleNamespace(returncode=0)
                return SimpleNamespace(returncode=1)  # "start" fails

            with mock.patch.object(installer.service_manager, "stop_service"), \
                 mock.patch.object(installer.service_manager, "start_service"), \
                 mock.patch("corvinOS.installer.core.subprocess.run", side_effect=_fake_run), \
                 mock.patch("corvinOS.installer.core._IS_WHEEL_INSTALL", True), \
                 mock.patch("corvinOS.installer.core._console.start_server") as mock_start, \
                 mock.patch("corvinOS.installer.steps.console._kill_port"):
                installer.restore()

            mock_start.assert_called_once_with(installer.repo_root)

    def test_restore_reads_valid_manifest_without_falling_back(self):
        """Sanity check: a well-formed manifest is honoured verbatim, so the
        fallback tests above are actually exercising the fallback branch and
        not just always defaulting to BRIDGES regardless of manifest state."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            installer = self._make_installer(tmpdir_path)
            (installer.voice_config / "installer.json").write_text(
                json.dumps({"installed_bridges": ["slack"]})
            )

            with mock.patch.object(installer.service_manager, "stop_service"), \
                 mock.patch.object(installer.service_manager, "start_service"), \
                 mock.patch("corvinOS.installer.core.subprocess.run",
                            return_value=SimpleNamespace(returncode=1)), \
                 mock.patch("corvinOS.installer.core._IS_WHEEL_INSTALL", True), \
                 mock.patch("corvinOS.installer.core._console.start_server"), \
                 mock.patch("corvinOS.installer.steps.console._kill_port"):
                installer.restore()

        assert installer.selected_bridges == ["slack"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
