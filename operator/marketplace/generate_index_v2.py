#!/usr/bin/env python3
"""
Marketplace Index Generator v2 — ADR-0511 compliant.

Generates index.json from plugin.json manifests in operator/marketplace/plugins/.

**Source of Truth:** plugin.json (JSON Schema validation)
**Structure:** operator/marketplace/plugins/{buildin,contributor}/[category]/[plugin_id]/plugin.json
**Output:** operator/marketplace/index/plugins.json (validated against index-schema.json)

This replaces generate_index.py (legacy YAML-based, now deprecated).
See ADR-0511 for full specification.
"""

import json
import sys
from pathlib import Path
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone
import logging
import jsonschema

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)


class MarketplaceIndexGenerator:
    """Generate marketplace index from ADR-0511 plugin.json manifests."""

    VALID_TIERS = {"buildin", "contributor"}
    VALID_CATEGORIES = {
        "memory",
        "security_compliance",
        "integration",
        "data_processing",
        "observability",
    }

    def __init__(
        self,
        marketplace_root: Path,
        plugin_schema_path: Optional[Path] = None,
        index_schema_path: Optional[Path] = None,
    ):
        self.marketplace_root = Path(marketplace_root)
        self.plugins_dir = self.marketplace_root / "plugins"

        # Load schemas
        if plugin_schema_path is None:
            plugin_schema_path = self.marketplace_root / "plugin-schema.json"
        if index_schema_path is None:
            index_schema_path = self.marketplace_root / "index-schema-v2.json"

        with open(plugin_schema_path) as f:
            self.plugin_schema = json.load(f)

        with open(index_schema_path) as f:
            self.index_schema = json.load(f)

        self.plugins: List[Dict[str, Any]] = []
        self.errors: List[str] = []

    def discover_plugins(self) -> List[Path]:
        """
        Discover all plugin.json files in the marketplace.

        Expected structure:
            plugins/{buildin|contributor}/{category}/{plugin_id}/plugin.json
        """
        found = []

        if not self.plugins_dir.exists():
            logger.warning(f"Plugins directory not found: {self.plugins_dir}")
            return found

        for plugin_json in self.plugins_dir.rglob("plugin.json"):
            found.append(plugin_json)

        logger.info(f"Discovered {len(found)} plugin.json files")
        return found

    def validate_plugin(self, plugin_path: Path) -> Optional[Dict[str, Any]]:
        """
        Load and validate a single plugin.json against the schema.

        Returns the plugin dict if valid, None if validation fails.
        """
        try:
            with open(plugin_path) as f:
                plugin = json.load(f)
        except json.JSONDecodeError as e:
            error = f"❌ {plugin_path}: Invalid JSON — {e}"
            logger.error(error)
            self.errors.append(error)
            return None
        except Exception as e:
            error = f"❌ {plugin_path}: Read failed — {e}"
            logger.error(error)
            self.errors.append(error)
            return None

        # Validate against schema
        try:
            jsonschema.validate(plugin, self.plugin_schema)
            logger.info(f"✅ {plugin_path}: Valid")
            return plugin
        except jsonschema.ValidationError as e:
            error = f"❌ {plugin_path}: Schema violation — {e.message}"
            logger.error(error)
            self.errors.append(error)
            return None

    def verify_directory_structure(self, plugin_path: Path, plugin: Dict) -> bool:
        """
        Verify that the plugin.json is in the expected location.

        Expected: plugins/{tier}/{category}/{plugin_id}/plugin.json
        Plugin ID should match: plugin:{tier}-{category}-...
        """
        parts = plugin_path.relative_to(self.plugins_dir).parts

        if len(parts) != 4:
            error = f"❌ {plugin_path}: Wrong directory depth (expected 4 levels, got {len(parts)})"
            logger.error(error)
            self.errors.append(error)
            return False

        tier, category, plugin_id, filename = parts

        if filename != "plugin.json":
            error = f"❌ {plugin_path}: Filename must be 'plugin.json', got '{filename}'"
            logger.error(error)
            self.errors.append(error)
            return False

        if tier not in self.VALID_TIERS:
            error = f"❌ {plugin_path}: Invalid tier '{tier}' (must be in {self.VALID_TIERS})"
            logger.error(error)
            self.errors.append(error)
            return False

        if category not in self.VALID_CATEGORIES:
            error = f"❌ {plugin_path}: Invalid category '{category}' (must be in {self.VALID_CATEGORIES})"
            logger.error(error)
            self.errors.append(error)
            return False

        # Verify ID matches path
        expected_prefix = f"plugin:{tier}-{category}-"
        if not plugin.get("id", "").startswith(expected_prefix):
            error = f"❌ {plugin_path}: ID mismatch. Path suggests '{expected_prefix}...', got '{plugin.get('id')}'"
            logger.error(error)
            self.errors.append(error)
            return False

        logger.info(f"✅ {plugin_path}: Directory structure valid")
        return True

    def generate_index(self) -> Dict[str, Any]:
        """
        Generate the marketplace index from all discovered plugins.

        Returns:
            index: {
                "version": "2.0",
                "generated_at": "ISO-8601",
                "plugin_count": int,
                "plugins": [...],
                "by_id": {...},
                "by_category": {...}
            }
        """
        logger.info("=" * 60)
        logger.info("Marketplace Index Generation v2 (ADR-0511)")
        logger.info("=" * 60)

        # Discover
        plugin_paths = self.discover_plugins()

        if not plugin_paths:
            logger.warning("⚠️  No plugins discovered")

        # Validate each
        for plugin_path in plugin_paths:
            plugin = self.validate_plugin(plugin_path)
            if plugin and self.verify_directory_structure(plugin_path, plugin):
                self.plugins.append(plugin)

        # Build index
        index = {
            "version": "2.0",
            "schema": "ADR-0511",
            "generated_at": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
            "plugin_count": len(self.plugins),
            "plugins": self.plugins,
            "by_id": {p["id"]: p for p in self.plugins},
            "by_category": self._group_by_category(),
            "by_tier": self._group_by_tier(),
        }

        # Validate index schema
        try:
            jsonschema.validate(index, self.index_schema)
            logger.info("✅ Index schema validation passed")
        except jsonschema.ValidationError as e:
            error = f"❌ Index schema validation failed: {e.message}"
            logger.error(error)
            self.errors.append(error)

        logger.info("=" * 60)
        if self.errors:
            logger.error(f"⚠️  {len(self.errors)} error(s) during generation")
            for err in self.errors:
                logger.error(err)
        else:
            logger.info(f"✅ Generated index with {len(self.plugins)} plugins")
        logger.info("=" * 60)

        return index

    def _group_by_category(self) -> Dict[str, List[Dict]]:
        """Group plugins by category."""
        by_cat = {}
        for plugin in self.plugins:
            cat = plugin.get("category", "unknown")
            if cat not in by_cat:
                by_cat[cat] = []
            by_cat[cat].append(plugin)
        return by_cat

    def _group_by_tier(self) -> Dict[str, List[Dict]]:
        """Group plugins by tier (buildin/contributor)."""
        by_tier = {}
        for plugin in self.plugins:
            tier = plugin.get("tier", "unknown")
            if tier not in by_tier:
                by_tier[tier] = []
            by_tier[tier].append(plugin)
        return by_tier


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate Marketplace Index (ADR-0511)"
    )
    parser.add_argument(
        "--marketplace",
        type=Path,
        default=Path.cwd() / "operator/marketplace",
        help="Path to marketplace root (default: ./operator/marketplace)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output path for index.json (default: {marketplace}/index/plugins.json)",
    )
    parser.add_argument(
        "--plugin-schema",
        type=Path,
        default=None,
        help="Path to plugin schema (default: {marketplace}/plugin-schema.json)",
    )
    parser.add_argument(
        "--index-schema",
        type=Path,
        default=None,
        help="Path to index schema (default: {marketplace}/index-schema.json)",
    )

    args = parser.parse_args()

    marketplace_root = args.marketplace.resolve()
    output_path = args.output or (marketplace_root / "index" / "plugins.json")

    generator = MarketplaceIndexGenerator(
        marketplace_root,
        plugin_schema_path=args.plugin_schema,
        index_schema_path=args.index_schema,
    )

    index = generator.generate_index()

    # Write output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(index, f, indent=2)

    logger.info(f"✅ Index written to {output_path}")

    # Exit with error code if there were errors
    sys.exit(1 if generator.errors else 0)


if __name__ == "__main__":
    main()
