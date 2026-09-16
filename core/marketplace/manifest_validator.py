"""Plugin manifest validation — backward-compatible schema checker.

Fixes F23: Validates plugin.json with schema version awareness.
Old plugins (no schema_version) → v1 compat
New plugins (schema_version: "2.0") → v2 validation

Loss-Signal: Provides introspection for which plugins fail + why.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List


@dataclass
class ManifestError:
    """Validation error with plugin context."""
    plugin_id: str
    plugin_path: Path
    error_type: str  # "schema_mismatch", "missing_field", "invalid_type"
    message: str
    schema_version: Optional[str] = None


class ManifestValidator:
    """Validate plugin.json manifests with backward compatibility."""

    # Schema versions and their required fields
    SCHEMA_V1 = {
        "required": ["id", "name", "version"],
        "optional": ["description", "author", "license"]
    }

    SCHEMA_V2 = {
        "required": ["id", "name", "version", "schema_version"],
        "optional": ["description", "author", "license", "dependencies", "tts_engine", "config"]
    }

    @staticmethod
    def detect_schema_version(manifest: dict) -> str:
        """Detect schema version from manifest (default v1 for legacy)."""
        return manifest.get("schema_version", "1.0")

    @staticmethod
    def validate_manifest(plugin_path: Path) -> tuple[bool, Optional[ManifestError]]:
        """
        Validate a single plugin.json file.

        Returns:
            (is_valid, error_if_any)
        """
        plugin_json = plugin_path / "plugin.json"

        if not plugin_json.exists():
            return False, ManifestError(
                plugin_id=plugin_path.name,
                plugin_path=plugin_path,
                error_type="missing_file",
                message=f"plugin.json not found at {plugin_json}"
            )

        try:
            with open(plugin_json) as f:
                manifest = json.load(f)
        except json.JSONDecodeError as e:
            return False, ManifestError(
                plugin_id=plugin_path.name,
                plugin_path=plugin_path,
                error_type="json_parse",
                message=f"Invalid JSON: {str(e)}"
            )

        schema_version = ManifestValidator.detect_schema_version(manifest)

        # Choose schema based on version
        schema = ManifestValidator.SCHEMA_V2 if schema_version.startswith("2") else ManifestValidator.SCHEMA_V1

        # Check required fields
        for field in schema["required"]:
            if field not in manifest:
                return False, ManifestError(
                    plugin_id=manifest.get("id", plugin_path.name),
                    plugin_path=plugin_path,
                    error_type="missing_field",
                    message=f"Required field missing: {field}",
                    schema_version=schema_version
                )

        # Type checking for critical fields
        if not isinstance(manifest.get("id"), str):
            return False, ManifestError(
                plugin_id=str(manifest.get("id")),
                plugin_path=plugin_path,
                error_type="invalid_type",
                message="id must be string",
                schema_version=schema_version
            )

        # Validation passed
        return True, None

    @staticmethod
    def validate_all_plugins(marketplace_root: Path) -> tuple[int, List[ManifestError]]:
        """
        Validate all plugins in marketplace.
        Recursively finds all plugin.json files.

        Returns:
            (valid_count, errors)
        """
        valid_count = 0
        errors = []

        plugins_dir = marketplace_root / "plugins"
        if not plugins_dir.exists():
            return 0, [ManifestError(
                plugin_id="marketplace",
                plugin_path=marketplace_root,
                error_type="missing_dir",
                message=f"plugins/ directory not found"
            )]

        # Recursively find all plugin.json files
        # Structure: plugins/[buildin|contributor]/[category]/[plugin_name]/plugin.json
        for plugin_json_path in plugins_dir.rglob("plugin.json"):
            plugin_dir = plugin_json_path.parent

            is_valid, error = ManifestValidator.validate_manifest(plugin_dir)

            if is_valid:
                valid_count += 1
            else:
                errors.append(error)

        return valid_count, errors


def validate_and_report(marketplace_root: Path) -> bool:
    """
    Validate all manifests and print a detailed report.

    Returns True if all valid, False if any errors.
    """
    valid_count, errors = ManifestValidator.validate_all_plugins(marketplace_root)

    print(f"\n✅ Valid manifests: {valid_count}")
    print(f"❌ Invalid manifests: {len(errors)}\n")

    if errors:
        print("Errors (by type):")
        by_type = {}
        for err in errors:
            if err.error_type not in by_type:
                by_type[err.error_type] = []
            by_type[err.error_type].append(err)

        for error_type, type_errors in sorted(by_type.items()):
            print(f"\n  {error_type.upper()} ({len(type_errors)} plugins):")
            for err in type_errors[:5]:  # Show first 5
                print(f"    - {err.plugin_id}: {err.message}")
                if err.schema_version:
                    print(f"      (schema v{err.schema_version})")
            if len(type_errors) > 5:
                print(f"    ... and {len(type_errors) - 5} more")

    return len(errors) == 0


if __name__ == "__main__":
    import sys
    marketplace_root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    success = validate_and_report(marketplace_root)
    sys.exit(0 if success else 1)
