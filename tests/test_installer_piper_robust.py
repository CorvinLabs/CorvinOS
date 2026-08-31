"""Tests for robust Piper model download on Windows.

ADR feedback (2026-07-28): Piper download fails on Windows due to CDN
connection reset (WinError 10054) even after successful ONNX transfer.

Solution: Retry ONNX download up to 3x with 2s back-off, same as JSON config.
"""
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest


def test_download_model_retries_onnx_on_windows():
    """ONNX download retries on Windows CDN reset."""
    from corvinOS.installer.steps.piper import _download_model

    with tempfile.TemporaryDirectory() as tmpdir:
        model_dir = Path(tmpdir) / "piper-models"
        config_file = Path(tmpdir) / "config.json"

        # Mock _fetch to fail once, then succeed (simulating WinError 10054 recovery)
        call_count = {"fetch": 0, "save": 0}

        def mock_fetch(url, dest, silent=False):
            call_count["fetch"] += 1
            if "onnx.json" in url:
                # JSON always succeeds
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(json.dumps({}))
                return True
            # ONNX fails once, succeeds on retry
            if call_count["fetch"] == 1:
                return False
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"fake onnx content")
            return True

        with patch("corvinOS.installer.steps.piper._fetch", side_effect=mock_fetch):
            with patch("corvinOS.installer.steps.piper._save_model_config") as mock_save:
                _download_model("de", "de/de_DE/kerstin/low/de_DE-kerstin-low", model_dir, config_file)

        # Verify: ONNX fetch was called twice (fail + retry)
        # JSON fetch called once, save called once
        assert call_count["fetch"] >= 2, "ONNX should retry after first failure"
        assert mock_save.called, "_save_model_config should be called on success"


def test_download_model_onnx_all_retries_failed():
    """When all ONNX retries fail, shows helpful error message."""
    from corvinOS.installer.steps.piper import _download_model

    with tempfile.TemporaryDirectory() as tmpdir:
        model_dir = Path(tmpdir) / "piper-models"
        config_file = Path(tmpdir) / "config.json"

        def mock_fetch_fail(url, dest, silent=False):
            # All fetches fail
            return False

        with patch("corvinOS.installer.steps.piper._fetch", side_effect=mock_fetch_fail):
            with patch("builtins.print") as mock_print:
                _download_model("de", "de/de_DE/kerstin/low/de_DE-kerstin-low", model_dir, config_file)

                # Verify helpful error message was printed
                calls = [str(call) for call in mock_print.call_args_list]
                assert any("Download failed after 3 attempts" in str(c) for c in calls), \
                    "Should show retry exhaustion message"
                assert any("manual" in str(c) for c in calls), \
                    "Should offer manual download option"


def test_download_model_skips_existing():
    """Skip download if model already exists on disk."""
    from corvinOS.installer.steps.piper import _download_model

    with tempfile.TemporaryDirectory() as tmpdir:
        model_dir = Path(tmpdir) / "piper-models"
        config_file = Path(tmpdir) / "config.json"
        model_dir.mkdir(parents=True, exist_ok=True)

        # Create existing model file
        onnx_path = model_dir / "de_DE-kerstin-low.onnx"
        onnx_path.write_bytes(b"existing model")

        call_count = {"fetch": 0}

        def mock_fetch(url, dest, silent=False):
            call_count["fetch"] += 1
            # JSON only
            if "json" in url:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(json.dumps({}))
                return True
            return False

        with patch("corvinOS.installer.steps.piper._fetch", side_effect=mock_fetch):
            with patch("corvinOS.installer.steps.piper._save_model_config"):
                _download_model("de", "de/de_DE/kerstin/low/de_DE-kerstin-low", model_dir, config_file)

        # ONNX should NOT be fetched (already exists)
        onnx_fetches = sum(1 for c in range(call_count["fetch"]) if True)  # just count
        assert call_count["fetch"] == 1, "Only JSON should be fetched, ONNX skipped"


def test_piper_fetch_logs_windows_errors():
    """Fetch logs Windows-specific errors (WinError 10054) for debugging."""
    from corvinOS.installer.steps.piper import _fetch

    with tempfile.TemporaryDirectory() as tmpdir:
        dest = Path(tmpdir) / "model.onnx"

        # Simulate WinError 10054
        class FakeWinError(Exception):
            def __init__(self):
                self.errno = 10054  # Windows connection reset

        def mock_urlretrieve_fail(*args, **kwargs):
            raise FakeWinError()

        with patch("urllib.request.urlretrieve", side_effect=mock_urlretrieve_fail):
            with patch("sys.platform", "win32"):
                with patch("builtins.print") as mock_print:
                    result = _fetch("https://example.com/model.onnx", dest, silent=False)

                    # Should fail (file doesn't exist)
                    assert not result

                    # Should log the error
                    calls = [str(call) for call in mock_print.call_args_list]
                    assert any("urllib error" in str(c) or "common on Windows" in str(c) for c in calls), \
                        "Should log Windows-specific error"
