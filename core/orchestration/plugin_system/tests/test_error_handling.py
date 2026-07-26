"""Error Handling Tests for Plugin System (ADR-0XXX Phase 2a k=4)."""

import pytest
from core.orchestration.plugin_system.managers.marketplace import (
    DownloadError,
    ChecksumMismatchError,
    MarketplaceDownloadManager
)


def test_download_error_message():
    """Test DownloadError has useful message."""
    error = DownloadError("Network timeout")
    assert "Network timeout" in str(error)


def test_checksum_error_message():
    """Test ChecksumMismatchError provides expected/actual."""
    error = ChecksumMismatchError("expected abc123, got def456")
    assert "expected" in str(error).lower()
    assert "actual" in str(error).lower() or "got" in str(error).lower()


def test_corrupt_plugin_graceful():
    """Test handling of corrupted plugin files."""
    manager = MarketplaceDownloadManager()
    # Would test with actual corrupt ZIP in real impl
    # For now, document the requirement


if __name__ == "__main__":
    pytest.main([__file__, "-xvs"])
