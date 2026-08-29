"""
Manifest Validation for Plugin Marketplace (Phase 4).

Implements JSON Schema validation with fail-closed semantics.
Every manifest must pass validation before registration.

ADR-0385 Phase 4: Security Hardening
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Any
import re

try:
    import jsonschema
    JSONSCHEMA_AVAILABLE = True
except ImportError:
    JSONSCHEMA_AVAILABLE = False

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    """Result of manifest validation."""
    is_valid: bool
    errors: List[str]
    warnings: List[str] = None

    def __post_init__(self):
        if self.warnings is None:
            self.warnings = []


class ManifestValidationError(Exception):
    """Raised when manifest validation fails."""
    def __init__(self, errors: List[str]):
        self.errors = errors
        super().__init__(f"Manifest validation failed: {'; '.join(errors)}")


class ManifestValidator:
    """Validates plugin manifests against schema."""

    def __init__(self):
        self.schema = self._load_schema()
        self.errors = []
        self.warnings = []

    def _load_schema(self) -> Dict[str, Any]:
        """Load the JSON Schema from disk."""
        schema_path = Path(__file__).parent / "plugin-manifest-schema.json"
        if not schema_path.exists():
            logger.warning(f"Schema not found at {schema_path}, using minimal schema")
            return self._minimal_schema()

        try:
            with open(schema_path, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load schema: {e}, using minimal schema")
            return self._minimal_schema()

    def _minimal_schema(self) -> Dict[str, Any]:
        """Fallback minimal schema for offline operation."""
        return {
            "type": "object",
            "required": ["id", "name", "version", "author", "license", "description"],
        }

    def validate(self, manifest: Dict[str, Any]) -> ValidationResult:
        """
        Validate manifest against schema.

        Args:
            manifest: Plugin manifest dictionary (from manifest.yaml)

        Returns:
            ValidationResult with is_valid, errors, warnings

        Raises:
            ManifestValidationError if validation fails (fail-closed)
        """
        self.errors = []
        self.warnings = []

        # Step 1: Required fields check
        self._check_required_fields(manifest)

        # Step 2: Type checks
        self._check_types(manifest)

        # Step 3: Semantic validation
        self._check_semantic_constraints(manifest)

        # Step 4: Dependency/version validation
        self._check_dependencies(manifest)

        # Step 5: Boot layer constraints
        self._check_boot_layer_constraints(manifest)

        # Step 6: JSON Schema validation (if available)
        if JSONSCHEMA_AVAILABLE:
            self._check_jsonschema(manifest)

        result = ValidationResult(
            is_valid=len(self.errors) == 0,
            errors=self.errors,
            warnings=self.warnings,
        )

        if not result.is_valid:
            raise ManifestValidationError(result.errors)

        return result

    def _check_required_fields(self, manifest: Dict[str, Any]) -> None:
        """Check that required fields are present."""
        required = ["id", "name", "version", "author", "license", "description", "category"]
        for field in required:
            if field not in manifest:
                self.errors.append(f"Required field missing: {field}")

    def _check_types(self, manifest: Dict[str, Any]) -> None:
        """Check field types."""
        if "id" in manifest and not isinstance(manifest["id"], str):
            self.errors.append("Field 'id' must be a string")

        if "version" in manifest and not isinstance(manifest["version"], str):
            self.errors.append("Field 'version' must be a string")

        if "author" in manifest and not isinstance(manifest["author"], dict):
            self.errors.append("Field 'author' must be a dictionary")
        elif "author" in manifest:
            if "name" not in manifest["author"]:
                self.errors.append("Field 'author.name' is required")
            if "email" not in manifest["author"]:
                self.errors.append("Field 'author.email' is required")

        if "dependencies" in manifest and not isinstance(manifest["dependencies"], dict):
            self.errors.append("Field 'dependencies' must be a dictionary")

        if "conflicts_with" in manifest and not isinstance(manifest["conflicts_with"], list):
            self.errors.append("Field 'conflicts_with' must be a list")

        if "required_permissions" in manifest and not isinstance(manifest["required_permissions"], list):
            self.errors.append("Field 'required_permissions' must be a list")

    def _check_semantic_constraints(self, manifest: Dict[str, Any]) -> None:
        """Check semantic constraints (ranges, patterns)."""
        # ID pattern: lowercase, hyphens only
        if "id" in manifest:
            plugin_id = manifest["id"]
            if not re.match(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$", plugin_id):
                self.errors.append(
                    f"Field 'id' must be lowercase alphanumeric with hyphens only, got: {plugin_id}"
                )

        # Version pattern: semantic versioning
        if "version" in manifest:
            version = manifest["version"]
            if not re.match(r"^\d+\.\d+\.\d+(-[a-z0-9]+(\.[a-z0-9]+)*)?$", version):
                self.errors.append(
                    f"Field 'version' must be semantic version (e.g., 1.0.0), got: {version}"
                )

        # Description length
        if "description" in manifest:
            desc = manifest["description"]
            if len(desc) < 10:
                self.errors.append("Field 'description' must be at least 10 characters")
            if len(desc) > 200:
                self.errors.append("Field 'description' must not exceed 200 characters")

        # Long description length
        if "long_description" in manifest:
            long_desc = manifest["long_description"]
            if len(long_desc) > 5000:
                self.errors.append("Field 'long_description' must not exceed 5000 characters")

        # Email validation
        if "author" in manifest and "email" in manifest["author"]:
            email = manifest["author"]["email"]
            if not re.match(r"^[^@]+@[^@]+\.[^@]+$", email):
                self.errors.append(f"Field 'author.email' is not a valid email: {email}")

        # Category enum
        valid_categories = [
            "Authentication", "Performance", "Security", "Database",
            "Integration", "UI", "Analytics", "Tooling"
        ]
        if "category" in manifest and manifest["category"] not in valid_categories:
            self.errors.append(
                f"Field 'category' must be one of {valid_categories}, got: {manifest['category']}"
            )

        # Boot layer enum
        if "boot_layer" in manifest:
            valid_layers = ["compliance", "core", "bundled", "installed"]
            if manifest["boot_layer"] not in valid_layers:
                self.errors.append(
                    f"Field 'boot_layer' must be one of {valid_layers}, got: {manifest['boot_layer']}"
                )

    def _check_dependencies(self, manifest: Dict[str, Any]) -> None:
        """Validate dependency specifications."""
        if "dependencies" not in manifest:
            return

        deps = manifest["dependencies"]
        for plugin_id, version_spec in deps.items():
            # Validate plugin_id format
            if not re.match(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$", plugin_id):
                self.errors.append(
                    f"Dependency plugin_id must be lowercase alphanumeric: {plugin_id}"
                )

            # Validate version spec (must be semantic version or *)
            if version_spec != "*" and not re.match(r"^\d+\.\d+\.\d+", version_spec):
                self.errors.append(
                    f"Dependency version spec must be semantic version or '*', got: {version_spec}"
                )

        # Check that conflicting plugins are not in dependencies
        conflicts = manifest.get("conflicts_with", [])
        for conflict_id in conflicts:
            if conflict_id in deps:
                self.errors.append(
                    f"Plugin cannot both depend on and conflict with: {conflict_id}"
                )

    def _check_boot_layer_constraints(self, manifest: Dict[str, Any]) -> None:
        """
        Validate boot layer constraints (ADR-0243).

        - Compliance layer: never disableable, may not be community-origin
        - Core/Bundled: may be replaceable
        - Installed: user-installed only
        """
        boot_layer = manifest.get("boot_layer", "installed")
        origin = manifest.get("origin", "builtin")

        # Compliance layer is reserved for built-in, trusted plugins only
        if boot_layer == "compliance" and origin == "community":
            self.errors.append(
                "Compliance boot_layer is reserved for vetted/builtin plugins only"
            )

        # Community plugins must be installed layer
        if origin == "community" and boot_layer != "installed":
            self.warnings.append(
                f"Community plugin declared as boot_layer={boot_layer}, "
                "will be downgraded to 'installed' at registration"
            )

    def _check_jsonschema(self, manifest: Dict[str, Any]) -> None:
        """Run JSON Schema validation if jsonschema library is available."""
        if not JSONSCHEMA_AVAILABLE:
            return

        try:
            jsonschema.validate(instance=manifest, schema=self.schema)
        except jsonschema.ValidationError as e:
            self.errors.append(f"JSON Schema validation failed: {e.message}")
        except Exception as e:
            self.errors.append(f"JSON Schema validation error: {str(e)}")

    def validate_config_schema(self, config_schema: Dict[str, Any]) -> ValidationResult:
        """
        Validate a plugin's config_schema field.

        The config_schema itself must be a valid JSON Schema.
        """
        self.errors = []
        self.warnings = []

        if not isinstance(config_schema, dict):
            self.errors.append("config_schema must be a dictionary (JSON Schema object)")
            return ValidationResult(is_valid=False, errors=self.errors)

        # Basic JSON Schema validation
        if "type" not in config_schema:
            self.warnings.append("config_schema should have a 'type' field")

        # Try to validate it against JSON Schema meta-schema (if jsonschema available)
        if JSONSCHEMA_AVAILABLE:
            try:
                jsonschema.Draft7Validator.check_schema(config_schema)
            except jsonschema.SchemaError as e:
                self.errors.append(f"config_schema is not valid JSON Schema: {e.message}")
            except Exception as e:
                self.errors.append(f"config_schema validation error: {str(e)}")

        return ValidationResult(
            is_valid=len(self.errors) == 0,
            errors=self.errors,
            warnings=self.warnings,
        )
