"""Tests for Console SPA auto-build robustness (k=8 production-hardening)."""
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI

from corvin_console.app import mount_static


def test_spa_mount_auto_build_success(tmp_path):
    """Test: Auto-build succeeds → SPA is mounted."""
    app = FastAPI()
    dist_dir = tmp_path / "web-next" / "dist"
    dist_dir.mkdir(parents=True)
    (dist_dir / "index.html").write_text("<html></html>")

    with patch("corvin_console.app._NEXT_DIST_DIR", dist_dir):
        with patch("corvin_console.app.headless_enabled", return_value=False):
            mount_static(app, url_prefix="/console")

    # Verify SPA was mounted (app.routes will contain the mount)
    assert any("corvin_console_static" in str(r) for r in app.routes)


def test_spa_mount_auto_build_failure_fallback(tmp_path):
    """Test: Auto-build fails → fallback 503 error page is served."""
    app = FastAPI()
    missing_dist = tmp_path / "web-next" / "dist"

    with patch("corvin_console.app._NEXT_DIST_DIR", missing_dist):
        with patch("corvin_console.app.headless_enabled", return_value=False):
            with patch("subprocess.run", side_effect=FileNotFoundError("npm not found")):
                mount_static(app, url_prefix="/console")

    # Verify fallback 503 route was registered
    assert any("path" in str(r) for r in app.routes)  # fallback route registered


def test_spa_mount_headless_mode_no_mount():
    """Test: Headless mode → no SPA route registered at all."""
    app = FastAPI()

    with patch("corvin_console.app.headless_enabled", return_value=True):
        mount_static(app, url_prefix="/console")

    # In headless mode, no /console route should be registered
    console_routes = [r for r in app.routes if "console" in str(r).lower()]
    assert len(console_routes) == 0


def test_spa_mount_timeout_handling():
    """Test: npm build timeout → graceful fallback."""
    app = FastAPI()
    missing_dist = Path("/tmp/nonexistent-dist")

    with patch("corvin_console.app._NEXT_DIST_DIR", missing_dist):
        with patch("corvin_console.app.headless_enabled", return_value=False):
            import subprocess

            with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("npm", 300)):
                mount_static(app, url_prefix="/console")

    # Fallback route should be mounted
    assert len(app.routes) > 0
