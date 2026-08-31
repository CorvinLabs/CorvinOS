"""Tests for tenant export/import commands."""

import json
import tarfile
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

# Import the tenant_cmd module
import sys
from pathlib import Path as PathlibPath

CORVIN_ROOT = PathlibPath(__file__).resolve().parents[3].parent.parent
if str(CORVIN_ROOT / "ops" / "launcher") not in sys.path:
    sys.path.insert(0, str(CORVIN_ROOT / "ops" / "launcher"))

from corvin import tenant_cmd


@pytest.fixture
def temp_corvin_home(tmp_path):
    """Create a temporary CORVIN_HOME with a test tenant."""
    import os
    corvin_home = tmp_path / ".corvin"
    corvin_home.mkdir()
    os.environ["CORVIN_HOME"] = str(corvin_home)

    # Create tenant structure
    tenant_dir = corvin_home / "tenants" / "_default"
    (tenant_dir / "global").mkdir(parents=True)
    (tenant_dir / "voice").mkdir(parents=True)
    (tenant_dir / "sessions").mkdir(parents=True)
    (tenant_dir / "plugins").mkdir(parents=True)
    (tenant_dir / "datasource_connections").mkdir(parents=True)

    # Add some test files
    (tenant_dir / "global" / "tenant.corvin.yaml").write_text("spec:\n  features: {}\n")
    (tenant_dir / "global" / "config.json").write_text('{"test": true}')
    (tenant_dir / "voice" / "profile.json").write_text('{"lang": "en"}')
    (tenant_dir / "plugins" / "manifest.json").write_text('{"plugins": []}')

    # Create a test session
    (tenant_dir / "sessions" / "test_session").mkdir()
    (tenant_dir / "sessions" / "test_session" / "metadata.json").write_text('{}')

    yield corvin_home

    # Cleanup
    if "CORVIN_HOME" in os.environ:
        del os.environ["CORVIN_HOME"]


class TestTenantExport:
    """Tests for tenant export command."""

    def test_export_creates_valid_bundle(self, temp_corvin_home):
        """Export creates a valid tar.gz bundle."""
        output_path = temp_corvin_home.parent / "export.tar.gz"

        class Args:
            tenant_id = "_default"
            output = str(output_path)
            with_secrets = False
            with_compute_runs = False
            exclude_old_sessions = None

        result = tenant_cmd.cmd_export(Args())
        assert result == 0
        assert output_path.exists()

        # Verify it's a valid tar.gz
        with tarfile.open(output_path, "r:gz") as tar:
            members = tar.getnames()
            assert any("metadata.json" in m for m in members)
            assert any("global" in m for m in members)
            assert any("voice" in m for m in members)

    def test_export_excludes_secrets_by_default(self, temp_corvin_home):
        """Export without --with-secrets omits secrets.enc."""
        tenant_dir = temp_corvin_home / "tenants" / "_default"
        (tenant_dir / "global" / "secrets.enc").write_text("encrypted_data")

        output_path = temp_corvin_home.parent / "export.tar.gz"

        class Args:
            tenant_id = "_default"
            output = str(output_path)
            with_secrets = False
            with_compute_runs = False
            exclude_old_sessions = None

        result = tenant_cmd.cmd_export(Args())
        assert result == 0

        with tarfile.open(output_path, "r:gz") as tar:
            members = tar.getnames()
            assert not any("secrets.enc" in m for m in members)

    def test_export_includes_secrets_when_requested(self, temp_corvin_home):
        """Export with --with-secrets includes secrets.enc."""
        tenant_dir = temp_corvin_home / "tenants" / "_default"
        (tenant_dir / "global" / "secrets.enc").write_text("encrypted_data")

        output_path = temp_corvin_home.parent / "export.tar.gz"

        class Args:
            tenant_id = "_default"
            output = str(output_path)
            with_secrets = True
            with_compute_runs = False
            exclude_old_sessions = None

        result = tenant_cmd.cmd_export(Args())
        assert result == 0

        with tarfile.open(output_path, "r:gz") as tar:
            members = tar.getnames()
            assert any("secrets.enc" in m for m in members)

    def test_export_creates_valid_metadata(self, temp_corvin_home):
        """Export creates valid metadata.json."""
        output_path = temp_corvin_home.parent / "export.tar.gz"

        class Args:
            tenant_id = "_default"
            output = str(output_path)
            with_secrets = False
            with_compute_runs = False
            exclude_old_sessions = None

        result = tenant_cmd.cmd_export(Args())
        assert result == 0

        with tarfile.open(output_path, "r:gz") as tar:
            metadata_member = None
            for member in tar.getmembers():
                if member.name.endswith("metadata.json"):
                    metadata_member = member
                    break

            assert metadata_member is not None
            f = tar.extractfile(metadata_member)
            metadata = json.loads(f.read().decode())

            assert metadata["version"] == "1.0"
            assert metadata["tenant_id"] == "_default"
            assert "created_at" in metadata
            assert "corvin_version" in metadata
            assert metadata["includes"]["tenant_config"] is True
            assert metadata["includes"]["sessions"] is True

    def test_export_nonexistent_tenant_fails(self, temp_corvin_home):
        """Export of nonexistent tenant returns error."""
        output_path = temp_corvin_home.parent / "export.tar.gz"

        class Args:
            tenant_id = "nonexistent"
            output = str(output_path)
            with_secrets = False
            with_compute_runs = False
            exclude_old_sessions = None

        result = tenant_cmd.cmd_export(Args())
        assert result == 1
        assert not output_path.exists()

    def test_export_to_existing_output_fails(self, temp_corvin_home):
        """Export to existing output file returns error."""
        output_path = temp_corvin_home.parent / "export.tar.gz"
        output_path.write_text("existing")

        class Args:
            tenant_id = "_default"
            output = str(output_path)
            with_secrets = False
            with_compute_runs = False
            exclude_old_sessions = None

        result = tenant_cmd.cmd_export(Args())
        assert result == 1


class TestTenantImport:
    """Tests for tenant import command."""

    def test_import_restores_tenant(self, temp_corvin_home):
        """Import restores full tenant from bundle."""
        # First export
        export_path = temp_corvin_home.parent / "export.tar.gz"

        class ExportArgs:
            tenant_id = "_default"
            output = str(export_path)
            with_secrets = False
            with_compute_runs = False
            exclude_old_sessions = None

        result = tenant_cmd.cmd_export(ExportArgs())
        assert result == 0

        # Delete the tenant
        import shutil
        tenant_dir = temp_corvin_home / "tenants" / "_default"
        shutil.rmtree(tenant_dir)

        # Import
        class ImportArgs:
            bundle_path = str(export_path)
            tenant_id = "_default"
            force_overwrite = False
            decrypt_secrets = False

        result = tenant_cmd.cmd_import(ImportArgs())
        assert result == 0

        # Verify tenant was restored
        assert tenant_dir.exists()
        assert (tenant_dir / "global" / "tenant.corvin.yaml").exists()
        assert (tenant_dir / "voice" / "profile.json").exists()
        assert (tenant_dir / "plugins" / "manifest.json").exists()

    def test_import_to_existing_tenant_fails(self, temp_corvin_home):
        """Import to existing tenant without force returns error."""
        # Export first
        export_path = temp_corvin_home.parent / "export.tar.gz"

        class ExportArgs:
            tenant_id = "_default"
            output = str(export_path)
            with_secrets = False
            with_compute_runs = False
            exclude_old_sessions = None

        tenant_cmd.cmd_export(ExportArgs())

        # Try to import without force
        class ImportArgs:
            bundle_path = str(export_path)
            tenant_id = "_default"
            force_overwrite = False
            decrypt_secrets = False

        result = tenant_cmd.cmd_import(ImportArgs())
        assert result == 1

    def test_import_with_force_overwrites(self, temp_corvin_home):
        """Import with --force-overwrite replaces tenant."""
        # Export
        export_path = temp_corvin_home.parent / "export.tar.gz"

        class ExportArgs:
            tenant_id = "_default"
            output = str(export_path)
            with_secrets = False
            with_compute_runs = False
            exclude_old_sessions = None

        tenant_cmd.cmd_export(ExportArgs())

        # Modify a file to verify overwrite
        tenant_dir = temp_corvin_home / "tenants" / "_default"
        (tenant_dir / "global" / "config.json").write_text('{"modified": true}')

        # Import with force
        class ImportArgs:
            bundle_path = str(export_path)
            tenant_id = "_default"
            force_overwrite = True
            decrypt_secrets = False

        result = tenant_cmd.cmd_import(ImportArgs())
        assert result == 0

        # Verify file was overwritten
        config = json.loads((tenant_dir / "global" / "config.json").read_text())
        assert config.get("test") is True
        assert "modified" not in config

    def test_import_validates_bundle_format(self, temp_corvin_home):
        """Import validates bundle format."""
        # Create a fake tar.gz without proper structure
        bad_bundle = temp_corvin_home.parent / "bad.tar.gz"
        with tarfile.open(bad_bundle, "w:gz") as tar:
            pass  # Empty tar

        class ImportArgs:
            bundle_path = str(bad_bundle)
            tenant_id = "_default"
            force_overwrite = True
            decrypt_secrets = False

        result = tenant_cmd.cmd_import(ImportArgs())
        assert result == 1

    def test_import_missing_bundle_fails(self, temp_corvin_home):
        """Import of missing bundle returns error."""
        missing_bundle = temp_corvin_home.parent / "missing.tar.gz"

        class ImportArgs:
            bundle_path = str(missing_bundle)
            tenant_id = "_default"
            force_overwrite = False
            decrypt_secrets = False

        result = tenant_cmd.cmd_import(ImportArgs())
        assert result == 1


class TestTenantList:
    """Tests for tenant list command."""

    def test_list_shows_all_tenants(self, temp_corvin_home, capsys):
        """List shows all available tenants."""
        # Create a second tenant
        (temp_corvin_home / "tenants" / "prod").mkdir(parents=True)
        (temp_corvin_home / "tenants" / "prod" / "global").mkdir()

        class Args:
            pass

        result = tenant_cmd.cmd_list(Args())
        assert result == 0

        captured = capsys.readouterr()
        assert "_default" in captured.out
        assert "prod" in captured.out


class TestTenantInfo:
    """Tests for tenant info command."""

    def test_info_shows_tenant_details(self, temp_corvin_home, capsys):
        """Info shows tenant configuration and statistics."""
        class Args:
            tenant_id = "_default"

        result = tenant_cmd.cmd_info(Args())
        assert result == 0

        captured = capsys.readouterr()
        assert "_default" in captured.out
        assert "Configuration present" in captured.out
        assert "Sessions" in captured.out
        assert "Voice configuration" in captured.out

    def test_info_nonexistent_tenant_fails(self, temp_corvin_home):
        """Info for nonexistent tenant returns error."""
        class Args:
            tenant_id = "nonexistent"

        result = tenant_cmd.cmd_info(Args())
        assert result == 1


class TestValidation:
    """Tests for validation functions."""

    def test_validate_tenant_id_accepts_valid_ids(self):
        """_validate_tenant_id accepts valid tenant IDs."""
        assert tenant_cmd._validate_tenant_id("_default") == "_default"
        assert tenant_cmd._validate_tenant_id("prod") == "prod"
        assert tenant_cmd._validate_tenant_id("my-tenant-1") == "my-tenant-1"
        assert tenant_cmd._validate_tenant_id("test_2024") == "test_2024"

    def test_validate_tenant_id_rejects_invalid_ids(self):
        """_validate_tenant_id rejects invalid tenant IDs."""
        with pytest.raises(ValueError):
            tenant_cmd._validate_tenant_id("__reserved")

        with pytest.raises(ValueError):
            tenant_cmd._validate_tenant_id("Invalid-Case")

        with pytest.raises(ValueError):
            tenant_cmd._validate_tenant_id("")

        with pytest.raises(ValueError):
            tenant_cmd._validate_tenant_id("@invalid")
