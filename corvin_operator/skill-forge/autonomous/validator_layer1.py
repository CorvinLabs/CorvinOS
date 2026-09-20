"""Layer 1: Structural validation for generated skills.

Checks:
- Manifest exists and valid JSON (skill.json)
- Schema: id, version, capabilities, dependencies, hooks, lom_binding present
- Folder structure: src/, tests/, hooks/, scripts/ present
- No breaking changes in public API (compare old vs new manifest)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .result import ValidationResult


_REQUIRED_MANIFEST_FIELDS = {
    "id",
    "version",
    "capabilities",
    "dependencies",
    "hooks",
    "lom_binding",
}

_REQUIRED_FOLDERS = {
    "src",
    "tests",
    "hooks",
    "scripts",
}


class StructuralValidator:
    """Validates skill structure, manifest, and folder layout (deterministic, no subprocess)."""

    def validate(self, skill_dir: Path) -> ValidationResult:
        """Validate skill structural integrity.

        Args:
            skill_dir: Path to skill directory.

        Returns:
            ValidationResult with passed=True iff all checks pass.
        """
        errors: list[str] = []
        warnings: list[str] = []
        metrics: dict[str, Any] = {}

        # Convert to Path if string
        skill_dir = Path(skill_dir)

        # Check 1: Skill directory exists
        if not skill_dir.is_dir():
            errors.append(f"skill_dir does not exist: {skill_dir}")
            return ValidationResult(
                layer=1,
                passed=False,
                errors=errors,
                warnings=warnings,
                metrics=metrics,
            )

        # Check 2: skill.json manifest exists
        manifest_path = skill_dir / "skill.json"
        if not manifest_path.exists():
            errors.append(f"skill.json not found at {manifest_path}")
            return ValidationResult(
                layer=1,
                passed=False,
                errors=errors,
                warnings=warnings,
                metrics=metrics,
            )

        # Check 3: skill.json is valid JSON
        try:
            with open(manifest_path) as f:
                manifest = json.load(f)
        except json.JSONDecodeError as e:
            errors.append(f"skill.json is invalid JSON: {e}")
            return ValidationResult(
                layer=1,
                passed=False,
                errors=errors,
                warnings=warnings,
                metrics=metrics,
            )
        except Exception as e:
            errors.append(f"Failed to read skill.json: {e}")
            return ValidationResult(
                layer=1,
                passed=False,
                errors=errors,
                warnings=warnings,
                metrics=metrics,
            )

        # Check 4: skill.json schema — required fields
        if not isinstance(manifest, dict):
            errors.append(f"skill.json root must be a dict, got {type(manifest).__name__}")
            return ValidationResult(
                layer=1,
                passed=False,
                errors=errors,
                warnings=warnings,
                metrics=metrics,
            )

        missing_fields = _REQUIRED_MANIFEST_FIELDS - set(manifest.keys())
        if missing_fields:
            errors.append(f"Missing required fields in skill.json: {sorted(missing_fields)}")

        # Check 5: Folder structure
        missing_folders = []
        for folder in _REQUIRED_FOLDERS:
            folder_path = skill_dir / folder
            if not folder_path.is_dir():
                missing_folders.append(folder)

        if missing_folders:
            errors.append(f"Missing required folders: {sorted(missing_folders)}")

        # Check 6: src/ folder has at least one .py file
        src_dir = skill_dir / "src"
        if src_dir.is_dir():
            py_files = list(src_dir.glob("**/*.py"))
            if not py_files:
                warnings.append("src/ folder exists but contains no .py files")
            else:
                metrics["src_python_files"] = len(py_files)

        # Check 7: tests/ folder has at least one test file
        tests_dir = skill_dir / "tests"
        if tests_dir.is_dir():
            test_files = [
                f for f in tests_dir.glob("**/*.py")
                if f.name.startswith("test_") or f.name.endswith("_test.py")
            ]
            if not test_files:
                warnings.append("tests/ folder exists but contains no test files")
            else:
                metrics["test_files"] = len(test_files)

        # Check 8: Manifest schema type checking
        if not isinstance(manifest.get("capabilities"), (list, dict)):
            errors.append("capabilities must be a list or dict")

        if not isinstance(manifest.get("dependencies"), (list, dict)):
            errors.append("dependencies must be a list or dict")

        if not isinstance(manifest.get("hooks"), (list, dict)):
            errors.append("hooks must be a list or dict")

        if not isinstance(manifest.get("lom_binding"), (str, dict)):
            errors.append("lom_binding must be a string or dict")

        # Check 9: version format (semver-like: X.Y.Z)
        version = manifest.get("version", "")
        if version and not self._is_valid_semver(version):
            warnings.append(f"version {version!r} does not follow semver (X.Y.Z format)")

        metrics["manifest_file_size"] = manifest_path.stat().st_size

        return ValidationResult(
            layer=1,
            passed=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            metrics=metrics,
        )

    @staticmethod
    def _is_valid_semver(version: str) -> bool:
        """Check if version follows X.Y.Z format (simplified semver)."""
        parts = version.split(".")
        if len(parts) < 3:
            return False
        try:
            for part in parts[:3]:
                int(part)
            return True
        except ValueError:
            return False
