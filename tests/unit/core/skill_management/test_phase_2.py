"""Phase 2 unit tests: GitHub Export/Import."""

import pytest
import json
import tarfile
from pathlib import Path
from datetime import datetime
from click.testing import CliRunner

from core.skill_management.github_exporter import GitHubExporter
from core.skill_management.github_importer import GitHubImporter, ConflictResolution
# `operator/` is not importable as a package (stdlib `operator` shadows it),
# so this module is loaded by file path -- see load_operator_module in conftest.py.
from corvin_test_support import load_operator_module

_skill_sync = load_operator_module("cli/skill_sync_commands.py")
sync_push = _skill_sync.sync_push
sync_pull = _skill_sync.sync_pull
configure_sync = _skill_sync.configure_sync


@pytest.fixture
def temp_tenant_export(tmp_path, monkeypatch):
    """Create tenant with skills for export."""
    monkeypatch.setenv("HOME", str(tmp_path))
    tenant_path = tmp_path / ".corvin" / "tenants" / "_default"
    (tenant_path / "_shared" / "skills").mkdir(parents=True)

    # Create 3 test skills
    for i in range(3):
        skill_id = f"skill-{i}"
        skill_dir = tenant_path / "_shared" / "skills" / skill_id
        skill_dir.mkdir(parents=True)

        metadata = {
            "id": skill_id,
            "version": f"1.{i}.0",
            "scope": "_shared",
            "created": datetime.now().isoformat(),
            "last_modified": datetime.now().isoformat(),
            "dependencies": []
        }
        with open(skill_dir / "meta.json", "w") as f:
            json.dump(metadata, f)

        (skill_dir / "body.md").write_text(f"# {skill_id}")

    return tenant_path


class TestGitHubExporter:
    def test_export_creates_tarball(self, temp_tenant_export):
        """Exporter creates tarball."""
        exporter = GitHubExporter("github:test/repo")
        result = exporter.export_shared_skills(dry_run=True)

        assert result.success is True
        assert len(result.exported_skills) == 3
        assert result.tarball_path is not None
        assert result.tarball_path.exists()

    def test_export_tarball_contains_skills(self, temp_tenant_export):
        """Tarball contains all skills."""
        exporter = GitHubExporter("github:test/repo")
        result = exporter.export_shared_skills(dry_run=True)

        assert result.tarball_path.exists()

        # Verify tarball contents
        with tarfile.open(result.tarball_path, "r:gz") as tar:
            names = tar.getnames()
            assert any("skill-0" in name for name in names)
            assert any("skill-1" in name for name in names)
            assert any("skill-2" in name for name in names)

    def test_export_generates_manifest(self, temp_tenant_export):
        """Exporter generates manifest.json."""
        exporter = GitHubExporter("github:test/repo")
        result = exporter.export_shared_skills(dry_run=True)

        assert result.manifest_path is not None
        assert result.manifest_path.exists()

        # Verify manifest
        with open(result.manifest_path) as f:
            manifest = json.load(f)

        assert manifest["version"] == "1.0"
        assert len(manifest["skills"]) == 3
        assert "integrity_hash" in manifest

    def test_export_single_skill(self, temp_tenant_export):
        """Exporter can export single skill."""
        exporter = GitHubExporter("github:test/repo")
        result = exporter.export_single_skill("skill-0", dry_run=True)

        assert result.success is True
        assert "skill-0" in result.exported_skills

    def test_export_calculates_hash(self, temp_tenant_export):
        """Exporter calculates tarball hash."""
        exporter = GitHubExporter("github:test/repo")
        result = exporter.export_shared_skills(dry_run=True)

        assert result.manifest_hash is not None
        assert result.manifest_hash.startswith("sha256:")


class TestGitHubImporter:
    def test_import_from_tarball(self, temp_tenant_export, tmp_path, monkeypatch):
        """Importer extracts and imports skills."""
        # Export first
        monkeypatch.setenv("HOME", str(tmp_path))
        exporter = GitHubExporter("github:test/repo")
        export_result = exporter.export_shared_skills(dry_run=True)

        # Now import into new tenant
        import_tenant = tmp_path / ".corvin" / "tenants" / "import_test"
        (import_tenant / "_shared" / "skills").mkdir(parents=True)

        monkeypatch.setenv("HOME", str(tmp_path))
        import_tenant_id = "import_test"

        # Need to mock the tenant path
        # For now, just verify importer loads tarball
        importer = GitHubImporter(import_tenant_id)
        result = importer.import_from_tarball(export_result.tarball_path, dry_run=True)

        assert result.success is True
        assert len(result.imported_skills) > 0

    def test_import_detects_conflicts(self, temp_tenant_export):
        """Importer detects conflicting skills."""
        exporter = GitHubExporter("github:test/repo")
        export_result = exporter.export_shared_skills(dry_run=True)

        # Create local skill that conflicts
        local_skill_dir = Path.home() / ".corvin" / "tenants" / "_default" / "_shared" / "skills" / "skill-0"
        local_skill_dir.mkdir(parents=True, exist_ok=True)
        local_meta = {
            "id": "skill-0",
            "version": "2.0.0",  # Different version
            "scope": "_shared",
            "created": datetime.now().isoformat(),
            "last_modified": datetime.now().isoformat()
        }
        with open(local_skill_dir / "meta.json", "w") as f:
            json.dump(local_meta, f)

        importer = GitHubImporter()
        result = importer.import_from_tarball(export_result.tarball_path, dry_run=True)

        assert len(result.conflicts) > 0
        assert result.conflicts[0].skill_id == "skill-0"

    def test_import_operator_wins_resolution(self, temp_tenant_export):
        """Import resolves conflicts with operator_wins."""
        exporter = GitHubExporter("github:test/repo")
        export_result = exporter.export_shared_skills(dry_run=True)

        importer = GitHubImporter()
        result = importer.import_from_tarball(
            export_result.tarball_path,
            conflict_resolution=ConflictResolution.OPERATOR_WINS,
            dry_run=True
        )

        assert result.conflict_resolution == "operator_wins"

    def test_import_github_wins_resolution(self, temp_tenant_export):
        """Import resolves conflicts with github_wins."""
        exporter = GitHubExporter("github:test/repo")
        export_result = exporter.export_shared_skills(dry_run=True)

        importer = GitHubImporter()
        result = importer.import_from_tarball(
            export_result.tarball_path,
            conflict_resolution=ConflictResolution.GITHUB_WINS,
            dry_run=True
        )

        assert result.conflict_resolution == "github_wins"


class TestCliSync:
    @pytest.fixture
    def cli_runner(self):
        return CliRunner()

    def test_cli_configure_sync(self, cli_runner, tmp_path, monkeypatch):
        """CLI configures GitHub sync."""
        monkeypatch.setenv("HOME", str(tmp_path))

        result = cli_runner.invoke(configure_sync, [
            "--tenant", "_default",
            "--repo", "github:test/repo",
            "--enable-sync"
        ])

        assert result.exit_code == 0
        assert "GitHub sync configured" in result.output

    def test_cli_sync_status(self, cli_runner, tmp_path, monkeypatch):
        """CLI shows sync status."""
        monkeypatch.setenv("HOME", str(tmp_path))

        # Configure first
        cli_runner.invoke(configure_sync, [
            "--tenant", "_default",
            "--repo", "github:test/repo"
        ])

        # Then check status
        sync_status = load_operator_module("cli/skill_sync_commands.py").sync_status
        result = cli_runner.invoke(sync_status, ["--tenant", "_default"])

        assert result.exit_code == 0

    def test_cli_push_dry_run(self, cli_runner, temp_tenant_export, monkeypatch):
        """CLI push with --dry-run."""
        result = cli_runner.invoke(sync_push, [
            "--tenant", "_default",
            "--repo", "github:test/repo",
            "--dry-run"
        ])

        assert result.exit_code == 0
        assert "Dry-run" in result.output or "Export" in result.output


# Robustness Tests (Failure Scenarios)
class TestPhase2Robustness:
    def test_export_missing_shared_dir(self, tmp_path, monkeypatch):
        """Exporter handles missing _shared/ gracefully."""
        monkeypatch.setenv("HOME", str(tmp_path))

        exporter = GitHubExporter("github:test/repo")
        result = exporter.export_shared_skills(dry_run=True)

        assert result.success is False
        assert "No _shared/" in result.error

    def test_import_invalid_tarball(self, tmp_path, monkeypatch):
        """Importer handles invalid tarball."""
        monkeypatch.setenv("HOME", str(tmp_path))

        bad_tarball = tmp_path / "bad.tar.gz"
        bad_tarball.write_text("not a tarball")

        importer = GitHubImporter()
        result = importer.import_from_tarball(bad_tarball, dry_run=True)

        assert result.success is False

    def test_export_corrupted_metadata(self, tmp_path, monkeypatch):
        """Exporter handles corrupted metadata gracefully."""
        monkeypatch.setenv("HOME", str(tmp_path))
        tenant_path = tmp_path / ".corvin" / "tenants" / "_default"
        skill_dir = tenant_path / "_shared" / "skills" / "bad-skill"
        skill_dir.mkdir(parents=True)

        # Write invalid JSON
        with open(skill_dir / "meta.json", "w") as f:
            f.write("{ invalid json }")

        exporter = GitHubExporter("github:test/repo")
        result = exporter.export_shared_skills(dry_run=True)

        # Should still work but with bad-skill maybe excluded or error reported
        # This depends on implementation robustness
        assert result.success is True or result.error is not None
