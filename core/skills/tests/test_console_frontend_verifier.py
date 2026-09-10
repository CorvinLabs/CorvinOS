"""
E2E Tests for ConsoleFrontendVerifier Skill.
"""

import pytest
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

from core.skills.console_frontend_verifier import (
    ConsoleFrontendVerifier,
    VerificationStatus,
)


class TestConsoleFrontendVerifier:
    """Test ConsoleFrontendVerifier skill."""

    @pytest.fixture
    def temp_console_dir(self):
        """Create a temporary console directory structure."""
        with tempfile.TemporaryDirectory() as tmpdir:
            console_dir = Path(tmpdir)
            (console_dir / "dist" / "assets").mkdir(parents=True)
            (console_dir / "src" / "pages").mkdir(parents=True)

            # Create mock source file
            source_file = console_dir / "src" / "pages" / "test.tsx"
            source_file.write_text('export function TestPanel() { return <div>TEST_MARKER_V1</div>; }')

            yield console_dir, source_file

    @pytest.fixture
    def verifier(self, temp_console_dir):
        """Create a verifier with temp directory."""
        console_dir, _ = temp_console_dir
        verifier = ConsoleFrontendVerifier(console_dir=str(console_dir))
        verifier.backend_url = "http://localhost:8765"
        return verifier

    def test_marker_not_in_source(self, verifier, temp_console_dir):
        """Fail if marker not in source file."""
        console_dir, source_file = temp_console_dir

        result = verifier.verify_frontend_change(
            marker="MISSING_MARKER",
            location=str(source_file),
            auto_restart_backend=False,
        )

        assert result.status == VerificationStatus.FAIL
        assert "not found" in result.message.lower()

    def test_source_file_not_found(self, verifier):
        """Fail if source file doesn't exist."""
        result = verifier.verify_frontend_change(
            marker="ANY_MARKER",
            location="/nonexistent/file.tsx",
            auto_restart_backend=False,
        )

        assert result.status == VerificationStatus.FAIL
        assert "not found" in result.message.lower()

    def test_marker_in_bundle(self, temp_console_dir):
        """Helper: check marker detection in bundled assets."""
        console_dir, _ = temp_console_dir
        verifier = ConsoleFrontendVerifier(console_dir=str(console_dir))

        # Write a bundled asset with the marker
        bundle_file = console_dir / "dist" / "assets" / "index-abc123.js"
        bundle_file.write_text(
            "function hello() { console.log('TEST_MARKER_V1'); }"
        )

        found, asset_name = verifier._marker_in_bundle("TEST_MARKER_V1")
        assert found is True
        assert asset_name == "index-abc123.js"

    def test_marker_not_in_bundle(self, temp_console_dir):
        """Helper: marker not in any bundled asset."""
        console_dir, _ = temp_console_dir
        verifier = ConsoleFrontendVerifier(console_dir=str(console_dir))

        # Write a bundled asset without the marker
        bundle_file = console_dir / "dist" / "assets" / "index-abc123.js"
        bundle_file.write_text("function hello() { console.log('old code'); }")

        found, asset_name = verifier._marker_in_bundle("MISSING_MARKER")
        assert found is False
        assert asset_name is None

    def test_list_bundled_assets(self, temp_console_dir):
        """Helper: list bundled assets."""
        console_dir, _ = temp_console_dir
        verifier = ConsoleFrontendVerifier(console_dir=str(console_dir))

        # Create multiple bundled files
        (console_dir / "dist" / "assets" / "index-abc.js").touch()
        (console_dir / "dist" / "assets" / "chunk-123.js").touch()
        (console_dir / "dist" / "assets" / "style.css").touch()

        assets = verifier._list_bundled_assets()
        assert "index-abc.js" in assets
        assert "chunk-123.js" in assets
        # CSS should be filtered by glob("*.js")
        assert "style.css" not in assets

    @patch("requests.get")
    def test_get_deployed_hash_success(self, mock_get, temp_console_dir):
        """Helper: extract hash from backend response."""
        console_dir, _ = temp_console_dir
        verifier = ConsoleFrontendVerifier(console_dir=str(console_dir))

        # Mock backend response
        mock_response = Mock()
        mock_response.ok = True
        mock_response.text = '<script src="/assets/index-xyz789.js"></script>'
        mock_get.return_value = mock_response

        hash_name = verifier._get_deployed_hash()
        assert hash_name == "index-xyz789.js"

    @patch("requests.get")
    def test_get_deployed_hash_failure(self, mock_get, temp_console_dir):
        """Helper: fail if backend unavailable."""
        console_dir, _ = temp_console_dir
        verifier = ConsoleFrontendVerifier(console_dir=str(console_dir))

        mock_get.side_effect = Exception("Connection refused")

        hash_name = verifier._get_deployed_hash()
        assert hash_name is None

    def test_get_built_hash(self, temp_console_dir):
        """Helper: extract hash from dist/ directory."""
        console_dir, _ = temp_console_dir
        verifier = ConsoleFrontendVerifier(console_dir=str(console_dir))

        # Create a bundled file
        (console_dir / "dist" / "assets" / "index-def456.js").touch()

        hash_name = verifier._get_built_hash()
        assert hash_name == "index-def456.js"

    def test_clear_caches(self, temp_console_dir):
        """Helper: verify cache clearing works."""
        console_dir, _ = temp_console_dir
        verifier = ConsoleFrontendVerifier(console_dir=str(console_dir))

        # Create cache directories
        dist_dir = console_dir / "dist"
        vite_dir = console_dir / "node_modules" / ".vite"

        vite_dir.mkdir(parents=True, exist_ok=True)
        (dist_dir / "old_file.js").touch()
        (vite_dir / "old_cache.json").touch()

        assert (dist_dir / "old_file.js").exists()
        assert (vite_dir / "old_cache.json").exists()

        verifier._clear_caches()

        # Both should be gone
        assert not (dist_dir / "old_file.js").exists()
        assert not (vite_dir / "old_cache.json").exists()

    @patch("subprocess.run")
    def test_build_success(self, mock_run, temp_console_dir):
        """Helper: mock successful build."""
        console_dir, _ = temp_console_dir
        verifier = ConsoleFrontendVerifier(console_dir=str(console_dir))

        mock_run.return_value = Mock(stdout=b"Build complete")
        verifier._build()

        mock_run.assert_called_once()
        assert "build" in mock_run.call_args[0][0]

    @patch("subprocess.run")
    def test_build_failure(self, mock_run, temp_console_dir):
        """Helper: mock failed build."""
        console_dir, _ = temp_console_dir
        verifier = ConsoleFrontendVerifier(console_dir=str(console_dir))

        mock_run.side_effect = Exception("Build failed")

        with pytest.raises(Exception):
            verifier._build()

    @patch("subprocess.run")
    @patch("requests.get")
    def test_full_verification_success(self, mock_get, mock_run, temp_console_dir):
        """Integration: full verification succeeds."""
        console_dir, source_file = temp_console_dir
        verifier = ConsoleFrontendVerifier(console_dir=str(console_dir))

        # Mock successful build
        mock_run.return_value = Mock(stdout=b"Build ok")

        # Create bundled file with marker
        (console_dir / "dist" / "assets" / "index-new.js").write_text(
            "TEST_MARKER_V2 code"
        )

        # Mock backend response
        mock_response = Mock()
        mock_response.ok = True
        mock_response.text = '<script src="/assets/index-new.js"></script>'
        mock_get.return_value = mock_response

        # Verify
        result = verifier.verify_frontend_change(
            marker="TEST_MARKER_V2",
            location=str(source_file),
            auto_restart_backend=False,
        )

        assert result.status == VerificationStatus.PASS
        assert result.marker_found is True
        assert "verified" in result.message.lower()

    @patch("subprocess.run")
    @patch("requests.get")
    def test_verification_marker_not_bundled(
        self, mock_get, mock_run, temp_console_dir
    ):
        """Fail if marker not found in bundled assets."""
        console_dir, source_file = temp_console_dir
        verifier = ConsoleFrontendVerifier(console_dir=str(console_dir))

        # Mock successful build
        mock_run.return_value = Mock(stdout=b"Build ok")

        # Create bundled file WITHOUT the marker
        (console_dir / "dist" / "assets" / "index-old.js").write_text("old code")

        result = verifier.verify_frontend_change(
            marker="TEST_MARKER_NOT_HERE",
            location=str(source_file),
            auto_restart_backend=False,
        )

        assert result.status == VerificationStatus.FAIL
        assert "not found" in result.message.lower()
        assert result.marker_found is False

    @patch("subprocess.run")
    @patch("requests.get")
    def test_verification_hash_mismatch(
        self, mock_get, mock_run, temp_console_dir
    ):
        """Handle hash mismatch (backend not restarted)."""
        console_dir, source_file = temp_console_dir
        verifier = ConsoleFrontendVerifier(console_dir=str(console_dir))

        # Mock successful build
        mock_run.return_value = Mock(stdout=b"Build ok")

        # Create bundled file with marker
        (console_dir / "dist" / "assets" / "index-new.js").write_text(
            "TEST_MARKER code"
        )

        # Mock backend serving OLD hash
        mock_response = Mock()
        mock_response.ok = True
        mock_response.text = '<script src="/assets/index-old.js"></script>'
        mock_get.return_value = mock_response

        result = verifier.verify_frontend_change(
            marker="TEST_MARKER",
            location=str(source_file),
            auto_restart_backend=False,
        )

        # Should be NEEDS_BROWSER_REFRESH (not complete failure)
        assert result.status == VerificationStatus.NEEDS_BROWSER_REFRESH
        assert result.marker_found is True
        # Implies backend restart needed
        assert "hash mismatch" in result.message.lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
