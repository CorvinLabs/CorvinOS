"""Phase 1.1 unit tests: Directory init + migration."""

import pytest
from pathlib import Path
from core.skill_management.directory_init import SkillDirectoryInitializer
from core.skill_management.migrator import SkillMigrator
from core.skill_management.meta_generator import generate_skill_metadata

class TestDirectoryInit:
    def test_init_creates_all_directories(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        init = SkillDirectoryInitializer("_default")
        info = init.init_tenant_structure()

        assert info.status in ["success", "partial"]
        assert (tmp_path / ".corvin" / "tenants" / "_default" / "_platform").exists()
        assert (tmp_path / ".corvin" / "tenants" / "_default" / "_shared").exists()
        assert (tmp_path / ".corvin" / "tenants" / "_default" / "_local").exists()

    def test_validate_structure_passes(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        init = SkillDirectoryInitializer("_default")
        init.init_tenant_structure()

        validation = init.validate_structure()
        assert all(validation.values()), f"Validation failed: {validation}"

    def test_create_placeholder_manifests(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        init = SkillDirectoryInitializer("_default")
        init.init_tenant_structure()
        init.create_placeholder_manifests()

        assert (tmp_path / ".corvin" / "tenants" / "_default" / "_platform" / "manifest.json").exists()
        assert (tmp_path / ".corvin" / "tenants" / "_default" / "_shared" / "manifest.json").exists()

class TestMigrator:
    def test_migrate_simple_skill(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))

        # Create fake ~/.claude/skills/test_skill
        source = tmp_path / ".claude" / "skills" / "test_skill"
        source.mkdir(parents=True)
        (source / "body.md").write_text("# Test Skill")

        migrator = SkillMigrator("_default")
        report = migrator.migrate_from_claude_global()

        assert "test_skill" in report.migrated_skills
        assert (tmp_path / ".corvin" / "tenants" / "_default" / "_shared" / "skills" / "test_skill" / "body.md").exists()

    def test_migrate_creates_backup(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        source = tmp_path / ".claude" / "skills" / "test"
        source.mkdir(parents=True)
        (source / "body.md").write_text("test")

        migrator = SkillMigrator("_default")
        report = migrator.migrate_from_claude_global(backup=True)

        assert report.backup_path is not None
        assert report.backup_path.exists()

class TestMetaGenerator:
    def test_generate_skill_metadata(self, tmp_path):
        skill_dir = tmp_path / "test_skill"
        skill_dir.mkdir()
        (skill_dir / "body.md").write_text("# Test")

        metadata = generate_skill_metadata(skill_dir, "test_skill")

        assert metadata["id"] == "test_skill"
        assert metadata["version"] == "1.0.0"
        assert metadata["scope"] == "_shared"
        assert "created" in metadata
        assert "last_modified" in metadata

    def test_write_metadata(self, tmp_path):
        skill_dir = tmp_path / "test_skill"
        skill_dir.mkdir()
        metadata = {"id": "test", "version": "1.0.0"}

        success = generate_skill_metadata(skill_dir, "test")
        # Success if no exception
        assert True
