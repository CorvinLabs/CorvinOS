"""
Plugin Discovery Scanner — Auto-Index Plugins & Maintain Test Inventory

Walks plugin directories, extracts metadata, generates test_inventory.json,
and provides automated test-generation scaffolding.
"""

import json
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional, Set
from enum import Enum


class BootLayer(str, Enum):
    """Plugin boot layer classification"""
    COMPLIANCE = "compliance"
    CORE = "core"
    BUNDLED = "bundled"
    INSTALLED = "installed"


class Origin(str, Enum):
    """Plugin origin classification"""
    BUILDIN = "buildin"
    VETTED = "vetted"
    COMMUNITY = "community"


@dataclass
class PluginMetadata:
    """Extracted plugin metadata from manifest"""
    plugin_id: str
    version: str
    plugin_type: str
    display_name: str
    description: str
    entry_point: str
    dependencies: List[str]
    requires_api_version: str
    boot_layer: str
    origin: str
    manifest_path: Path
    plugin_dir: Path

    def to_dict(self) -> Dict:
        """Convert to dict (with Path → str for JSON serialization)"""
        d = asdict(self)
        d["manifest_path"] = str(d["manifest_path"])
        d["plugin_dir"] = str(d["plugin_dir"])
        return d


@dataclass
class TestInventory:
    """Test inventory for all discovered plugins"""
    discovered_at: str
    plugin_count: int
    plugins: Dict[str, Dict]  # plugin_id → metadata + test requirements
    gaps: Dict[str, List[str]]  # plugin_id → missing test categories
    total_coverage: float  # % of plugins with full test checklist


class PluginScanner:
    """Discover plugins, extract metadata, generate test inventory"""

    def __init__(self, plugin_dirs: Optional[List[Path]] = None):
        """
        Initialize scanner with plugin directories to search.

        Args:
            plugin_dirs: List of directories to scan for plugins.
                        Defaults to standard locations if None.
        """
        if plugin_dirs is None:
            plugin_dirs = [
                Path("core/plugins/corvin_plugins"),
                Path("operator/marketplace/plugins/buildin"),
                Path("operator/marketplace/plugins/contributor"),
            ]
        self.plugin_dirs = [Path(d) for d in plugin_dirs]
        self.discovered_plugins: Dict[str, PluginMetadata] = {}
        self.test_requirements: Dict[str, Set[str]] = {}

    def scan(self) -> Dict[str, PluginMetadata]:
        """
        Scan all plugin directories and extract metadata.

        Returns:
            Dictionary of plugin_id → PluginMetadata
        """
        for plugin_dir in self.plugin_dirs:
            if not plugin_dir.exists():
                print(f"⚠ Plugin directory not found: {plugin_dir}")
                continue

            # Walk directory looking for manifest.json files
            for manifest_path in plugin_dir.rglob("manifest.json"):
                try:
                    metadata = self._extract_metadata(manifest_path)
                    self.discovered_plugins[metadata.plugin_id] = metadata
                    print(f"✓ Discovered: {metadata.plugin_id} @ {manifest_path}")
                except Exception as e:
                    print(f"✗ Failed to parse {manifest_path}: {e}")

        return self.discovered_plugins

    def _extract_metadata(self, manifest_path: Path) -> PluginMetadata:
        """Parse manifest.json and extract metadata"""
        manifest = json.loads(manifest_path.read_text())

        required_fields = [
            "plugin_id", "version", "plugin_type", "display_name",
            "entry_point", "requires_api_version"
        ]
        for field in required_fields:
            if field not in manifest:
                raise ValueError(f"Missing required field: {field}")

        return PluginMetadata(
            plugin_id=manifest["plugin_id"],
            version=manifest["version"],
            plugin_type=manifest["plugin_type"],
            display_name=manifest["display_name"],
            description=manifest.get("description", ""),
            entry_point=manifest["entry_point"],
            dependencies=manifest.get("dependencies", []),
            requires_api_version=manifest["requires_api_version"],
            boot_layer=manifest.get("boot_layer", "installed"),
            origin=manifest.get("origin", "community"),
            manifest_path=manifest_path,
            plugin_dir=manifest_path.parent,
        )

    def get_test_requirements(self) -> Dict[str, Set[str]]:
        """
        Generate per-plugin test requirements based on boot_layer and origin.

        Returns:
            Dict of plugin_id → set of mandatory test categories
        """
        for plugin_id, metadata in self.discovered_plugins.items():
            requirements = set()

            # Every plugin needs these
            requirements.update([
                "test_init_lifecycle",
                "test_features",
                "test_hooks",
                "test_integration",
                "test_cleanup",
            ])

            # High-risk plugins (compliance, core) need extra tests
            if metadata.boot_layer in [BootLayer.COMPLIANCE, BootLayer.CORE]:
                requirements.update([
                    "test_load_order",
                    "test_hot_reload",
                    "test_fault_injection",
                ])

            # Third-party plugins need isolation tests
            if metadata.origin == Origin.COMMUNITY:
                requirements.update([
                    "test_sandbox",
                    "test_resource_limits",
                ])

            self.test_requirements[plugin_id] = requirements

        return self.test_requirements

    def get_test_gaps(self) -> Dict[str, List[str]]:
        """
        Compare discovered plugins against existing test files.

        Returns:
            Dict of plugin_id → list of missing test categories
        """
        gaps = {}
        test_requirements = self.get_test_requirements()
        existing_tests = self._scan_existing_tests()

        for plugin_id, required in test_requirements.items():
            existing = existing_tests.get(plugin_id, set())
            missing = required - existing
            if missing:
                gaps[plugin_id] = sorted(list(missing))

        return gaps

    def _scan_existing_tests(self) -> Dict[str, Set[str]]:
        """Scan test directories for existing test files"""
        existing = {}
        test_root = Path("tests")

        if not test_root.exists():
            return existing

        for test_file in test_root.rglob("test_*.py"):
            # Extract plugin_id from test filename or content
            # Simple heuristic: test_<plugin_id>_*.py
            if "__pycache__" in str(test_file):
                continue

            parts = test_file.stem.split("_")
            if len(parts) >= 3 and parts[0] == "test":
                plugin_id = parts[1]
                test_category = "_".join(parts[2:])
                if plugin_id not in existing:
                    existing[plugin_id] = set()
                existing[plugin_id].add(test_category)

        return existing

    def generate_inventory_json(self, output_path: Path) -> TestInventory:
        """
        Generate test_inventory.json with all metadata, requirements, and gaps.

        Args:
            output_path: Where to write test_inventory.json

        Returns:
            TestInventory object
        """
        test_reqs = self.get_test_requirements()
        gaps = self.get_test_gaps()

        # Calculate coverage %
        total_plugins = len(self.discovered_plugins)
        covered = sum(1 for gap_list in gaps.values() if not gap_list)
        coverage = (covered / total_plugins * 100) if total_plugins > 0 else 0

        inventory_data = {
            "discovered_at": str(Path.cwd()),
            "plugin_count": total_plugins,
            "plugins": {},
            "gaps": gaps,
            "total_coverage": coverage,
        }

        for plugin_id, metadata in self.discovered_plugins.items():
            inventory_data["plugins"][plugin_id] = {
                **metadata.to_dict(),
                "test_requirements": sorted(list(test_reqs[plugin_id])),
                "test_gaps": gaps.get(plugin_id, []),
                "status": "✓ COMPLETE" if not gaps.get(plugin_id) else "⚠ INCOMPLETE",
            }

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(inventory_data, indent=2))

        print(f"✓ Generated: {output_path}")
        print(f"  Plugins discovered: {total_plugins}")
        print(f"  Coverage: {coverage:.1f}%")
        print(f"  Gaps: {sum(len(g) for g in gaps.values())} tests needed")

        return TestInventory(
            discovered_at=str(Path.cwd()),
            plugin_count=total_plugins,
            plugins=inventory_data["plugins"],
            gaps=gaps,
            total_coverage=coverage,
        )


def main():
    """CLI entry point: `python plugin_scanner.py`"""
    scanner = PluginScanner()
    plugins = scanner.scan()

    print(f"\nDiscovered {len(plugins)} plugins:")
    for plugin_id in sorted(plugins.keys()):
        print(f"  - {plugin_id}")

    inventory = scanner.generate_inventory_json(
        Path("tests/e2e/plugin_verification/test_inventory.json")
    )

    # Print coverage summary
    gaps_total = sum(len(g) for g in inventory.gaps.values())
    if gaps_total > 0:
        print(f"\n⚠ Missing tests (total: {gaps_total}):")
        for plugin_id, gap_list in sorted(inventory.gaps.items()):
            if gap_list:
                print(f"  {plugin_id}: {', '.join(gap_list)}")
    else:
        print("\n✓ All plugins have complete test coverage!")

    return 0 if inventory.total_coverage >= 95.0 else 1


if __name__ == "__main__":
    sys.exit(main())
