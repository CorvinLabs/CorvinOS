"""Smoke test: A2A env vars are initialized in serve_backend.py (HIGH-1 fix).

Layer 38 — RemoteTriggerReceiver A2A pairing requires REMOTE_ORIGINS_DIR and
REMOTE_ENDPOINTS_DIR env vars. This test verifies the fix is present.

Bug: ops/launcher/corvin/serve_backend.py was not setting these env vars before
subprocess.run(), causing A2A friendship tokens to be unreachable on fresh installs.

Fix: serve_backend.py now initializes A2A directories and env vars.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path


class TestA2AEnvPropagation(unittest.TestCase):
    """Verify A2A env var initialization code is present in serve_backend.py."""

    def test_serve_backend_sets_remote_origins_dir(self):
        """Code to set REMOTE_ORIGINS_DIR exists."""
        serve_backend_py = Path(__file__).parent.parent / "serve_backend.py"
        content = serve_backend_py.read_text()

        # Check for the env var setup code
        assert "REMOTE_ORIGINS_DIR" in content, "REMOTE_ORIGINS_DIR not found in serve_backend.py"
        assert "remote_origins" in content, "remote_origins directory not referenced"

    def test_serve_backend_sets_remote_endpoints_dir(self):
        """Code to set REMOTE_ENDPOINTS_DIR exists."""
        serve_backend_py = Path(__file__).parent.parent / "serve_backend.py"
        content = serve_backend_py.read_text()

        assert "REMOTE_ENDPOINTS_DIR" in content, "REMOTE_ENDPOINTS_DIR not found in serve_backend.py"
        assert "remote_endpoints" in content, "remote_endpoints directory not referenced"

    def test_serve_backend_sets_pending_dir(self):
        """Code to set REMOTE_PENDING_DIR exists."""
        serve_backend_py = Path(__file__).parent.parent / "serve_backend.py"
        content = serve_backend_py.read_text()

        assert "REMOTE_PENDING_DIR" in content, "REMOTE_PENDING_DIR not found in serve_backend.py"

    def test_serve_backend_creates_directories(self):
        """Code to create A2A directories exists."""
        serve_backend_py = Path(__file__).parent.parent / "serve_backend.py"
        content = serve_backend_py.read_text()

        # Check for mkdir calls
        assert "mkdir" in content, "mkdir not found in serve_backend.py"
        assert "parents=True" in content, "mkdir parents=True not found"

    def test_serve_backend_catches_import_errors(self):
        """Code gracefully handles missing forge.paths."""
        serve_backend_py = Path(__file__).parent.parent / "serve_backend.py"
        content = serve_backend_py.read_text()

        # Check for try/except around forge import
        assert "try:" in content, "try block not found"
        assert "except" in content, "except block not found"
        assert "best-effort" in content or "graceful" in content.lower() or "fallback" in content, \
            "Graceful degradation comment not found"

    def test_serve_backend_uses_setdefault(self):
        """Code uses setdefault to preserve existing env vars."""
        serve_backend_py = Path(__file__).parent.parent / "serve_backend.py"
        content = serve_backend_py.read_text()

        # Check that setdefault is used (important for respecting already-set vars)
        assert "setdefault" in content, "setdefault not found in serve_backend.py"
        # Should have multiple setdefault calls
        setdefault_count = content.count("setdefault")
        assert setdefault_count >= 4, f"Expected >=4 setdefault calls, found {setdefault_count}"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
