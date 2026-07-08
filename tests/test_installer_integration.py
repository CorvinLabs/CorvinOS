"""Integration tests for the installer."""

import json
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from corvinOS.installer.core import CorvinInstaller
from corvinOS.shared.paths import corvin_home


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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
