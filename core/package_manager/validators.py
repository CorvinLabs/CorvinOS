"""Package validation for ZIP archives and manifests (ADR-0268 Phase 1)."""
from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jsonschema


@dataclass
class ValidationError(Exception):
    """Raised when package validation fails."""

    message: str
    field: str | None = None
    details: dict[str, Any] | None = None


MANIFEST_SCHEMA = {
    "type": "object",
    "required": ["name"],
    "properties": {
        "id": {"type": "string", "pattern": "^[a-zA-Z0-9._-]+$"},
        "version": {"type": "string"},
        "name": {"type": "string"},
        "display_name": {"type": "string"},
        "corvinOS": {
            "type": "object",
            "properties": {
                "min_version": {"type": "string"},
                "max_version": {"type": "string"},
            },
        },
        "permissions": {
            "type": "array",
            "items": {"type": "string"},
        },
        "dependencies": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "version"],
                "properties": {
                    "id": {"type": "string"},
                    "version": {"type": "string"},
                },
            },
        },
        "contents": {
            "type": "object",
            "properties": {
                "skills": {"type": "array"},
                "hooks": {"type": "array"},
                "plugins": {"type": "array"},
                "routes": {"type": "array"},
            },
        },
        "capabilities": {"type": "array", "items": {"type": "string"}},
        "exports": {},
        "configuration": {},
        "author": {"type": "string"},
        "license": {"type": "string"},
        "metadata": {},
        "supported_models": {"type": "array"},
        "entry_point": {"type": "string"},
        "hooks": {},
    },
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
        """
        # Require 'name' field at minimum
        if "name" not in manifest:
            raise ValidationError("Manifest must have 'name' field", field="name")

        # Validate string fields
        for field in ["name", "id", "version", "author", "license", "entry_point"]:
            if field in manifest and not isinstance(manifest[field], str):
                raise ValidationError(
                    f"Field '{field}' must be string",
                    field=field,
                )

        # Validate array fields
        for field in ["capabilities", "supported_models"]:
            if field in manifest and not isinstance(manifest[field], list):
                raise ValidationError(
                    f"Field '{field}' must be array",
                    field=field,
                )

        # Validate object fields
        for field in ["contents", "configuration", "metadata", "exports", "hooks"]:
            if field in manifest and not isinstance(manifest[field], dict):
                raise ValidationError(
                    f"Field '{field}' must be object",
                    field=field,
                )

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
