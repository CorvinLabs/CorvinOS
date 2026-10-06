"""Forge Bundle import unit tests (ADR-2229 Phase 3).

Tests for import_bundle, extract_bundle, and per-artifact staging.
"""
from pathlib import Path
import pytest
import zipfile
import json
import io

from core.forge_bundle.import_module import import_bundle, extract_bundle, ImportResult, ImportError
from core.forge_bundle.validate import validate_bundle


TENANT_ID = "_default"


@pytest.fixture
def valid_bundle_bytes() -> bytes:
    """Create a minimal valid Forge Bundle ZIP."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        manifest = {
            "id": "test-bundle",
            "version": "1.0.0",
            "timestamp": "2026-01-01T00:00:00Z",
            "artifacts": [],
        }
        zf.writestr("forge-bundle.json", json.dumps(manifest))
    return buf.getvalue()


class TestImportBundle:
    """Tests for import_bundle()."""

    def test_import_empty_bundle(self, valid_bundle_bytes):
        """Importing empty bundle succeeds."""
        result = import_bundle(
            data=valid_bundle_bytes,
            tenant_id=TENANT_ID,
            user_id="test_user",
        )

        assert isinstance(result, ImportResult)
        assert result.bundle_id == "test-bundle"
        assert result.artifacts_staged == 0
        assert result.artifacts_failed == 0

    def test_import_returns_result_shape(self, valid_bundle_bytes):
        """import_bundle returns ImportResult with expected fields."""
        result = import_bundle(
            data=valid_bundle_bytes,
            tenant_id=TENANT_ID,
            user_id="test_user",
        )

        assert hasattr(result, "bundle_id")
        assert hasattr(result, "bundle_version")
        assert hasattr(result, "success")
        assert hasattr(result, "artifacts_staged")
        assert hasattr(result, "artifacts_failed")
        assert hasattr(result, "errors")


class TestExtractBundle:
    """Tests for extract_bundle()."""

    def test_extract_valid_bundle(self, valid_bundle_bytes):
        """extract_bundle returns envelope and zipfile."""
        envelope, zf = extract_bundle(valid_bundle_bytes)

        assert envelope.id == "test-bundle"
        assert envelope.version == "1.0.0"
        assert isinstance(zf, zipfile.ZipFile)
        zf.close()

    def test_extract_malformed_zip(self):
        """Malformed ZIP raises ImportError."""
        bad_bytes = b"not a zip file"

        with pytest.raises(ImportError):
            extract_bundle(bad_bytes)
