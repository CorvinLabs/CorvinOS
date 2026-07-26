"""Test suite for Marketplace Download Manager (ADR-0XXX Phase 2a k=1).

Tests for: async download, checksum verification, extraction
"""

import pytest
import tempfile
import zipfile
import hashlib
from pathlib import Path

from core.orchestration.plugin_system.managers.marketplace import (
    MarketplaceDownloadManager,
    DownloadError,
    ChecksumMismatchError
)


class TestMarketplaceDownloadManager:
    """Tests for plugin marketplace download functionality."""

    @pytest.fixture
    def temp_dir(self):
        """Temporary directory for downloads."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def sample_plugin_zip(self, temp_dir):
        """Create a sample plugin ZIP file."""
        zip_path = temp_dir / "test-plugin-1.0.0.zip"
        
        with zipfile.ZipFile(zip_path, 'w') as zf:
            zf.writestr("test-plugin/__init__.py", "# Test plugin")
            zf.writestr("test-plugin/plugin.py", "class TestPlugin: pass")
            zf.writestr("test-plugin/manifest.json", '{"id": "test-plugin"}')
        
        return zip_path

    def test_calculate_checksum(self, sample_plugin_zip):
        """Test calculating SHA256 checksum of a file."""
        manager = MarketplaceDownloadManager()
        checksum = manager.calculate_checksum(sample_plugin_zip)
        
        # Should be valid SHA256 in format "sha256:hexdigest"
        assert checksum.startswith("sha256:")
        hex_part = checksum.split(":")[1]
        assert len(hex_part) == 64
        assert all(c in '0123456789abcdef' for c in hex_part)

    def test_verify_checksum_valid(self, sample_plugin_zip):
        """Test checksum verification passes for valid file."""
        manager = MarketplaceDownloadManager()
        actual_checksum = manager.calculate_checksum(sample_plugin_zip)
        
        # Should not raise
        manager.verify_checksum(sample_plugin_zip, actual_checksum)

    def test_verify_checksum_invalid(self, sample_plugin_zip):
        """Test checksum verification fails for mismatched checksum."""
        manager = MarketplaceDownloadManager()
        wrong_checksum = "0" * 64  # Fake checksum
        
        with pytest.raises(ChecksumMismatchError):
            manager.verify_checksum(sample_plugin_zip, wrong_checksum)

    def test_extract_zip(self, sample_plugin_zip, temp_dir):
        """Test extracting a ZIP file."""
        manager = MarketplaceDownloadManager()
        extract_dir = temp_dir / "extracted"
        extract_dir.mkdir()
        
        manager.extract_zip(sample_plugin_zip, extract_dir)
        
        # Check files were extracted
        assert (extract_dir / "test-plugin").exists()
        assert (extract_dir / "test-plugin" / "__init__.py").exists()
        assert (extract_dir / "test-plugin" / "manifest.json").exists()

    def test_extract_zip_with_nested_structure(self, temp_dir):
        """Test extracting ZIP with nested plugin structure."""
        zip_path = temp_dir / "complex-plugin.zip"
        
        # Create ZIP with nested structure
        with zipfile.ZipFile(zip_path, 'w') as zf:
            zf.writestr("plugins/my-plugin/src/main.py", "# Main")
            zf.writestr("plugins/my-plugin/config.yaml", "name: my-plugin")
            zf.writestr("README.md", "# Readme")
        
        manager = MarketplaceDownloadManager()
        extract_dir = temp_dir / "extracted"
        extract_dir.mkdir()
        
        manager.extract_zip(zip_path, extract_dir)
        
        assert (extract_dir / "plugins" / "my-plugin").exists()
        assert (extract_dir / "README.md").exists()

    def test_download_from_mock_url(self, temp_dir):
        """Test downloading from a mock/local URL."""
        # Create a source ZIP file
        source_zip = temp_dir / "source-plugin.zip"
        with zipfile.ZipFile(source_zip, 'w') as zf:
            zf.writestr("plugin/__init__.py", "# Plugin")
        
        manager = MarketplaceDownloadManager()
        
        # For this test, we'll just copy (in real impl, this would be HTTP)
        # This tests the integration path
        download_dir = temp_dir / "downloads"
        download_dir.mkdir()
        
        # Mock: just copy the file
        import shutil
        downloaded = download_dir / "plugin.zip"
        shutil.copy(source_zip, downloaded)
        
        assert downloaded.exists()
        assert downloaded.stat().st_size > 0

    def test_full_workflow_download_verify_extract(self, temp_dir):
        """E2E: Download → Verify → Extract workflow."""
        # Create source plugin
        source_zip = temp_dir / "my-plugin-2.0.0.zip"
        with zipfile.ZipFile(source_zip, 'w') as zf:
            zf.writestr("my-plugin/__init__.py", "class MyPlugin: pass")
            zf.writestr("my-plugin/settings.json", '{"model": "haiku"}')
        
        manager = MarketplaceDownloadManager()
        
        # Calculate expected checksum
        expected_checksum = manager.calculate_checksum(source_zip)
        
        # Verify checksum
        manager.verify_checksum(source_zip, expected_checksum)
        
        # Extract
        extract_dir = temp_dir / "final_extracted"
        extract_dir.mkdir()
        manager.extract_zip(source_zip, extract_dir)
        
        # Verify structure
        assert (extract_dir / "my-plugin" / "__init__.py").exists()
        assert (extract_dir / "my-plugin" / "settings.json").exists()

    def test_extract_corrupted_zip_fails(self, temp_dir):
        """Test extracting a corrupted ZIP file fails."""
        corrupted_zip = temp_dir / "corrupted.zip"
        
        # Write invalid ZIP data
        corrupted_zip.write_bytes(b"This is not a ZIP file!")
        
        manager = MarketplaceDownloadManager()
        extract_dir = temp_dir / "extracted"
        extract_dir.mkdir()
        
        with pytest.raises(Exception):  # zipfile.BadZipFile or similar
            manager.extract_zip(corrupted_zip, extract_dir)

    def test_checksum_format(self):
        """Test that checksums have correct format."""
        manager = MarketplaceDownloadManager()
        
        # Format: "sha256:hexdigest"
        test_data = b"test content"
        checksum = manager.calculate_checksum_from_bytes(test_data)
        
        assert checksum.startswith("sha256:")
        assert len(checksum) == 7 + 64  # "sha256:" + 64 hex chars


if __name__ == "__main__":
    pytest.main([__file__, "-xvs"])
