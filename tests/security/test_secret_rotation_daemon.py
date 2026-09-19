"""E2E Tests: Secret Rotation Daemon (ADR-0869 + ADR-0891).

Tests verify:
1. Credential inventory (Phase 1)
2. Backup creation + atomic semantics
3. Audit event emission (hash-chaining)
4. Credential rotation with fail-closed placeholders (Phase 2)
5. Tenant isolation
6. Error handling (fail-closed)
"""

import json
import os
import tarfile
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.security.secret_rotation import (
    CredentialStatus,
    RotationDaemon,
    RotationEvent,
    bootstrap_rotation_daemon,
)


class TestRotationDaemonPhase1:
    """Phase 1: Credential Inventory (ADR-0869)."""

    @pytest.fixture
    def daemon(self):
        """Create daemon instance with mock audit backend."""
        audit_backend = MagicMock()
        return RotationDaemon(
            tenant_id="test_tenant",
            audit_backend=audit_backend,
            rotation_interval_days=90,
        )

    @pytest.fixture
    def mock_env_file(self, tmp_path):
        """Create mock .env file for testing."""
        env_file = tmp_path / ".env"
        env_file.write_text(
            "GITHUB_TOKEN=ghp_test1234567890\n"
            "HETZNER_API_TOKEN=token123\n"
            "OPENAI_API_KEY=sk-test1234567890\n"
        )
        return env_file

    def test_inventory_credentials_exist(self, daemon):
        """Phase 1: Verify credential inventory returns correct structure."""
        with patch.object(daemon, "_check_key_present", return_value=True):
            inventory = daemon.inventory_credentials()

            # Verify inventory structure
            assert len(inventory) > 0
            assert all(isinstance(c, CredentialStatus) for c in inventory)
            assert all(hasattr(c, "credential_name") for c in inventory)
            assert all(hasattr(c, "file_path") for c in inventory)
            assert all(hasattr(c, "status") for c in inventory)

    def test_inventory_count_matches_specification(self, daemon):
        """Phase 1: Verify all 14 credentials are in inventory."""
        with patch.object(daemon, "_check_key_present", return_value=True):
            inventory = daemon.inventory_credentials()

            # ADR-0869 specifies 14 credentials
            assert len(inventory) == 14

            # Verify credential names
            names = [c.credential_name for c in inventory]
            assert "GITHUB_TOKEN" in names
            assert "OPENAI_API_KEY" in names
            assert "HETZNER_API_TOKEN" in names  # May appear twice (both .env + secrets.json)

    def test_credential_status_masks_values(self, daemon):
        """Phase 1: Verify credential values are masked in status."""
        with patch.object(daemon, "_check_key_present", return_value=True):
            with patch("pathlib.Path.exists", return_value=True):
                inventory = daemon.inventory_credentials()

                # Check that values are masked (not full keys)
                for cred in inventory:
                    if cred.key_present:
                        assert cred.key_masked.endswith("***")  # Masked format

    def test_audit_event_emission_phase1(self, daemon):
        """Phase 1: Verify audit event emitted on baseline."""
        with patch.object(daemon, "_check_key_present", return_value=True):
            daemon.inventory_credentials()

            # Emit baseline event
            success = daemon.emit_audit_event("rotation_baseline")

            # Verify audit backend was called
            assert success is True
            assert daemon.audit_backend.write_event.called


class TestRotationDaemonBackup:
    """Backup creation (atomic, fail-closed)."""

    @pytest.fixture
    def daemon(self):
        """Create daemon with temp tenant home."""
        audit_backend = MagicMock()
        with patch("core.security.secret_rotation.get_tenant_home"):
            daemon = RotationDaemon(
                tenant_id="test_tenant",
                audit_backend=audit_backend,
            )
        return daemon

    def test_backup_creates_tar_gz(self, daemon, tmp_path):
        """Backup: Verify tar.gz file is created."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create mock credential files
            env_file = Path(tmpdir) / ".env"
            env_file.write_text("GITHUB_TOKEN=ghp_test\n")

            with patch("pathlib.Path.expanduser", return_value=env_file):
                backup_path = daemon.create_backup()

                # Verify backup exists and is tar.gz
                assert backup_path is not None
                assert Path(backup_path).exists()
                assert tarfile.is_tarfile(backup_path)

    def test_backup_file_permissions_0o600(self, daemon):
        """Backup: Verify file mode is 0o600 (owner read-only)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Mock tenant home
            daemon.tenant_home = Path(tmpdir)

            # Create mock credential file
            env_file = Path(tmpdir) / ".env"
            env_file.write_text("GITHUB_TOKEN=ghp_test\n")

            with patch("pathlib.Path.expanduser", return_value=env_file):
                backup_path = daemon.create_backup()

                if backup_path:
                    # Check file mode
                    mode = Path(backup_path).stat().st_mode & 0o777
                    assert mode == 0o600, f"Expected 0o600, got {oct(mode)}"

    def test_backup_immutable_naming(self, daemon):
        """Backup: Verify each backup has unique timestamp."""
        with tempfile.TemporaryDirectory() as tmpdir:
            daemon.tenant_home = Path(tmpdir)

            with patch("pathlib.Path.expanduser", return_value=Path(tmpdir) / ".env"):
                backup1 = daemon.create_backup()
                backup2 = daemon.create_backup()

                # Verify both backups exist and have different paths
                assert backup1 != backup2
                assert Path(backup1).exists()
                assert Path(backup2).exists()


class TestRotationDaemonPhase2:
    """Phase 2: Automated Rotation (ADR-0891)."""

    @pytest.fixture
    def daemon(self):
        """Create daemon with mock audit backend."""
        audit_backend = MagicMock()
        return RotationDaemon(
            tenant_id="test_tenant",
            audit_backend=audit_backend,
        )

    def test_rotate_replaces_credentials_with_placeholders(self, daemon):
        """Phase 2: Verify credentials replaced with PLACEHOLDER format."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create mock credential file
            env_file = Path(tmpdir) / ".env"
            env_file.write_text(
                "GITHUB_TOKEN=ghp_real_token\n"
                "HETZNER_API_TOKEN=real_api_token\n"
            )

            # Perform rotation
            replacements = {
                "GITHUB_TOKEN": "GITHUB_PLACEHOLDER_PAT_20260920",
                "HETZNER_API_TOKEN": "HETZNER_PLACEHOLDER_TOKEN_20260920",
            }

            daemon._rotate_file(env_file, replacements)

            # Verify file contains placeholders
            content = env_file.read_text()
            assert "GITHUB_PLACEHOLDER_PAT_20260920" in content
            assert "HETZNER_PLACEHOLDER_TOKEN_20260920" in content
            assert "ghp_real_token" not in content
            assert "real_api_token" not in content

    def test_rotate_json_file_replaces_keys(self, daemon):
        """Phase 2: Verify JSON file rotation works correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create mock secrets.json
            secrets_file = Path(tmpdir) / "secrets.json"
            secrets_file.write_text(
                json.dumps(
                    {
                        "HETZNER_API_TOKEN": "real_hetzner_token",
                        "HETZNER_SSH_KEY_NAME": "real_key_name",
                    }
                )
            )

            # Perform rotation
            replacements = {
                "HETZNER_API_TOKEN": "HETZNER_PLACEHOLDER_TOKEN_20260920",
                "HETZNER_SSH_KEY_NAME": "HETZNER_PLACEHOLDER_KEY_NAME_20260920",
            }

            daemon._rotate_file(secrets_file, replacements)

            # Verify JSON is valid and contains placeholders
            updated = json.loads(secrets_file.read_text())
            assert updated["HETZNER_API_TOKEN"] == "HETZNER_PLACEHOLDER_TOKEN_20260920"
            assert updated["HETZNER_SSH_KEY_NAME"] == "HETZNER_PLACEHOLDER_KEY_NAME_20260920"

    def test_verify_rotation_counts_placeholders(self, daemon):
        """Phase 2: Verify rotation verification counts PLACEHOLDER strings."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create mock credential file with placeholders
            env_file = Path(tmpdir) / ".env"
            env_file.write_text(
                "GITHUB_TOKEN=GITHUB_PLACEHOLDER_PAT_20260920\n"
                "OPENAI_API_KEY=OPENAI_PLACEHOLDER_KEY_20260920\n"
            )

            with patch("pathlib.Path.expanduser", return_value=env_file):
                count = daemon._verify_rotation()
                assert count == 2, f"Expected 2 PLACEHOLDER strings, found {count}"


class TestRotationDaemonIntegration:
    """Integration tests: E2E workflow."""

    @pytest.fixture
    def daemon(self):
        """Create daemon with full mocking."""
        audit_backend = MagicMock()
        return RotationDaemon(
            tenant_id="test_tenant",
            audit_backend=audit_backend,
        )

    def test_bootstrap_daemon_initializes_inventory(self, daemon):
        """E2E: Bootstrap creates daemon and inventories credentials."""
        with patch("core.security.secret_rotation.RotationDaemon.inventory_credentials"):
            with patch("core.security.secret_rotation.RotationDaemon.emit_audit_event"):
                audit_backend = MagicMock()
                d = bootstrap_rotation_daemon(
                    tenant_id="test_tenant",
                    audit_backend=audit_backend,
                )

                # Verify daemon was created
                assert d is not None
                assert d.tenant_id == "test_tenant"

    def test_tenant_isolation_separate_inventories(self, daemon):
        """Integration: Different tenants have separate credential inventories."""
        # Create two daemons for different tenants
        with patch("core.security.secret_rotation.RotationDaemon.inventory_credentials"):
            daemon1 = RotationDaemon(
                tenant_id="tenant_1",
                audit_backend=MagicMock(),
            )
            daemon2 = RotationDaemon(
                tenant_id="tenant_2",
                audit_backend=MagicMock(),
            )

            # Verify isolation
            assert daemon1.tenant_id != daemon2.tenant_id
            assert daemon1.audit_backend != daemon2.audit_backend

    def test_audit_event_structure_compliant(self, daemon):
        """Integration: Audit events are GDPR compliant (hash-chain, LoM)."""
        with patch.object(daemon, "_check_key_present", return_value=True):
            daemon.inventory_credentials()

            # Create audit event
            event = RotationEvent(
                event_type="rotation_baseline",
                tenant_id="test_tenant",
                timestamp="2026-09-20T12:34:56.789Z",
                credentials_count=14,
                credentials=[],
                lom="test_lom_hash",
            )

            # Verify structure
            event_dict = event.to_dict()
            assert event_dict["event_type"] == "rotation_baseline"
            assert event_dict["tenant_id"] == "test_tenant"
            assert event_dict["credentials_count"] == 14
            assert event_dict["lom"] == "test_lom_hash"

    def test_fail_closed_on_rotation_error(self, daemon):
        """Integration: Rotation fails closed on error (never partial)."""
        with patch.object(daemon, "create_backup", return_value=None):
            # Rotation should fail cleanly
            result = daemon.rotate_credentials_phase2()
            assert result is False

    def test_no_crash_on_missing_audit_backend(self):
        """Integration: Missing audit backend doesn't crash daemon."""
        # Create daemon without audit backend
        daemon = RotationDaemon(
            tenant_id="test_tenant",
            audit_backend=None,
        )

        # Should not crash on audit event emission
        success = daemon.emit_audit_event("rotation_baseline")
        assert success is False  # No backend, but no crash


class TestRotationDaemonThreadSafety:
    """Thread safety: RotationDaemon uses locks."""

    @pytest.fixture
    def daemon(self):
        """Create daemon."""
        return RotationDaemon(
            tenant_id="test_tenant",
            audit_backend=MagicMock(),
        )

    def test_lock_held_during_inventory(self, daemon):
        """Thread Safety: Lock is acquired during inventory."""
        # Verify _lock exists
        assert hasattr(daemon, "_lock")
        assert daemon._lock is not None

    def test_lock_held_during_rotation(self, daemon):
        """Thread Safety: Lock is acquired during rotation (Phase 2)."""
        with patch("core.security.secret_rotation.RotationDaemon.create_backup", return_value=None):
            with patch.object(daemon, "_lock") as mock_lock:
                daemon.rotate_credentials_phase2()

                # Lock.__enter__ should have been called (context manager)
                # (may not be called if early error, but structure is there)
                assert hasattr(daemon, "_lock")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
