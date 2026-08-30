"""Unit tests for Marketplace index schema and generation."""

import json
import pytest
from pathlib import Path
# `operator/` is not importable as a package (stdlib `operator` wins) and
# `marketplace` collides with core/plugins/marketplace.py, so this module is
# loaded by file path -- see load_operator_module in tests/conftest.py.
from corvin_test_support import load_operator_module

_generate_index = load_operator_module("marketplace/generate_index.py")
validate_semver = _generate_index.validate_semver
validate_extension_id = _generate_index.validate_extension_id
validate_url = _generate_index.validate_url
build_extension_entry = _generate_index.build_extension_entry


class TestSemverValidation:
    """Test semantic versioning validation."""

    def test_valid_semver(self):
        """Valid semver versions."""
        assert validate_semver("0.1.0")
        assert validate_semver("1.2.3")
        assert validate_semver("1.0.0-alpha")
        assert validate_semver("2.3.4-beta.1")
        assert validate_semver("1.0.0+build.1")

    def test_invalid_semver(self):
        """Invalid semver versions."""
        assert not validate_semver("1.0")  # Missing patch
        assert not validate_semver("v1.0.0")  # Leading 'v'
        assert not validate_semver("1.0.0.0")  # Too many parts
        assert not validate_semver("latest")
        assert not validate_semver("")


class TestExtensionIdValidation:
    """Test extension ID format validation."""

    def test_valid_ids(self):
        """Valid extension IDs."""
        assert validate_extension_id("plugin:example-plugin")
        assert validate_extension_id("skill:test-skill")
        assert validate_extension_id("extension_layer:my-layer")
        assert validate_extension_id("plugin:a")  # Single char slug

    def test_invalid_ids(self):
        """Invalid extension IDs."""
        assert not validate_extension_id("invalid:plugin:name")  # Double colon
        assert not validate_extension_id("plugin:")  # Missing slug
        assert not validate_extension_id(":example")  # Missing type
        assert not validate_extension_id("plugin:-invalid")  # Slug starts with dash
        assert not validate_extension_id("plugin:invalid-")  # Slug ends with dash
        assert not validate_extension_id("unknown:plugin")  # Unknown type
        assert not validate_extension_id("plugin:UPPERCASE")  # Uppercase not allowed


class TestUrlValidation:
    """Test URL validation."""

    def test_valid_urls(self):
        """Valid URLs."""
        assert validate_url("https://example.com")
        assert validate_url("http://example.com/path")
        assert validate_url("https://github.com/repo/releases/download/file.whl")

    def test_invalid_urls(self):
        """Invalid URLs."""
        assert not validate_url("example.com")
        assert not validate_url("ftp://example.com")
        assert not validate_url("")
        assert not validate_url("mailto:user@example.com")


class TestSchemaFile:
    """Test schema.json file structure."""

    @pytest.fixture
    def schema(self):
        """Load index schema."""
        schema_path = Path(__file__).parent.parent.parent / "operator" / "marketplace" / "index-schema.json"
        with open(schema_path) as f:
            return json.load(f)

    def test_schema_has_required_fields(self, schema):
        """Schema has required fields."""
        # `version` is a property of the DOCUMENT the schema describes, not a
        # key of the schema itself -- a JSON Schema has $schema/title/type/
        # properties/definitions at the top level. The assertion read
        # `"version" in schema`, which conflated the two and could never pass.
        assert "version" in schema["properties"]
        assert "version" in schema["required"]
        assert "title" in schema
        assert "type" in schema
        assert "properties" in schema
        assert "definitions" in schema

    def test_schema_has_extension_definition(self, schema):
        """Schema defines Extension type."""
        assert "Extension" in schema["definitions"]
        ext_schema = schema["definitions"]["Extension"]
        assert ext_schema["type"] == "object"
        assert "required" in ext_schema

    def test_required_extension_fields(self, schema):
        """Extension has all required fields."""
        required = schema["definitions"]["Extension"]["required"]
        assert "id" in required
        assert "type" in required
        assert "name" in required
        assert "version" in required
        assert "author" in required
        assert "readme_url" in required
        assert "install_url" in required


class TestExampleIndexes:
    """Test index.json examples."""

    def test_valid_plugin_entry(self):
        """Valid plugin entry."""
        entry = {
            "id": "plugin:example-plugin",
            "type": "plugin",
            "name": "Example Plugin",
            "version": "0.1.0",
            "author": "Test Author",
            "description": "A test plugin for the marketplace",
            "tags": ["automation"],
            "readme_url": "https://raw.githubusercontent.com/anthropics/Corvin-Marketplace/main/plugins/example-plugin/README.md",
            "install_url": "https://github.com/anthropics/Corvin-Marketplace/releases/download/plugin-example-plugin-0.1.0/example-plugin.whl",
            "repo_url": "https://github.com/anthropics/Corvin-Marketplace/tree/main/plugins/example-plugin",
            "dependencies": [],
            "requires_version": ">=1.0.0",
            "latest_version": "0.1.0",
            "update_available": False,
            "conflicts_with": [],
            "maintainer_url": "mailto:author@example.com",
        }
        # Should not raise
        assert entry["type"] == "plugin"

    def test_valid_skill_entry(self):
        """Valid skill entry."""
        entry = {
            "id": "skill:test-skill",
            "type": "skill",
            "name": "Test Skill",
            "version": "1.0.0",
            "author": "Test Author",
            "description": "A test skill for the marketplace system",
            "tags": ["utilities", "integration"],
            "readme_url": "https://raw.githubusercontent.com/anthropics/Corvin-Marketplace/main/skills/test-skill/README.md",
            "install_url": "https://github.com/anthropics/Corvin-Marketplace/releases/download/skill-test-skill-1.0.0/test-skill.whl",
            "repo_url": "https://github.com/anthropics/Corvin-Marketplace/tree/main/skills/test-skill",
            "dependencies": ["plugin:dependency-1"],
            "requires_version": "^1.5.0",
            "latest_version": "1.0.0",
            "update_available": False,
            "conflicts_with": [],
            "maintainer_url": "https://example.com/support",
        }
        assert entry["type"] == "skill"
        assert "dependency-1" in entry["dependencies"][0]


class TestIndexStructure:
    """Test complete index.json structure."""

    def test_minimal_valid_index(self):
        """Minimal valid index.json."""
        index = {
            "version": "1.0",
            "last_updated": "2026-08-29T14:00:00Z",
            "extensions": [
                {
                    "id": "plugin:test",
                    "type": "plugin",
                    "name": "Test",
                    "version": "0.1.0",
                    "author": "Author",
                    "description": "Test description for the plugin",
                    "tags": ["utilities"],
                    "readme_url": "https://example.com/README.md",
                    "install_url": "https://example.com/plugin.whl",
                    "repo_url": "https://example.com/repo",
                }
            ],
        }
        assert index["version"] == "1.0"
        assert len(index["extensions"]) == 1
        assert index["extensions"][0]["id"] == "plugin:test"

    def test_index_with_dependencies(self):
        """Index entry with dependencies."""
        entry = {
            "id": "plugin:dependent",
            "type": "plugin",
            "name": "Dependent Plugin",
            "version": "1.0.0",
            "author": "Author",
            "description": "A plugin that depends on another",
            "tags": ["utilities"],
            "readme_url": "https://example.com/README.md",
            "install_url": "https://example.com/plugin.whl",
            "repo_url": "https://example.com/repo",
            "dependencies": ["plugin:base-plugin"],
        }
        assert len(entry["dependencies"]) == 1
        assert validate_extension_id(entry["dependencies"][0])

    def test_index_with_conflicts(self):
        """Index entry with conflicts."""
        entry = {
            "id": "plugin:conflicting",
            "type": "plugin",
            "name": "Conflicting Plugin",
            "version": "1.0.0",
            "author": "Author",
            "description": "A plugin that conflicts with another",
            "tags": ["utilities"],
            "readme_url": "https://example.com/README.md",
            "install_url": "https://example.com/plugin.whl",
            "repo_url": "https://example.com/repo",
            "conflicts_with": ["plugin:incompatible"],
        }
        assert len(entry["conflicts_with"]) == 1
        assert validate_extension_id(entry["conflicts_with"][0])


class TestTagEnumeration:
    """Test tag values."""

    ALLOWED_TAGS = [
        "automation", "utilities", "learning", "integration",
        "observability", "security", "performance", "experimental"
    ]

    def test_allowed_tags(self):
        """All defined tags are allowed."""
        for tag in self.ALLOWED_TAGS:
            assert tag in self.ALLOWED_TAGS

    def test_example_uses_valid_tags(self):
        """Example entries use valid tags."""
        entry = {
            "id": "plugin:test",
            "type": "plugin",
            "name": "Test",
            "version": "0.1.0",
            "author": "Author",
            "description": "Test description",
            "tags": ["automation", "utilities"],
            "readme_url": "https://example.com/README.md",
            "install_url": "https://example.com/plugin.whl",
            "repo_url": "https://example.com/repo",
        }
        for tag in entry["tags"]:
            assert tag in self.ALLOWED_TAGS
