"""
Unit tests for operator/marketplace/generate_index_v2.py (ADR-0511).

Tests cover:
1. Plugin discovery (directory traversal)
2. Schema validation (plugin.json against plugin-schema.json)
3. Directory structure validation (tier/category/plugin_id hierarchy)
4. Index generation (aggregation, grouping by category/tier)
5. Error handling (invalid JSON, schema violations, path mismatches)
"""

import json
import pytest
from pathlib import Path
from tempfile import TemporaryDirectory

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from operator.marketplace.generate_index_v2 import MarketplaceIndexGenerator


@pytest.fixture
def marketplace_dir():
    """Create a temporary marketplace directory with test structure."""
    with TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # Create schema files
        plugin_schema = {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "required": ["id", "type", "name", "version", "author", "license", "tier", "category", "description", "distribution", "last_updated"],
            "properties": {
                "id": {"type": "string"},
                "type": {"type": "string", "enum": ["plugin"]},
                "name": {"type": "string"},
                "version": {"type": "string"},
                "author": {"type": "string"},
                "license": {"type": "string"},
                "tier": {"type": "string", "enum": ["buildin", "contributor"]},
                "category": {"type": "string"},
                "description": {"type": "string"},
                "distribution": {"type": "object"},
                "last_updated": {"type": "string", "format": "date-time"}
            }
        }

        index_schema = {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "required": ["version", "schema", "generated_at", "plugin_count", "plugins"],
            "properties": {
                "version": {"type": "string"},
                "schema": {"type": "string"},
                "generated_at": {"type": "string"},
                "plugin_count": {"type": "integer"},
                "plugins": {"type": "array"}
            }
        }

        (root / "plugin-schema.json").write_text(json.dumps(plugin_schema, indent=2))
        (root / "index-schema-v2.json").write_text(json.dumps(index_schema, indent=2))

        yield root


def create_plugin(
    root: Path,
    tier: str,
    category: str,
    plugin_id: str,
    name: str = "Test Plugin",
    version: str = "1.0.0"
) -> dict:
    """Create a test plugin in the marketplace directory."""
    plugin_dir = root / "plugins" / tier / category / plugin_id
    plugin_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "id": f"plugin:{tier}-{category}-{plugin_id}",
        "type": "plugin",
        "name": name,
        "version": version,
        "author": "Test Author",
        "license": "Apache-2.0",
        "tier": tier,
        "category": category,
        "description": "Test plugin description",
        "distribution": {
            "supports_source": True,
            "supports_wheel": tier == "buildin"
        },
        "last_updated": "2026-08-31T00:00:00Z"
    }

    (plugin_dir / "plugin.json").write_text(json.dumps(manifest, indent=2))
    return manifest


class TestIndexGeneratorV2:
    """Test suite for MarketplaceIndexGenerator v2."""

    def test_discover_plugins_empty(self, marketplace_dir):
        """Test discovery with no plugins."""
        gen = MarketplaceIndexGenerator(
            marketplace_dir,
            plugin_schema_path=marketplace_dir / "plugin-schema.json",
            index_schema_path=marketplace_dir / "index-schema-v2.json"
        )
        plugins = gen.discover_plugins()
        assert plugins == []

    def test_discover_plugins_single(self, marketplace_dir):
        """Test discovery with one plugin."""
        create_plugin(marketplace_dir, "buildin", "memory", "test_plugin")

        gen = MarketplaceIndexGenerator(
            marketplace_dir,
            plugin_schema_path=marketplace_dir / "plugin-schema.json",
            index_schema_path=marketplace_dir / "index-schema-v2.json"
        )
        plugins = gen.discover_plugins()
        assert len(plugins) == 1
        assert plugins[0].name == "test_plugin"

    def test_discover_plugins_multiple_categories(self, marketplace_dir):
        """Test discovery across multiple categories."""
        create_plugin(marketplace_dir, "buildin", "memory", "recall_backend")
        create_plugin(marketplace_dir, "buildin", "security_compliance", "audit_backend")
        create_plugin(marketplace_dir, "buildin", "integration", "router_backend")

        gen = MarketplaceIndexGenerator(
            marketplace_dir,
            plugin_schema_path=marketplace_dir / "plugin-schema.json",
            index_schema_path=marketplace_dir / "index-schema-v2.json"
        )
        plugins = gen.discover_plugins()
        assert len(plugins) == 3

    def test_validate_plugin_schema_pass(self, marketplace_dir):
        """Test plugin schema validation (valid plugin)."""
        create_plugin(marketplace_dir, "buildin", "memory", "test_plugin")
        plugin_path = marketplace_dir / "plugins" / "buildin" / "memory" / "test_plugin" / "plugin.json"

        gen = MarketplaceIndexGenerator(
            marketplace_dir,
            plugin_schema_path=marketplace_dir / "plugin-schema.json",
            index_schema_path=marketplace_dir / "index-schema-v2.json"
        )
        result = gen.validate_plugin(plugin_path)
        assert result is not None
        assert result["id"] == "plugin:buildin-memory-test_plugin"

    def test_validate_plugin_invalid_json(self, marketplace_dir):
        """Test plugin validation (invalid JSON)."""
        plugin_dir = marketplace_dir / "plugins" / "buildin" / "memory" / "broken_plugin"
        plugin_dir.mkdir(parents=True, exist_ok=True)
        (plugin_dir / "plugin.json").write_text("{ invalid json")

        gen = MarketplaceIndexGenerator(
            marketplace_dir,
            plugin_schema_path=marketplace_dir / "plugin-schema.json",
            index_schema_path=marketplace_dir / "index-schema-v2.json"
        )
        result = gen.validate_plugin(plugin_dir / "plugin.json")
        assert result is None
        assert len(gen.errors) > 0

    def test_directory_structure_validation_pass(self, marketplace_dir):
        """Test directory structure validation (correct path)."""
        manifest = create_plugin(marketplace_dir, "buildin", "memory", "recall_backend")
        plugin_path = marketplace_dir / "plugins" / "buildin" / "memory" / "recall_backend" / "plugin.json"

        gen = MarketplaceIndexGenerator(
            marketplace_dir,
            plugin_schema_path=marketplace_dir / "plugin-schema.json",
            index_schema_path=marketplace_dir / "index-schema-v2.json"
        )
        result = gen.verify_directory_structure(plugin_path, manifest)
        assert result is True

    def test_directory_structure_validation_id_mismatch(self, marketplace_dir):
        """Test directory structure validation (ID mismatch)."""
        plugin_dir = marketplace_dir / "plugins" / "buildin" / "memory" / "wrong_id"
        plugin_dir.mkdir(parents=True, exist_ok=True)

        manifest = {
            "id": "plugin:buildin-memory-correct_id",  # Doesn't match directory
            "type": "plugin",
            "name": "Test",
            "version": "1.0.0",
            "author": "Test",
            "license": "Apache-2.0",
            "tier": "buildin",
            "category": "memory",
            "description": "Test",
            "distribution": {"supports_source": True, "supports_wheel": True},
            "last_updated": "2026-08-31T00:00:00Z"
        }

        (plugin_dir / "plugin.json").write_text(json.dumps(manifest))

        gen = MarketplaceIndexGenerator(
            marketplace_dir,
            plugin_schema_path=marketplace_dir / "plugin-schema.json",
            index_schema_path=marketplace_dir / "index-schema-v2.json"
        )
        result = gen.verify_directory_structure(plugin_dir / "plugin.json", manifest)
        assert result is False

    def test_generate_index_single_plugin(self, marketplace_dir):
        """Test index generation with single plugin."""
        create_plugin(marketplace_dir, "buildin", "memory", "recall_backend", "CEL Session Recall")

        gen = MarketplaceIndexGenerator(
            marketplace_dir,
            plugin_schema_path=marketplace_dir / "plugin-schema.json",
            index_schema_path=marketplace_dir / "index-schema-v2.json"
        )
        index = gen.generate_index()

        assert index["version"] == "2.0"
        assert index["schema"] == "ADR-0511"
        assert index["plugin_count"] == 1
        assert len(index["plugins"]) == 1
        assert index["plugins"][0]["name"] == "CEL Session Recall"

    def test_generate_index_grouping(self, marketplace_dir):
        """Test index grouping by category and tier."""
        create_plugin(marketplace_dir, "buildin", "memory", "plugin1")
        create_plugin(marketplace_dir, "buildin", "memory", "plugin2")
        create_plugin(marketplace_dir, "buildin", "security_compliance", "plugin3")
        create_plugin(marketplace_dir, "contributor", "integration", "plugin4")

        gen = MarketplaceIndexGenerator(
            marketplace_dir,
            plugin_schema_path=marketplace_dir / "plugin-schema.json",
            index_schema_path=marketplace_dir / "index-schema-v2.json"
        )
        index = gen.generate_index()

        assert index["plugin_count"] == 4
        assert len(index["by_category"]["memory"]) == 2
        assert len(index["by_category"]["security_compliance"]) == 1
        assert len(index["by_category"]["integration"]) == 1
        assert len(index["by_tier"]["buildin"]) == 3
        assert len(index["by_tier"]["contributor"]) == 1

    def test_generate_index_lookup_table(self, marketplace_dir):
        """Test index by_id lookup table."""
        create_plugin(marketplace_dir, "buildin", "memory", "recall")

        gen = MarketplaceIndexGenerator(
            marketplace_dir,
            plugin_schema_path=marketplace_dir / "plugin-schema.json",
            index_schema_path=marketplace_dir / "index-schema-v2.json"
        )
        index = gen.generate_index()

        plugin_id = "plugin:buildin-memory-recall"
        assert plugin_id in index["by_id"]
        assert index["by_id"][plugin_id]["name"] == "Test Plugin"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
