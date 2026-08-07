"""Package validation for ZIP archives and manifests (ADR-0268 Phase 1-3)."""
from __future__ import annotations

import json
import logging
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jsonschema

logger = logging.getLogger(__name__)
import yaml


@dataclass
class ValidationError(Exception):
    """Raised when package validation fails."""

    message: str
    field: str | None = None
    details: dict[str, Any] | None = None


MANIFEST_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "required": ["name"],
    "properties": {
        "id": {
            "type": "string",
            "pattern": "^[a-zA-Z0-9._-]+$",
            "description": "Unique package identifier (reverse-domain format)",
        },
        "version": {
            "type": "string",
            "description": "Version string (semantic versioning preferred: X.Y.Z[-prerelease])",
        },
        "name": {
            "type": "string",
            "minLength": 1,
            "description": "Package display name (required)",
        },
        "display_name": {
            "type": "string",
            "description": "Human-readable package name for UI",
        },
        "corvinOS": {
            "type": "object",
            "properties": {
                "min_version": {"type": "string"},
                "max_version": {"type": "string"},
            },
            "description": "CorvinOS compatibility constraints",
        },
        "permissions": {
            "type": "array",
            "items": {"type": "string"},
            "description": "List of required permissions (e.g., audit:write, storage:read)",
        },
        "dependencies": {
            "oneOf": [
                {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["id", "version"],
                        "properties": {
                            "id": {"type": "string"},
                            "version": {"type": "string"},
                        },
                        "additionalProperties": False,
                    },
                    "description": "ADR-0268 format: array of {id, version}",
                },
                {
                    "type": "object",
                    "description": "Skill 2.0 format: dict of module files",
                },
            ],
            "description": "Package dependencies (ADR-0268 array or Skill 2.0 dict)",
        },
        "contents": {
            "type": "object",
            "properties": {
                "skills": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "file": {"type": "string"},
                        },
                        "required": ["id", "file"],
                    },
                },
                "hooks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "file": {"type": "string"},
                            "trigger": {"type": "string"},
                            "priority": {"type": "integer"},
                            "function": {"type": "string"},
                        },
                        "required": ["id", "file", "trigger"],
                    },
                },
                "plugins": {"type": "array"},
                "routes": {"type": "array"},
            },
            "description": "Package contents manifest",
        },
        "capabilities": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Declared capabilities provided by package",
        },
        "exports": {
            "type": ["object", "null"],
            "description": "Public classes/functions exported by package",
        },
        "configuration": {
            "type": "object",
            "properties": {
                "required": {"type": "array", "items": {"type": "string"}},
                "optional": {"type": "array", "items": {"type": "string"}},
            },
            "description": "Configuration schema for package",
        },
        "author": {
            "type": "string",
            "description": "Package author name",
        },
        "license": {
            "type": "string",
            "description": "License identifier (e.g., MIT, Apache-2.0)",
        },
        "metadata": {
            "type": "object",
            "description": "Additional metadata",
        },
        "supported_models": {
            "type": "array",
            "items": {"type": "string"},
            "description": "List of supported model IDs",
        },
        "entry_point": {
            "type": "string",
            "description": "Python entry point (module:class)",
        },
        "hooks": {
            "type": "object",
            "description": "Hooks registered by package",
        },
        "signing": {
            "type": "object",
            "properties": {
                "key_id": {"type": "string"},
                "algorithm": {"type": "string"},
                "signature": {"type": "string"},
            },
            "description": "Cryptographic signature (RSA-2048)",
        },
    },
    "additionalProperties": True,
}

SKILL_DEFINITION_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "required": ["id", "name"],
    "properties": {
        "id": {
            "type": "string",
            "pattern": "^[a-zA-Z0-9_.-]+$",
            "description": "Unique skill identifier",
        },
        "name": {
            "type": "string",
            "minLength": 1,
            "description": "Human-readable skill name",
        },
        "description": {
            "type": "string",
            "description": "Detailed skill description",
        },
        "version": {
            "type": "string",
            "description": "Semantic version",
        },
        "category": {
            "type": "string",
            "description": "Skill category (e.g., 'productivity', 'data')",
        },
        "tags": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Searchable tags",
        },
        "hooks": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "trigger", "file", "function"],
                "properties": {
                    "id": {"type": "string"},
                    "trigger": {
                        "type": "string",
                        "enum": [
                            "preprocessing",
                            "on_error",
                            "on_complete",
                            "on_artifact",
                            "on_config_change",
                            "on_audit_event",
                        ],
                    },
                    "priority": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 1000,
                    },
                    "file": {"type": "string"},
                    "function": {"type": "string"},
                },
                "additionalProperties": False,
            },
            "description": "Hooks declared by skill",
        },
        "entry_point": {
            "type": "string",
            "description": "Python entry point (module:class)",
        },
        "capabilities": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Declared capabilities",
        },
        "permissions": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Required permissions",
        },
    },
    "additionalProperties": True,
}


class PackageValidator:
    """Validates skill packages (ZIP + manifest)."""

    @staticmethod
    def validate_zip_integrity(zip_path: str | Path) -> dict[str, Any]:
        """
        Validate ZIP structure and return parsed manifest.

        Raises ValidationError if ZIP is malformed or manifest missing/invalid.
        """
        zip_path = Path(zip_path)
        if not zip_path.exists():
            raise ValidationError(f"ZIP file not found: {zip_path}", field="zip_path")

        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                file_list = zf.namelist()
                if "manifest.json" not in file_list:
                    raise ValidationError(
                        "manifest.json not found in ZIP", field="manifest.json"
                    )

                manifest_text = zf.read("manifest.json").decode("utf-8")
                manifest = json.loads(manifest_text)
                return manifest
        except zipfile.BadZipFile as e:
            raise ValidationError(f"ZIP archive corrupted: {e}", field="zip") from e
        except json.JSONDecodeError as e:
            raise ValidationError(
                f"manifest.json invalid JSON: {e}", field="manifest.json"
            ) from e

    @staticmethod
    def validate_manifest_schema(manifest: dict[str, Any]) -> None:
        """
        Validate manifest against JSON schema.

        Supports both strict ADR-0268 format and flexible Skill 2.0 format.

        Raises ValidationError if manifest does not conform to schema.
        """
        try:
            jsonschema.validate(instance=manifest, schema=MANIFEST_SCHEMA)
        except jsonschema.ValidationError as e:
            # Extract field name from path if available
            field_name = None
            if e.path:
                field_name = e.path[-1]

            # Improve error message for common cases
            error_msg = e.message
            if "is not of type" in error_msg and field_name:
                # Transform "X is not of type 'string'" to "Field 'X' must be string"
                type_part = error_msg.split("not of type")[1].strip().strip("'\"")
                error_msg = f"Field '{field_name}' must be {type_part}"

            raise ValidationError(
                f"Manifest validation failed: {error_msg}",
                field=field_name,
                details={
                    "error": e.message,
                    "path": list(e.path) if e.path else [],
                    "schema": e.schema,
                },
            ) from e
        except jsonschema.SchemaError as e:
            raise ValidationError(
                f"Schema validation error: {e.message}",
                field=None,
                details={"error": e.message},
            ) from e

    @staticmethod
    def validate_dependencies(
        manifest: dict[str, Any],
        installed_packages: dict[str, str],
    ) -> list[str]:
        """
        Validate dependencies are installed and versions compatible.

        Supports both ADR-0268 array format and Skill 2.0 dict format.

        Args:
            manifest: Package manifest
            installed_packages: Dict of {package_id: version} for all installed packages

        Returns:
            List of unmet dependencies (empty if all satisfied)

        Raises ValidationError if version constraint is invalid.
        """
        unmet = []
        deps = manifest.get("dependencies", [])

        # Handle dict format (Skill 2.0: {"module_name": ["file1", "file2"]})
        if isinstance(deps, dict):
            # For Skill 2.0 format, dependencies are just file listings
            # Skip version validation in this case
            return unmet

        # Handle array format (ADR-0268: [{"id": "...", "version": "..."}])
        if not isinstance(deps, list):
            return unmet

        for dep in deps:
            if not isinstance(dep, dict):
                continue

            dep_id = dep.get("id")
            if not dep_id:
                continue

            version_constraint = dep.get("version", ">=0.0.0")

            if dep_id not in installed_packages:
                unmet.append(f"{dep_id} (not installed)")
                continue

            installed_version = installed_packages[dep_id]
            if not PackageValidator._check_version_constraint(
                installed_version, version_constraint
            ):
                unmet.append(
                    f"{dep_id} (installed: {installed_version}, required: {version_constraint})"
                )

        if unmet:
            raise ValidationError(
                f"Unmet dependencies: {', '.join(unmet)}",
                field="dependencies",
                details={"unmet": unmet},
            )

        return unmet

    @staticmethod
    def _check_version_constraint(installed: str, constraint: str) -> bool:
        """Check if installed version satisfies constraint (simple semantic versioning)."""
        # Parse versions: supports >=1.0.0, =1.0.0, 1.0.0, etc.
        constraint = constraint.strip()
        op = ""
        if constraint.startswith(">="):
            op = ">="
            version = constraint[2:]
        elif constraint.startswith("="):
            op = "="
            version = constraint[1:]
        elif constraint.startswith(">"):
            op = ">"
            version = constraint[1:]
        else:
            op = "="
            version = constraint

        try:
            installed_tuple = tuple(map(int, installed.split(".")))
            version_tuple = tuple(map(int, version.split(".")))
        except (ValueError, AttributeError) as e:
            raise ValidationError(
                f"Invalid version format: {installed} or {version}", field="version"
            ) from e

        if op == ">=":
            return installed_tuple >= version_tuple
        elif op == ">":
            return installed_tuple > version_tuple
        elif op == "=":
            return installed_tuple == version_tuple
        else:
            return False

    @staticmethod
    def validate_permissions(manifest: dict[str, Any]) -> list[str]:
        """Extract and validate permissions list."""
        permissions = manifest.get("permissions", [])
        if not isinstance(permissions, list):
            raise ValidationError(
                "permissions must be a list", field="permissions"
            )
        return permissions

    @staticmethod
    def validate_skill_definitions(
        zip_path: str | Path, manifest: dict[str, Any] | None = None
    ) -> dict[str, dict[str, Any]]:
        """
        Parse and validate skill YAML files in package.

        Extracts skills from skills/ directory in ZIP, validates each against
        schema, and validates any declared hooks.

        Args:
            zip_path: Path to ZIP package
            manifest: Optional pre-parsed manifest (fetched if not provided)

        Returns:
            Dict mapping skill_id → validated skill definition

        Raises:
            ValidationError if any skill is malformed or hooks invalid
        """
        zip_path = Path(zip_path)

        if manifest is None:
            manifest = PackageValidator.validate_zip_integrity(zip_path)

        skills = {}

        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                file_list = zf.namelist()

                # Find all YAML files in skills/ directory
                skill_files = [f for f in file_list if f.startswith("skills/") and f.endswith(".yaml")]

                if not skill_files:
                    # It's OK to have no skills (package might only have plugins/hooks)
                    return skills

                for skill_file in skill_files:
                    try:
                        # Read and parse YAML skill file
                        yaml_content = zf.read(skill_file).decode("utf-8")
                        skill_def = yaml.safe_load(yaml_content)

                        if not isinstance(skill_def, dict):
                            raise ValidationError(
                                f"Skill {skill_file} is not a valid YAML object",
                                field=skill_file,
                            )

                        # Validate against skill schema
                        try:
                            jsonschema.validate(
                                instance=skill_def, schema=SKILL_DEFINITION_SCHEMA
                            )
                        except jsonschema.ValidationError as e:
                            raise ValidationError(
                                f"Skill {skill_file} validation failed: {e.message}",
                                field=skill_file,
                                details={
                                    "file": skill_file,
                                    "error": e.message,
                                    "path": list(e.path),
                                },
                            ) from e

                        skill_id = skill_def.get("id")
                        if not skill_id:
                            raise ValidationError(
                                f"Skill in {skill_file} missing required 'id' field",
                                field=skill_file,
                            )

                        # Validate hooks if present
                        hooks = skill_def.get("hooks", [])
                        if hooks:
                            PackageValidator._validate_skill_hooks(
                                skill_id, hooks, zip_path
                            )

                        skills[skill_id] = skill_def

                    except yaml.YAMLError as e:
                        raise ValidationError(
                            f"Skill {skill_file} contains invalid YAML: {e}",
                            field=skill_file,
                        ) from e

        except zipfile.BadZipFile as e:
            raise ValidationError(f"ZIP archive corrupted: {e}", field="zip") from e

        return skills

    @staticmethod
    def _validate_skill_hooks(
        skill_id: str, hooks: list[dict[str, Any]], zip_path: Path
    ) -> None:
        """
        Validate hooks declared in a skill.

        Checks:
        - Hook has required fields (id, trigger, file, function)
        - Hook file exists in ZIP
        - Hook function is a valid Python identifier

        Args:
            skill_id: Parent skill ID (for error messages)
            hooks: List of hook definitions
            zip_path: Path to ZIP package

        Raises:
            ValidationError if any hook is invalid
        """
        if not isinstance(hooks, list):
            raise ValidationError(
                f"Skill {skill_id} 'hooks' must be a list",
                field="hooks",
            )

        with zipfile.ZipFile(zip_path, "r") as zf:
            file_list = zf.namelist()

            for hook_idx, hook in enumerate(hooks):
                if not isinstance(hook, dict):
                    raise ValidationError(
                        f"Skill {skill_id} hook #{hook_idx} is not a dict",
                        field="hooks",
                    )

                # Check required fields
                for required_field in ["id", "trigger", "file", "function"]:
                    if required_field not in hook:
                        raise ValidationError(
                            f"Skill {skill_id} hook #{hook_idx} missing '{required_field}'",
                            field="hooks",
                            details={"hook_index": hook_idx, "missing_field": required_field},
                        )

                hook_id = hook["id"]
                hook_file = hook["file"]
                hook_trigger = hook["trigger"]
                hook_function = hook["function"]

                # Validate trigger is known type
                valid_triggers = {
                    "preprocessing",
                    "on_error",
                    "on_complete",
                    "on_artifact",
                    "on_config_change",
                    "on_audit_event",
                }
                if hook_trigger not in valid_triggers:
                    raise ValidationError(
                        f"Skill {skill_id} hook '{hook_id}' has invalid trigger '{hook_trigger}'",
                        field="hooks",
                        details={
                            "hook_id": hook_id,
                            "trigger": hook_trigger,
                            "valid_triggers": list(valid_triggers),
                        },
                    )

                # Validate priority if present
                if "priority" in hook:
                    priority = hook["priority"]
                    if not isinstance(priority, int) or not (0 <= priority <= 1000):
                        raise ValidationError(
                            f"Skill {skill_id} hook '{hook_id}' priority must be int 0-1000",
                            field="hooks",
                            details={"hook_id": hook_id, "priority": priority},
                        )

                # Validate hook file exists (relative to skills/)
                skill_dir = Path("skills")
                hook_path_normalized = str(skill_dir / hook_file).replace("\\", "/")
                if hook_path_normalized not in file_list:
                    # Try without skills/ prefix in case it's relative to root
                    if hook_file not in file_list:
                        raise ValidationError(
                            f"Skill {skill_id} hook '{hook_id}' references missing file '{hook_file}'",
                            field="hooks",
                            details={"hook_id": hook_id, "file": hook_file},
                        )

                # Validate function is valid Python identifier
                if not hook_function.isidentifier():
                    raise ValidationError(
                        f"Skill {skill_id} hook '{hook_id}' function name '{hook_function}' is not valid Python identifier",
                        field="hooks",
                        details={"hook_id": hook_id, "function": hook_function},
                    )
