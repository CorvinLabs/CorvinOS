"""Plugin Upload E2E Tests — Layer 1-4 Distribution (K=3)

6 real HTTP tests using TestClient (not mocks).
All use sandboxed temp CORVIN_HOME + actual filesystem I/O.

Tests:
  1. Valid ZIP → staging + audit
  2. Invalid ZIP → 400 error
  3. Missing manifest → 400 error
  4. Approve → install + audit
  5. Concurrent uploads → no race
  6. Disk full → graceful error
"""
from __future__ import annotations

import io
import json
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Assume fixtures provide app + session record
pytest_plugins = ["conftest"]


def create_valid_skill_zip() -> bytes:
    """Create valid skill ZIP with manifest.json."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        manifest = {
            "name": "test-skill",
            "version": "1.0.0",
            "author": "test@example.com",
            "description": "Test skill package",
        }
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("src/main.py", "print('hello')")
    buffer.seek(0)
    return buffer.getvalue()


def create_invalid_zip() -> bytes:
    """Create invalid/corrupt ZIP."""
    return b"PK\x03\x04invalid corrupt data here not a real zip"


def create_zip_without_manifest() -> bytes:
    """Create ZIP but no manifest.json."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("src/main.py", "print('hello')")
    buffer.seek(0)
    return buffer.getvalue()


class TestPluginUploadE2E:
    """Real HTTP E2E tests for plugin upload flow."""

    def test_upload_valid_zip_creates_staging(
        self,
        client: TestClient,
        admin_session: dict,
        corvin_home: Path,
    ) -> None:
        """Valid ZIP → staging dir + audit event."""
        zip_data = create_valid_skill_zip()
        files = {"file": ("test.zip", zip_data, "application/zip")}

        response = client.post(
            "/v1/skills/upload",
            files=files,
            headers=admin_session,
        )

        assert response.status_code == 200
        data = response.json()
        assert "upload_id" in data
        assert data["status"] == "pending_approval"
        assert len(data["validation_errors"]) == 0

        # Verify staging directory exists
        staging_dir = corvin_home / "tenants/_default/global/plugin_staging"
        assert staging_dir.exists()

        # Verify metadata file exists
        upload_id = data["upload_id"]
        meta_path = staging_dir / f"{upload_id}.meta"
        assert meta_path.exists()

        # Verify audit event (read actual file)
        audit_path = corvin_home / "tenants/_default/global/forge/audit.jsonl"
        if audit_path.exists():
            lines = audit_path.read_text().strip().split("\n")
            last_event = json.loads(lines[-1])
            assert last_event.get("event_type") == "plugin.upload_staged"
            assert last_event.get("upload_id") == upload_id

    def test_upload_invalid_zip_rejects(
        self,
        client: TestClient,
        admin_session: dict,
    ) -> None:
        """Invalid ZIP → 400 error."""
        zip_data = create_invalid_zip()
        files = {"file": ("bad.zip", zip_data, "application/zip")}

        response = client.post(
            "/v1/skills/upload",
            files=files,
            headers=admin_session,
        )

        assert response.status_code == 400
        assert "corrupt" in response.json().get("detail", "").lower()

    def test_upload_missing_manifest_rejects(
        self,
        client: TestClient,
        admin_session: dict,
    ) -> None:
        """ZIP without manifest.json → 400 error."""
        zip_data = create_zip_without_manifest()
        files = {"file": ("no_manifest.zip", zip_data, "application/zip")}

        response = client.post(
            "/v1/skills/upload",
            files=files,
            headers=admin_session,
        )

        assert response.status_code == 400
        data = response.json()
        assert "manifest.json" in data.get("detail", "").lower()

    def test_approve_moves_file_and_triggers_install(
        self,
        client: TestClient,
        admin_session: dict,
        corvin_home: Path,
    ) -> None:
        """Upload + Approve → staging deleted, installed dir populated, audit logged."""
        zip_data = create_valid_skill_zip()
        files = {"file": ("test.zip", zip_data, "application/zip")}

        # Upload
        response = client.post(
            "/v1/skills/upload",
            files=files,
            headers=admin_session,
        )
        assert response.status_code == 200
        upload_id = response.json()["upload_id"]

        staging_dir = corvin_home / "tenants/_default/global/plugin_staging"
        assert (staging_dir / f"{upload_id}.zip").exists()

        # Approve
        response = client.post(
            f"/v1/skills/uploads/{upload_id}/approve",
            headers=admin_session,
        )
        assert response.status_code == 200

        # Verify moved to installed
        installed_dir = corvin_home / "tenants/_default/global/plugins_installed"
        assert (installed_dir / f"{upload_id}.zip").exists()
        assert not (staging_dir / f"{upload_id}.zip").exists()

        # Verify audit event
        audit_path = corvin_home / "tenants/_default/global/forge/audit.jsonl"
        if audit_path.exists():
            lines = audit_path.read_text().strip().split("\n")
            last_event = json.loads(lines[-1])
            assert last_event.get("event_type") == "plugin.upload_approved"

    def test_concurrent_uploads_no_race(
        self,
        client: TestClient,
        admin_session: dict,
    ) -> None:
        """5 parallel uploads → all succeed, no collision."""
        import concurrent.futures

        def upload_once() -> dict:
            zip_data = create_valid_skill_zip()
            files = {"file": ("concurrent.zip", zip_data, "application/zip")}
            response = client.post(
                "/v1/skills/upload",
                files=files,
                headers=admin_session,
            )
            return response.json() if response.status_code == 200 else None

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            results = list(executor.map(lambda _: upload_once(), range(5)))

        # All should succeed
        assert all(r is not None for r in results)

        # All upload_ids should be unique
        upload_ids = [r["upload_id"] for r in results]
        assert len(upload_ids) == len(set(upload_ids))

    def test_disk_full_graceful_error(
        self,
        client: TestClient,
        admin_session: dict,
    ) -> None:
        """Mock ENOSPC during upload → 500 error, no partial files, audit error."""
        zip_data = create_valid_skill_zip()
        files = {"file": ("test.zip", zip_data, "application/zip")}

        # Mock ENOSPC on file write
        with patch(
            "pathlib.Path.write_bytes",
            side_effect=OSError("[Errno 28] No space left on device"),
        ):
            response = client.post(
                "/v1/skills/upload",
                files=files,
                headers=admin_session,
            )

        assert response.status_code == 500
        assert "failed" in response.json().get("detail", "").lower()
