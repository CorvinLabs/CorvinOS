"""Tests for workflows console → plugin migration (zero-data-loss).

ADR-0039 Phase 6: Migration infrastructure.
"""

import asyncio
import json
import pytest
import tempfile
from pathlib import Path

# Import from scripts
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))
from migrate_workflows_to_plugin import (
    MigrationManifest,
    MigrationError,
    inventory_workflows,
    validate_source_data,
    copy_files_atomic,
    verify_checksums,
    run_smoke_tests,
    migrate_workflows_to_plugin,
    rollback_migration,
    _compute_file_hash,
)


class TestMigrationManifest:
    """Test manifest data structure."""

    def test_manifest_init(self):
        """Manifest initializes empty."""
        manifest = MigrationManifest()
        assert manifest.workflows == []

    def test_manifest_add_workflow(self):
        """Adding workflow updates list."""
        manifest = MigrationManifest()
        manifest.add_workflow("wid1", "hash1", "meta1")

        assert len(manifest.workflows) == 1
        assert manifest.workflows[0]["wid"] == "wid1"
        assert manifest.workflows[0]["yaml_hash"] == "hash1"

    def test_manifest_to_dict(self):
        """Manifest exports to dict."""
        manifest = MigrationManifest()
        manifest.add_workflow("wid1", "hash1", "meta1")

        data = manifest.to_dict()
        assert "workflows" in data
        assert "created_at" in data
        assert len(data["workflows"]) == 1

    def test_manifest_from_dict(self):
        """Manifest loads from dict."""
        data = {
            "workflows": [
                {"wid": "wid1", "yaml_hash": "hash1", "meta_hash": "meta1"}
            ]
        }

        manifest = MigrationManifest.from_dict(data)
        assert len(manifest.workflows) == 1
        assert manifest.workflows[0]["wid"] == "wid1"


class TestComputeFileHash:
    """Test SHA256 file hash computation."""

    def test_compute_file_hash(self, tmp_path):
        """Computing hash of a file works."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("test content")

        hash1 = asyncio.run(_compute_file_hash(test_file))
        hash2 = asyncio.run(_compute_file_hash(test_file))

        # Same content = same hash
        assert hash1 == hash2
        assert len(hash1) == 64  # SHA256 hex = 64 chars

    def test_compute_file_hash_different_content(self, tmp_path):
        """Different content produces different hash."""
        file1 = tmp_path / "file1.txt"
        file1.write_text("content1")

        file2 = tmp_path / "file2.txt"
        file2.write_text("content2")

        hash1 = asyncio.run(_compute_file_hash(file1))
        hash2 = asyncio.run(_compute_file_hash(file2))

        assert hash1 != hash2


class TestInventoryWorkflows:
    """Test workflow inventory (Phase 1)."""

    def test_inventory_empty_directory(self, tmp_path):
        """Inventory of empty directory returns empty manifest."""
        manifest = asyncio.run(inventory_workflows("tenant1", tmp_path))

        assert len(manifest.workflows) == 0

    def test_inventory_nonexistent_directory(self, tmp_path):
        """Inventory of missing directory returns empty manifest (no error)."""
        missing_dir = tmp_path / "missing"
        manifest = asyncio.run(inventory_workflows("tenant1", missing_dir))

        assert len(manifest.workflows) == 0

    def test_inventory_single_workflow(self, tmp_path):
        """Inventory of single workflow works."""
        yaml_file = tmp_path / "wid1.awp.yaml"
        yaml_file.write_text("name: test\n")

        meta_file = tmp_path / "wid1.meta.json"
        meta_file.write_text('{"title": "Test"}')

        manifest = asyncio.run(inventory_workflows("tenant1", tmp_path))

        assert len(manifest.workflows) == 1
        assert manifest.workflows[0]["wid"] == "wid1"
        assert manifest.workflows[0]["yaml_hash"] is not None
        assert manifest.workflows[0]["meta_hash"] is not None

    def test_inventory_multiple_workflows(self, tmp_path):
        """Inventory of multiple workflows works."""
        for i in range(3):
            yaml_file = tmp_path / f"wid{i}.awp.yaml"
            yaml_file.write_text(f"name: workflow{i}\n")

        manifest = asyncio.run(inventory_workflows("tenant1", tmp_path))

        assert len(manifest.workflows) == 3


class TestValidateSourceData:
    """Test source data validation (Phase 1)."""

    def test_validate_empty_manifest(self, tmp_path):
        """Validating empty manifest succeeds."""
        manifest = MigrationManifest()

        # Should not raise
        asyncio.run(validate_source_data("tenant1", manifest, tmp_path))

    def test_validate_missing_yaml_fails(self, tmp_path):
        """Validating with missing YAML file raises error."""
        manifest = MigrationManifest()
        manifest.add_workflow("wid1", "hash1", "meta1")

        with pytest.raises(MigrationError) as exc_info:
            asyncio.run(validate_source_data("tenant1", manifest, tmp_path))
        assert "YAML missing" in str(exc_info.value)

    def test_validate_valid_workflows(self, tmp_path):
        """Validating valid workflows succeeds."""
        yaml_file = tmp_path / "wid1.awp.yaml"
        yaml_file.write_text("name: test\n")

        manifest = MigrationManifest()
        manifest.add_workflow("wid1", "hash1", "missing")

        # Should not raise
        asyncio.run(validate_source_data("tenant1", manifest, tmp_path))


class TestCopyFilesAtomic:
    """Test atomic file copy (Phase 2)."""

    def test_copy_empty_source(self, tmp_path):
        """Copying empty source succeeds."""
        source = tmp_path / "source"
        source.mkdir()
        target = tmp_path / "target"

        manifest = MigrationManifest()

        backup = asyncio.run(copy_files_atomic("tenant1", manifest, source, target))

        # Target should exist (empty)
        assert target.exists()
        assert backup.exists()  # Backup dir created

    def test_copy_single_workflow(self, tmp_path):
        """Copying single workflow preserves checksums."""
        source = tmp_path / "source"
        source.mkdir()
        target = tmp_path / "target"

        # Create workflow in source
        yaml_file = source / "wid1.awp.yaml"
        yaml_file.write_text("name: test\n")

        # Compute hash
        yaml_hash = asyncio.run(_compute_file_hash(yaml_file))

        manifest = MigrationManifest()
        manifest.add_workflow("wid1", yaml_hash, "meta_hash")

        # Copy
        backup = asyncio.run(copy_files_atomic("tenant1", manifest, source, target))

        # Verify target has the file
        target_yaml = target / "wid1.awp.yaml"
        assert target_yaml.exists()
        assert target_yaml.read_text() == "name: test\n"


class TestVerifyChecksums:
    """Test checksum verification (Phase 2)."""

    def test_verify_empty_manifest(self, tmp_path):
        """Verifying empty manifest succeeds."""
        manifest = MigrationManifest()

        # Should not raise
        asyncio.run(verify_checksums("tenant1", manifest, tmp_path))

    def test_verify_checksum_match(self, tmp_path):
        """Verifying matching checksums succeeds."""
        yaml_file = tmp_path / "wid1.awp.yaml"
        yaml_file.write_text("name: test\n")

        yaml_hash = asyncio.run(_compute_file_hash(yaml_file))

        manifest = MigrationManifest()
        manifest.add_workflow("wid1", yaml_hash, "meta")

        # Should not raise
        asyncio.run(verify_checksums("tenant1", manifest, tmp_path))

    def test_verify_checksum_mismatch_fails(self, tmp_path):
        """Verifying mismatched checksums fails."""
        yaml_file = tmp_path / "wid1.awp.yaml"
        yaml_file.write_text("name: test\n")

        manifest = MigrationManifest()
        manifest.add_workflow("wid1", "wrong_hash", "meta")

        with pytest.raises(MigrationError) as exc_info:
            asyncio.run(verify_checksums("tenant1", manifest, tmp_path))
        assert "mismatch" in str(exc_info.value).lower()


class TestRunSmokeTests:
    """Test smoke tests (Phase 3)."""

    def test_smoke_test_empty_directory(self, tmp_path):
        """Smoke test on empty directory succeeds."""
        # Should not raise
        asyncio.run(run_smoke_tests("tenant1", tmp_path))

    def test_smoke_test_with_workflows(self, tmp_path):
        """Smoke test loads workflow files."""
        yaml_file = tmp_path / "wid1.awp.yaml"
        yaml_file.write_text("name: test\n")

        # Should not raise
        asyncio.run(run_smoke_tests("tenant1", tmp_path))

    def test_smoke_test_missing_target_fails(self, tmp_path):
        """Smoke test on missing directory fails."""
        missing_dir = tmp_path / "missing"

        with pytest.raises(MigrationError) as exc_info:
            asyncio.run(run_smoke_tests("tenant1", missing_dir))
        assert "does not exist" in str(exc_info.value)


class TestMigrateWorkflowsE2E:
    """End-to-end migration test (all 3 phases)."""

    def test_migrate_empty_tenant(self, tmp_path):
        """Migrating empty tenant succeeds."""
        source = tmp_path / "source"
        source.mkdir()
        target = tmp_path / "target"

        result = asyncio.run(migrate_workflows_to_plugin(
            tenant_id="tenant1",
            source_dir=source,
            target_dir=target,
        ))

        assert result["status"] == "success"
        assert result["workflows_count"] == 0
        assert target.exists()

    def test_migrate_single_workflow(self, tmp_path):
        """Migrating single workflow succeeds."""
        source = tmp_path / "source"
        source.mkdir()
        target = tmp_path / "target"

        # Create workflow in source
        yaml_file = source / "wid1.awp.yaml"
        yaml_file.write_text("name: test workflow\n")
        meta_file = source / "wid1.meta.json"
        meta_file.write_text('{"title": "Test"}')

        result = asyncio.run(migrate_workflows_to_plugin(
            tenant_id="tenant1",
            source_dir=source,
            target_dir=target,
        ))

        assert result["status"] == "success"
        assert result["workflows_count"] == 1

        # Verify target has files
        assert (target / "wid1.awp.yaml").exists()
        assert (target / "wid1.meta.json").exists()

    def test_migrate_multiple_workflows(self, tmp_path):
        """Migrating multiple workflows succeeds."""
        source = tmp_path / "source"
        source.mkdir()
        target = tmp_path / "target"

        # Create 3 workflows
        for i in range(3):
            yaml_file = source / f"wid{i}.awp.yaml"
            yaml_file.write_text(f"name: workflow {i}\n")

        result = asyncio.run(migrate_workflows_to_plugin(
            tenant_id="tenant1",
            source_dir=source,
            target_dir=target,
        ))

        assert result["status"] == "success"
        assert result["workflows_count"] == 3


class TestRollbackMigration:
    """Test rollback (recovery on failure)."""

    def test_rollback_restores_backup(self, tmp_path):
        """Rollback restores from backup."""
        backup_dir = tmp_path / "backup"
        backup_dir.mkdir()
        (backup_dir / "file.txt").write_text("backup content")

        target_dir = tmp_path / "target"
        target_dir.mkdir()
        (target_dir / "file.txt").write_text("old content")

        asyncio.run(rollback_migration("tenant1", backup_dir, target_dir))

        # Target should have backup content
        assert (target_dir / "file.txt").read_text() == "backup content"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
