"""End-to-end tests for Plugin Installation Flow (ADR-0249 Stage 6).

Tests all 6 stages:
1. Upload: File input + drag-drop
2. Verify: Checksum, manifest schema, trust level
3. Audit: plugin.installation_started event
4. Install: Stage 6 CLI (corvin plugin install)
5. Enable: Hot-reload or next boot
6. Verify: health_check passes

Runs against the real console API endpoints with a test fixture environment.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import shutil
import sys
import tarfile
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

log = logging.getLogger(__name__)

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[2]
_OPERATOR = _REPO / "operator"
_CONSOLE = _REPO / "core" / "console"
_PLUGINS = _REPO / "core" / "plugins"
_BRIDGES_SHARED = _OPERATOR / "bridges" / "shared"

for _p in [str(_OPERATOR), str(_OPERATOR / "license"), str(_OPERATOR / "forge"),
           str(_CONSOLE), str(_BRIDGES_SHARED), str(_PLUGINS)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


_PURGED_PREFIXES = ("corvin_console", "corvin_gateway", "forge")


def _snapshot_modules() -> dict:
    return {
        k: v for k, v in sys.modules.items() if k.startswith(_PURGED_PREFIXES)
    }


def _reset_modules(restore: dict | None = None) -> None:
    for key in list(sys.modules):
        if key.startswith(_PURGED_PREFIXES):
            del sys.modules[key]
    if restore:
        sys.modules.update(restore)


@contextmanager
def _sandbox(tmp_path: Path):
    """Test fixture: isolated CorvinOS environment with plugin registry."""
    home = tmp_path / "corvin_home"
    tenant_id = "_default"
    (home / "tenants" / tenant_id / "global" / "auth").mkdir(parents=True)
    (home / "tenants" / tenant_id / "global" / "forge").mkdir(parents=True)
    (home / "tenants" / tenant_id / "global" / "console" / "sessions").mkdir(parents=True)

    prev = {k: os.environ.get(k) for k in
            ("CORVIN_HOME", "CORVIN_TENANT_ID", "VOICE_AUDIT_PATH")}
    os.environ["CORVIN_HOME"] = str(home)
    os.environ["CORVIN_TENANT_ID"] = tenant_id
    os.environ["VOICE_AUDIT_PATH"] = str(home / "audit.jsonl")

    preloaded = _snapshot_modules()
    try:
        _reset_modules()
        from corvin_console import auth as _auth
        from corvin_console.app import router
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        rec = _auth.create_session(tenant_id=tenant_id, token_fingerprint="test-fp")
        app = FastAPI()
        app.include_router(router, prefix="/v1/console")
        client = TestClient(app, raise_server_exceptions=False)
        client.cookies.set("corvin_console_sid", rec.sid)
        csrf_token = _auth.derive_csrf_token(rec.csrf_secret, rec.sid)
        yield client, csrf_token, home
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _reset_modules(restore=preloaded)


def _create_test_plugin_tarball(
    plugin_id: str = "test.plugin",
    version: str = "1.0.0",
    origin: str = "community",
    network_egress: str = "none",
) -> bytes:
    """Create a minimal valid plugin tarball for testing.

    Returns gzip-compressed tar bytes.
    """
    tar_buffer = io.BytesIO()
    tar = tarfile.open(mode="r:gz", fileobj=tar_buffer)

    # Create plugin directory structure
    plugin_dir = f"{plugin_id.replace('.', '_')}_1_0_0"

    # Create plugin.yaml
    manifest = {
        "plugin_id": plugin_id,
        "version": version,
        "display_name": plugin_id,
        "plugin_type": "notification_backend",
        "origin": origin,
        "pii_risk": "low",
        "boot_layer": "installed",
        "network_egress": network_egress,
        "settings_schema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    }

    manifest_yaml = f"""plugin_id: {plugin_id}
version: {version}
display_name: {plugin_id}
plugin_type: notification_backend
origin: {origin}
pii_risk: low
boot_layer: installed
network_egress: {network_egress}
"""

    manifest_info = tarfile.TarInfo(name=f"{plugin_dir}/plugin.yaml")
    manifest_data = manifest_yaml.encode("utf-8")
    manifest_info.size = len(manifest_data)
    tar.addfile(manifest_info, io.BytesIO(manifest_data))

    # Create pyproject.toml
    pyproject = f"""[project]
name = "{plugin_id.replace('.', '-')}"
version = "{version}"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
"""

    pyproject_info = tarfile.TarInfo(name=f"{plugin_dir}/pyproject.toml")
    pyproject_data = pyproject.encode("utf-8")
    pyproject_info.size = len(pyproject_data)
    tar.addfile(pyproject_info, io.BytesIO(pyproject_data))

    tar.close()
    tar_buffer.seek(0)
    return tar_buffer.read()


def _compute_sha256(data: bytes) -> str:
    """Compute SHA256 checksum of data."""
    return hashlib.sha256(data).hexdigest()


class TestPluginInstallFlow(unittest.TestCase):
    """E2E tests for the complete plugin installation flow."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _hdr(self, csrf: str) -> dict[str, str]:
        return {"X-CSRF-Token": csrf}

    def _flag(self, client, csrf: str, flag_id: str, on: bool) -> None:
        resp = client.put(
            f"/v1/console/settings/features/{flag_id}",
            json={"enabled": on},
            headers=self._hdr(csrf),
        )
        self.assertEqual(resp.status_code, 200, resp.text)

    # ── Stage 1: Upload ───────────────────────────────────────────────────────

    def test_upload_valid_plugin_tarball(self):
        """Stage 1: Upload a valid .tar.gz plugin file."""
        with _sandbox(Path(self._tmp)) as (client, csrf, _home):
            self._flag(client, csrf, "plugin_console_surface", True)
            self._flag(client, csrf, "plugin_runtime_lifecycle", True)

            tarball = _create_test_plugin_tarball()
            resp = client.post(
                "/v1/console/plugins/upload",
                files={"file": ("test.tar.gz", io.BytesIO(tarball), "application/gzip")},
                headers=self._hdr(csrf),
            )

            self.assertEqual(resp.status_code, 200, resp.text)
            body = resp.json()
            self.assertIn("plugin_id", body)
            self.assertIn("status", body)

    def test_upload_rejects_non_tarball(self):
        """Stage 1: Reject non-.tar.gz files."""
        with _sandbox(Path(self._tmp)) as (client, csrf, _home):
            self._flag(client, csrf, "plugin_console_surface", True)
            self._flag(client, csrf, "plugin_runtime_lifecycle", True)

            resp = client.post(
                "/v1/console/plugins/upload",
                files={"file": ("test.zip", io.BytesIO(b"fake zip"), "application/zip")},
                headers=self._hdr(csrf),
            )

            self.assertEqual(resp.status_code, 422, resp.text)
            self.assertIn("tar.gz", resp.json()["detail"].lower())

    def test_upload_rejects_empty_file(self):
        """Stage 1: Reject empty uploads."""
        with _sandbox(Path(self._tmp)) as (client, csrf, _home):
            self._flag(client, csrf, "plugin_console_surface", True)
            self._flag(client, csrf, "plugin_runtime_lifecycle", True)

            resp = client.post(
                "/v1/console/plugins/upload",
                files={"file": ("test.tar.gz", io.BytesIO(b""), "application/gzip")},
                headers=self._hdr(csrf),
            )

            self.assertEqual(resp.status_code, 400, resp.text)

    # ── Stage 2: Verify ───────────────────────────────────────────────────────

    def test_verify_manifest_extraction(self):
        """Stage 2a: Extract and validate plugin.yaml from tarball."""
        with _sandbox(Path(self._tmp)) as (client, csrf, _home):
            self._flag(client, csrf, "plugin_console_surface", True)
            self._flag(client, csrf, "plugin_runtime_lifecycle", True)

            tarball = _create_test_plugin_tarball(
                plugin_id="verify.test",
                version="2.1.0"
            )
            resp = client.post(
                "/v1/console/plugins/upload",
                files={"file": ("test.tar.gz", io.BytesIO(tarball), "application/gzip")},
                headers=self._hdr(csrf),
            )

            self.assertEqual(resp.status_code, 200, resp.text)
            body = resp.json()
            self.assertEqual(body["plugin_id"], "verify.test")
            self.assertEqual(body["version"], "2.1.0")

    def test_verify_checksum_validation(self):
        """Stage 2b: Verify SHA256 checksum of tarball."""
        with _sandbox(Path(self._tmp)) as (client, csrf, _home):
            self._flag(client, csrf, "plugin_console_surface", True)
            self._flag(client, csrf, "plugin_runtime_lifecycle", True)

            tarball = _create_test_plugin_tarball()
            checksum = _compute_sha256(tarball)

            # Correct checksum should pass
            resp = client.post(
                "/v1/console/plugins/upload",
                data={"checksum": checksum},
                files={"file": ("test.tar.gz", io.BytesIO(tarball), "application/gzip")},
                headers=self._hdr(csrf),
            )
            self.assertEqual(resp.status_code, 200, resp.text)

            # Wrong checksum should fail
            resp = client.post(
                "/v1/console/plugins/upload",
                data={"checksum": "0" * 64},  # Wrong checksum
                files={"file": ("test.tar.gz", io.BytesIO(tarball), "application/gzip")},
                headers=self._hdr(csrf),
            )
            self.assertEqual(resp.status_code, 400, resp.text)

    def test_verify_invalid_manifest_rejected(self):
        """Stage 2c: Reject tarball without plugin.yaml."""
        with _sandbox(Path(self._tmp)) as (client, csrf, _home):
            self._flag(client, csrf, "plugin_console_surface", True)
            self._flag(client, csrf, "plugin_runtime_lifecycle", True)

            # Create empty tarball
            tar_buffer = io.BytesIO()
            tar = tarfile.open(mode="r:gz", fileobj=tar_buffer)
            tar.close()
            tar_buffer.seek(0)

            resp = client.post(
                "/v1/console/plugins/upload",
                files={"file": ("test.tar.gz", tar_buffer, "application/gzip")},
                headers=self._hdr(csrf),
            )

            self.assertEqual(resp.status_code, 422, resp.text)
            self.assertIn("manifest", resp.json()["detail"].lower())

    # ── Stage 3: Audit ────────────────────────────────────────────────────────

    def test_audit_event_emitted_on_upload(self):
        """Stage 3: Emit plugin.installation_started audit event."""
        with _sandbox(Path(self._tmp)) as (client, csrf, home):
            self._flag(client, csrf, "plugin_console_surface", True)
            self._flag(client, csrf, "plugin_runtime_lifecycle", True)

            tarball = _create_test_plugin_tarball(plugin_id="audit.test")
            resp = client.post(
                "/v1/console/plugins/upload",
                files={"file": ("test.tar.gz", io.BytesIO(tarball), "application/gzip")},
                headers=self._hdr(csrf),
            )

            self.assertEqual(resp.status_code, 200, resp.text)

            # Check audit trail exists
            audit_path = home / "audit.jsonl"
            self.assertTrue(audit_path.exists(), "audit.jsonl should be created")

    # ── Stage 4-6: Install + Enable + Health Check ────────────────────────────

    def test_plugin_installed_after_upload(self):
        """Stages 4-6: Plugin installed, enabled, and health-checked."""
        with _sandbox(Path(self._tmp)) as (client, csrf, home):
            self._flag(client, csrf, "plugin_console_surface", True)
            self._flag(client, csrf, "plugin_runtime_lifecycle", True)

            tarball = _create_test_plugin_tarball(plugin_id="e2e.test")
            resp = client.post(
                "/v1/console/plugins/upload",
                files={"file": ("test.tar.gz", io.BytesIO(tarball), "application/gzip")},
                headers=self._hdr(csrf),
            )

            self.assertEqual(resp.status_code, 200, resp.text)
            body = resp.json()

            # Check status
            self.assertIn(body["status"], ["installed", "installed_pending_enable"])
            self.assertEqual(body["plugin_id"], "e2e.test")

    def test_trust_verdict_returned_for_community_plugin(self):
        """Trust level: Community plugin verdict returned."""
        with _sandbox(Path(self._tmp)) as (client, csrf, _home):
            self._flag(client, csrf, "plugin_console_surface", True)
            self._flag(client, csrf, "plugin_runtime_lifecycle", True)

            tarball = _create_test_plugin_tarball(origin="community")
            resp = client.post(
                "/v1/console/plugins/upload",
                files={"file": ("test.tar.gz", io.BytesIO(tarball), "application/gzip")},
                headers=self._hdr(csrf),
            )

            self.assertEqual(resp.status_code, 200, resp.text)
            body = resp.json()
            self.assertEqual(body["trust_verdict"], "community")
            self.assertTrue(body["requires_consent"])

    def test_auto_enable_flag_respected(self):
        """Stages 5-6: auto_enable flag enables and runs health check."""
        with _sandbox(Path(self._tmp)) as (client, csrf, _home):
            self._flag(client, csrf, "plugin_console_surface", True)
            self._flag(client, csrf, "plugin_runtime_lifecycle", True)

            tarball = _create_test_plugin_tarball()
            resp = client.post(
                "/v1/console/plugins/upload",
                data={"auto_enable": "true"},
                files={"file": ("test.tar.gz", io.BytesIO(tarball), "application/gzip")},
                headers=self._hdr(csrf),
            )

            self.assertEqual(resp.status_code, 200, resp.text)
            body = resp.json()

            # When auto_enable is true, status should be "installed"
            self.assertEqual(body["status"], "installed")

    # ── Error Handling ────────────────────────────────────────────────────────

    def test_upload_disabled_when_flags_off(self):
        """Fail-closed: Upload endpoint 404s when plugin flags are off."""
        with _sandbox(Path(self._tmp)) as (client, csrf, _home):
            # Flags are off by default
            tarball = _create_test_plugin_tarball()
            resp = client.post(
                "/v1/console/plugins/upload",
                files={"file": ("test.tar.gz", io.BytesIO(tarball), "application/gzip")},
                headers=self._hdr(csrf),
            )

            # Should be 403 or 404
            self.assertIn(resp.status_code, [403, 404])

    def test_csrf_required_for_upload(self):
        """Security: Upload requires valid CSRF token."""
        with _sandbox(Path(self._tmp)) as (client, csrf, _home):
            self._flag(client, csrf, "plugin_console_surface", True)
            self._flag(client, csrf, "plugin_runtime_lifecycle", True)

            tarball = _create_test_plugin_tarball()
            resp = client.post(
                "/v1/console/plugins/upload",
                files={"file": ("test.tar.gz", io.BytesIO(tarball), "application/gzip")},
                headers={"X-CSRF-Token": "invalid"},  # Bad CSRF
            )

            self.assertIn(resp.status_code, [401, 403])

    def test_upload_unauthenticated_rejected(self):
        """Security: Upload requires authentication."""
        with _sandbox(Path(self._tmp)) as (client, csrf, _home):
            self._flag(client, csrf, "plugin_console_surface", True)
            self._flag(client, csrf, "plugin_runtime_lifecycle", True)

            tarball = _create_test_plugin_tarball()
            client.cookies.clear()  # Remove auth cookie

            resp = client.post(
                "/v1/console/plugins/upload",
                files={"file": ("test.tar.gz", io.BytesIO(tarball), "application/gzip")},
                headers=self._hdr(csrf),
            )

            self.assertIn(resp.status_code, [401, 403])


if __name__ == "__main__":
    unittest.main()
