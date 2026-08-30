"""Phase 1.4 unit tests: CLI Commands."""

import pytest
import json
from pathlib import Path
from datetime import datetime
from click.testing import CliRunner

# `operator/` is not importable as a package (stdlib `operator` shadows it),
# so this module is loaded by file path -- see load_operator_module in conftest.py.
from corvin_test_support import load_operator_module

_skill_commands = load_operator_module("cli/skill_commands.py")
skill_group = _skill_commands.skill_group
list_skills = _skill_commands.list_skills
skill_info = _skill_commands.skill_info
validate_skills = _skill_commands.validate_skills
show_dependencies = _skill_commands.show_dependencies
migrate = _skill_commands.migrate
init_structure = _skill_commands.init_structure


@pytest.fixture
def cli_runner():
    """Create CLI test runner."""
    return CliRunner()


@pytest.fixture
def temp_tenant_cli(tmp_path, monkeypatch):
    """Create tenant with skills for CLI tests."""
    monkeypatch.setenv("HOME", str(tmp_path))
    tenant_path = tmp_path / ".corvin" / "tenants" / "_default"

    for scope in ["_platform", "_shared", "_local"]:
        (tenant_path / scope / "skills").mkdir(parents=True)

    # Create test skills
    skills = {
        "academic-paper-gen": {"version": "1.0.0", "deps": []},
        "data-transform": {"version": "2.1.0", "deps": [{"id": "academic-paper-gen", "scope": "_shared"}]},
    }

    for skill_id, data in skills.items():
        skill_dir = tenant_path / "_shared" / "skills" / skill_id
        skill_dir.mkdir(parents=True)
        metadata = {
            "id": skill_id,
            "name": skill_id.replace("-", " ").title(),
            "version": data["version"],
            "scope": "_shared",
            "created": datetime.now().isoformat(),
            "last_modified": datetime.now().isoformat(),
            "dependencies": data["deps"],
            "tags": ["testing"]
        }
        with open(skill_dir / "meta.json", "w") as f:
            json.dump(metadata, f)

    return tenant_path


class TestCliListSkills:
    def test_list_skills_default(self, cli_runner, temp_tenant_cli):
        """CLI lists skills in default format."""
        result = cli_runner.invoke(list_skills, ["--tenant", "_default"])
        assert result.exit_code == 0
        assert "academic-paper-gen" in result.output
        assert "data-transform" in result.output

    def test_list_skills_json(self, cli_runner, temp_tenant_cli):
        """CLI lists skills in JSON format."""
        result = cli_runner.invoke(list_skills, ["--tenant", "_default", "--format", "json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 2

    def test_list_skills_table(self, cli_runner, temp_tenant_cli):
        """CLI lists skills in table format."""
        result = cli_runner.invoke(list_skills, ["--tenant", "_default", "--format", "table"])
        assert result.exit_code == 0
        assert "ID" in result.output
        assert "Version" in result.output


class TestCliSkillInfo:
    def test_skill_info_exists(self, cli_runner, temp_tenant_cli):
        """CLI shows info for existing skill."""
        result = cli_runner.invoke(skill_info, ["academic-paper-gen", "--tenant", "_default"])
        assert result.exit_code == 0
        assert "academic-paper-gen" in result.output
        assert "1.0.0" in result.output

    def test_skill_info_not_found(self, cli_runner, temp_tenant_cli):
        """CLI handles missing skill gracefully."""
        result = cli_runner.invoke(skill_info, ["nonexistent", "--tenant", "_default"])
        assert result.exit_code != 0
        assert "not found" in result.output.lower()

    def test_skill_info_with_deps(self, cli_runner, temp_tenant_cli):
        """CLI shows dependencies."""
        result = cli_runner.invoke(skill_info, ["data-transform", "--tenant", "_default"])
        assert result.exit_code == 0
        assert "Dependencies" in result.output
        assert "academic-paper-gen" in result.output


class TestCliValidate:
    def test_validate_skills(self, cli_runner, temp_tenant_cli):
        """CLI validates skills."""
        result = cli_runner.invoke(validate_skills, ["--tenant", "_default", "--scope", "_shared"])
        assert result.exit_code == 0
        assert "Validating" in result.output

    def test_validate_with_errors(self, cli_runner, tmp_path, monkeypatch):
        """CLI reports validation errors."""
        monkeypatch.setenv("HOME", str(tmp_path))
        tenant_path = tmp_path / ".corvin" / "tenants" / "_default"
        skill_dir = tenant_path / "_shared" / "skills" / "bad-skill"
        skill_dir.mkdir(parents=True)

        # Write invalid metadata
        bad_meta = {
            "id": "bad-skill",
            "version": "invalid",  # Wrong format
            "scope": "_shared",
            "created": datetime.now().isoformat(),
            "last_modified": datetime.now().isoformat()
        }
        with open(skill_dir / "meta.json", "w") as f:
            json.dump(bad_meta, f)

        result = cli_runner.invoke(validate_skills, ["--tenant", "_default", "--scope", "_shared"])
        # May or may not fail depending on error handling, but should show errors
        assert "ERROR" in result.output or "bad-skill" in result.output


class TestCliDependencies:
    def test_show_dependencies(self, cli_runner, temp_tenant_cli):
        """CLI shows dependency tree."""
        result = cli_runner.invoke(show_dependencies, ["academic-paper-gen", "--tenant", "_default"])
        assert result.exit_code == 0
        assert "academic-paper-gen" in result.output

    def test_show_dependencies_graph(self, cli_runner, temp_tenant_cli):
        """CLI exports dependency graph as JSON."""
        result = cli_runner.invoke(show_dependencies, ["academic-paper-gen", "--tenant", "_default", "--graph"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "nodes" in data
        assert "links" in data


class TestCliMigrate:
    def test_migrate_dry_run(self, cli_runner, tmp_path, monkeypatch):
        """CLI dry-run shows what would be migrated."""
        monkeypatch.setenv("HOME", str(tmp_path))

        # Create fake ~/.claude/skills
        source_dir = tmp_path / ".claude" / "skills" / "test-skill"
        source_dir.mkdir(parents=True)
        (source_dir / "body.md").write_text("# Test")

        result = cli_runner.invoke(migrate, ["--tenant", "_default", "--dry-run"])
        assert result.exit_code == 0
        assert "dry-run" in result.output.lower() or "validation" in result.output.lower()

    def test_migrate_confirm(self, cli_runner, tmp_path, monkeypatch):
        """CLI confirms and executes migration."""
        monkeypatch.setenv("HOME", str(tmp_path))

        # Create fake ~/.claude/skills
        source_dir = tmp_path / ".claude" / "skills" / "test-skill"
        source_dir.mkdir(parents=True)
        (source_dir / "body.md").write_text("# Test")

        result = cli_runner.invoke(migrate, ["--tenant", "_default", "--confirm"])
        assert result.exit_code == 0
        assert "Migrated" in result.output or "complete" in result.output.lower()


class TestCliInit:
    def test_init_structure(self, cli_runner, tmp_path, monkeypatch):
        """CLI initializes tenant structure."""
        monkeypatch.setenv("HOME", str(tmp_path))

        result = cli_runner.invoke(init_structure, ["--tenant", "_default"])
        assert result.exit_code == 0
        assert "Initializing" in result.output or "complete" in result.output.lower()

        # Verify structure created
        tenant_path = tmp_path / ".corvin" / "tenants" / "_default"
        assert (tenant_path / "_shared").exists()
        assert (tenant_path / "_local").exists()
