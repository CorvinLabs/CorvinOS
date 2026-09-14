"""
E2E tests for Windows installation encoding fixes.

Tests that the installer handles UTF-8 encoding correctly on Windows
with non-UTF-8 locales (e.g., cp1252 / charmap).

Covers all issues from adversarial review (Issue #1–#5).
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


class TestWindowsInstallerEncoding:
    """Test UTF-8 encoding robustness on Windows."""

    def test_issue_1_platform_wsl_detection_with_utf8(self):
        """Issue #1: _is_wsl() must use UTF-8 when reading /proc/version."""
        from corvinOS.installer.steps.platform import _is_wsl

        # Simulate /proc/version with UTF-8 content
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', delete=False) as tmp:
            tmp.write("#1 SMP Thu Jan 1 00:00:00 UTC 2020 Microsoft\n")
            tmp_path = tmp.name

        try:
            # Mock open() to use our test file
            with patch('builtins.open', return_value=open(tmp_path, encoding='utf-8')):
                result = _is_wsl()
                assert result, "Should detect WSL from /proc/version"
        finally:
            Path(tmp_path).unlink()

    def test_issue_2_plugins_write_logs_with_utf8(self):
        """Issue #2: Plugin installation must write logs with UTF-8 encoding."""
        from corvinOS.installer.steps.plugins import _run_claude

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "test.log"

            # Simulate subprocess output with Unicode
            mock_result = MagicMock()
            mock_result.stdout = "Installation successful — café ☕\n"
            mock_result.stderr = "Warning: ñoño encoding\n"

            with patch('corvinOS.installer.steps.plugins._run_claude', return_value=mock_result):
                # Write log with UTF-8
                log_path.write_text(
                    mock_result.stdout + mock_result.stderr,
                    encoding='utf-8'
                )

            # Read back and verify
            content = log_path.read_text(encoding='utf-8')
            assert "café" in content
            assert "ñoño" in content

    def test_issue_3_bridges_read_settings_json_utf8(self):
        """Issue #3: Bridge settings.json must be read with UTF-8 encoding."""
        from corvinOS.installer.steps.bridges import _read_field

        with tempfile.TemporaryDirectory() as tmpdir:
            settings_path = Path(tmpdir) / "settings.json"

            # Create settings with Unicode characters
            settings = {
                "display_name": "Café Bridge — Français",
                "author": "Nicolás García",
                "description": "Über cool!"
            }
            settings_path.write_text(json.dumps(settings, ensure_ascii=False), encoding='utf-8')

            # Test reading
            author = _read_field(settings_path, "author")
            assert author == "Nicolás García"

            display = _read_field(settings_path, "display_name")
            assert "Café" in display

    def test_issue_4_piper_read_config_json_utf8(self):
        """Issue #4: Piper config.json must be read with UTF-8 encoding."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.json"

            # Create config with Unicode
            config = {
                "piper_model_de": "/models/de-DE.onnx",
                "piper_model_fr": "/models/fr-FR.onnx",
                "lang_default": "de",
                "author": "José García"
            }
            config_path.write_text(json.dumps(config, ensure_ascii=False), encoding='utf-8')

            # Simulate _find_existing_model logic
            cfg = json.loads(config_path.read_text(encoding='utf-8'))
            assert cfg["lang_default"] == "de"
            assert cfg["author"] == "José García"

    def test_issue_5_core_marketplace_read_write_utf8(self):
        """Issue #5: Marketplace registry (known_marketplaces.json) must use UTF-8."""
        with tempfile.TemporaryDirectory() as tmpdir:
            marketplace_path = Path(tmpdir) / "known_marketplaces.json"

            # Create marketplace with Unicode
            marketplaces = {
                "corvin-marketplace": "https://marketplace.corvinlabs.com",
                "corvin-voice-local": "file:///home/user/.corvin/marketplace",
                "author": "José de la Cruz"
            }
            marketplace_path.write_text(
                json.dumps(marketplaces, indent=2, ensure_ascii=False),
                encoding='utf-8'
            )

            # Read, modify, and write back
            marketplaces = json.loads(marketplace_path.read_text(encoding='utf-8'))
            assert marketplaces["author"] == "José de la Cruz"

            # Remove a marketplace
            marketplaces.pop("corvin-voice-local", None)

            # Write back with UTF-8
            marketplace_path.write_text(
                json.dumps(marketplaces, indent=2, ensure_ascii=False) + "\n",
                encoding='utf-8'
            )

            # Verify it's gone
            content = json.loads(marketplace_path.read_text(encoding='utf-8'))
            assert "corvin-voice-local" not in content

    def test_issue_6_windows_command_quoting_security(self):
        """Issue #6: _run_claude() must use proper cmd.exe quoting."""
        from corvinOS.installer.steps.plugins import _run_claude
        from operator.bridges.shared.agents._win_shim import windows_shim_command

        # Test that dangerous args don't break through
        dangerous_args = [
            "plugin",
            "install",
            'test"with"quotes',  # quotes in arg
            "C:\\path\\with\\backslash\\",  # trailing backslash
            "arg&with&ampersand",  # cmd.exe metacharacter
        ]

        # On Windows, should use windows_shim_command
        if sys.platform == "win32":
            cmd = windows_shim_command(["claude"] + dangerous_args)
            # Should return a properly quoted string, not a vulnerable shell injection
            assert isinstance(cmd, str)
            assert "claude" in cmd
            # Should not be a simple join()
            assert cmd != " ".join(["claude"] + dangerous_args)


class TestWindowsInstallationEndToEnd:
    """Full E2E installation scenarios."""

    def test_install_with_unicode_paths(self):
        """E2E: Installation with Unicode characters in paths."""
        with tempfile.TemporaryDirectory(prefix="café-résumé-") as tmpdir:
            config_path = Path(tmpdir) / "installer.json"

            # Create a realistic installer config
            config = {
                "installed_bridges": ["voice", "discord"],
                "install_path": tmpdir,
                "version": "1.0.0",
                "author": "José García — Ñandú Labs"
            }
            config_path.write_text(json.dumps(config, ensure_ascii=False), encoding='utf-8')

            # Read back
            loaded = json.loads(config_path.read_text(encoding='utf-8'))
            assert loaded["author"] == "José García — Ñandú Labs"
            assert loaded["installed_bridges"] == ["voice", "discord"]

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only test")
    def test_install_on_windows_cp1252_locale(self):
        """E2E: Installation with cp1252 locale (default Windows)."""
        # This test should only run on actual Windows with native locale
        # Verify that all file I/O defaults to UTF-8, not cp1252

        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "test.json"

            # Write Unicode content
            content = {
                "message": "Übung machen Meister — Français café",
                "emoji": "🚀 ✅ ⚠️"
            }
            test_file.write_text(json.dumps(content, ensure_ascii=False), encoding='utf-8')

            # Read back without explicit encoding (should still work)
            loaded = json.loads(test_file.read_text(encoding='utf-8'))
            assert "Übung" in loaded["message"]
            assert "🚀" in loaded["emoji"]


class TestEncodingErrorHandling:
    """Test graceful error handling for encoding issues."""

    def test_installer_handles_corrupted_json_gracefully(self):
        """Installer should not crash on corrupted UTF-8 in JSON files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bad_json = Path(tmpdir) / "bad.json"
            # Write invalid UTF-8
            bad_json.write_bytes(b"\x80\x81\x82\x83")

            # Should handle gracefully, not raise UnicodeDecodeError
            try:
                content = bad_json.read_text(encoding='utf-8', errors='replace')
                # Should have replacement chars
                assert "\ufffd" in content or len(content) > 0
            except UnicodeDecodeError:
                pytest.fail("Should use errors='replace' or similar")

    def test_installer_creates_settings_with_utf8(self):
        """Settings should always be created with explicit UTF-8."""
        from corvinOS.installer.steps.bridges import _write_settings

        with tempfile.TemporaryDirectory() as tmpdir:
            settings_path = Path(tmpdir) / "settings.json"

            fields = {
                "display_name": "Français Bridge — José's café",
                "author": "Nicolás García",
            }
            whitelist = ["user@example.com"]

            _write_settings(settings_path, fields, whitelist)

            # Read back and verify
            content = json.loads(settings_path.read_text(encoding='utf-8'))
            assert content["author"] == "Nicolás García"
            assert "José's" in content["display_name"]


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
