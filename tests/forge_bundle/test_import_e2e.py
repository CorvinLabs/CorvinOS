"""Forge Bundle import E2E tests (ADR-2229 Phase 3).

End-to-end tests for POST /v1/console/forge-bundles/import.
"""
import io
import json
import zipfile
import pytest


TENANT_ID = "_default"


@pytest.fixture
def valid_bundle_bytes():
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


class TestImportEndpoint:
    """Tests for POST /v1/console/forge-bundles/import."""

    def test_import_valid_bundle(self, client, valid_bundle_bytes):
        """POST with valid bundle returns 200."""
        response = client.post(
            "/v1/console/forge-bundles/import",
            files={"file": ("test-bundle.zip", valid_bundle_bytes)},
        )

        assert response.status_code == 200
        data = response.json()
        assert "bundle_id" in data
        assert "artifact_count" in data

    def test_oversized_bundle_rejected(self, client):
        """Bundle exceeding size limit returns 413."""
        large_data = b"x" * (51 * 1024 * 1024)  # 51 MiB

        response = client.post(
            "/v1/console/forge-bundles/import",
            files={"file": ("large.zip", large_data)},
        )

        assert response.status_code == 413
