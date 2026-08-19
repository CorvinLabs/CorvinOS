"""Runtime validator for skill and tool metadata — fail-closed."""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple
from dataclasses import dataclass

import jsonschema

from core.skill_management.schema import SKILL_METADATA_SCHEMA, TOOL_METADATA_SCHEMA


@dataclass
class ValidationError:
    field: str
    error: str
    value: any = None


@dataclass
class ValidationResult:
    valid: bool
    errors: List[ValidationError]
    warnings: List[str]


class MetadataValidator:
    """Validate skill/tool metadata against schemas."""

    def __init__(self, tenant_id: str = "_default"):
        self.tenant_id = tenant_id
        self.base_path = Path.home() / ".corvin" / "tenants" / tenant_id

    def validate_skill_metadata(self, skill_id: str, scope: str = "_shared") -> ValidationResult:
        """Validate a skill's meta.json."""
        errors = []
        warnings = []

        # Load meta.json
        meta_path = self.base_path / scope / "skills" / skill_id / "meta.json"
        if not meta_path.exists():
            return ValidationResult(
                valid=False,
                errors=[ValidationError("file", f"meta.json not found at {meta_path}")],
                warnings=[]
            )

        try:
            with open(meta_path) as f:
                metadata = json.load(f)
        except json.JSONDecodeError as e:
            return ValidationResult(
                valid=False,
                errors=[ValidationError("json", f"Invalid JSON: {str(e)}")],
                warnings=[]
            )

        # Schema validation
        try:
            jsonschema.validate(metadata, SKILL_METADATA_SCHEMA)
        except jsonschema.ValidationError as e:
            errors.append(ValidationError(
                field=".".join(str(p) for p in e.absolute_path) or "root",
                error=e.message,
                value=e.instance
            ))

        # Custom validations
        custom_errors, custom_warnings = self._validate_skill_custom(metadata, skill_id, scope)
        errors.extend(custom_errors)
        warnings.extend(custom_warnings)

        return ValidationResult(
            valid=len(errors) == 0,
            errors=errors,
            warnings=warnings
        )

    def _validate_skill_custom(self, metadata: dict, skill_id: str, scope: str) -> Tuple[List[ValidationError], List[str]]:
        """Custom business logic validations."""
        errors = []
        warnings = []

        # Timestamps must be sane (created <= modified)
        created = datetime.fromisoformat(metadata.get("created", "").replace("Z", "+00:00"))
        modified = datetime.fromisoformat(metadata.get("last_modified", "").replace("Z", "+00:00"))

        if created > modified:
            errors.append(ValidationError(
                "timestamps",
                f"created ({created}) > last_modified ({modified})",
                {"created": metadata.get("created"), "last_modified": metadata.get("last_modified")}
            ))

        # Check dependencies exist (if any)
        for dep in metadata.get("dependencies", []):
            if not self._skill_exists(dep["id"], dep["scope"]):
                warnings.append(f"Dependency {dep['scope']}/{dep['id']} not found (may be lazy-loaded)")

        # Check version format
        version = metadata.get("version", "")
        if not re.match(r"^\d+\.\d+\.\d+", version):
            errors.append(ValidationError("version", f"Invalid semver: {version}"))

        # Check scope is valid
        if scope not in ["_platform", "_shared", "_local"]:
            errors.append(ValidationError("scope", f"Invalid scope: {scope}"))

        return errors, warnings

    def validate_all_skills(self, scope: str = "_shared") -> Dict[str, ValidationResult]:
        """Validate all skills in a tenant/scope."""
        results = {}

        skills_dir = self.base_path / scope / "skills"
        if not skills_dir.exists():
            return results

        for skill_dir in skills_dir.iterdir():
            if skill_dir.is_dir() and not skill_dir.name.startswith('.'):
                skill_id = skill_dir.name
                results[skill_id] = self.validate_skill_metadata(skill_id, scope)

        return results

    def validate_skill_exports(self, skill_id: str, scope: str = "_shared") -> ValidationResult:
        """Validate that exported skills are valid."""
        meta_result = self.validate_skill_metadata(skill_id, scope)

        if not meta_result.valid:
            return ValidationResult(
                valid=False,
                errors=meta_result.errors + [ValidationError("export", "Cannot export invalid skill")],
                warnings=meta_result.warnings
            )

        # Exported skills must have all deps in _shared (not _local or _platform dependencies)
        meta_path = self.base_path / scope / "skills" / skill_id / "meta.json"
        with open(meta_path) as f:
            metadata = json.load(f)

        errors = []
        for dep in metadata.get("dependencies", []):
            if dep["scope"] == "_local":
                errors.append(ValidationError(
                    "dependencies",
                    f"Cannot export: depends on _local/{dep['id']} (ephemeral scope)",
                    dep
                ))

        return ValidationResult(
            valid=len(errors) == 0,
            errors=errors,
            warnings=meta_result.warnings
        )

    def _skill_exists(self, skill_id: str, scope: str) -> bool:
        """Check if skill exists."""
        skill_path = self.base_path / scope / "skills" / skill_id
        return skill_path.exists() and (skill_path / "meta.json").exists()


class DependencyValidator:
    """Validate dependency consistency and versions."""

    def __init__(self, tenant_id: str = "_default"):
        self.tenant_id = tenant_id
        self.base_path = Path.home() / ".corvin" / "tenants" / tenant_id

    def validate_circular_dependencies(self, scope: str = "_shared") -> List[List[str]]:
        """Detect circular dependency chains."""
        # Build graph
        graph = {}
        skills_dir = self.base_path / scope / "skills"

        for skill_dir in skills_dir.iterdir():
            if not skill_dir.is_dir() or skill_dir.name.startswith('.'):
                continue

            meta_path = skill_dir / "meta.json"
            if not meta_path.exists():
                continue

            with open(meta_path) as f:
                metadata = json.load(f)

            skill_id = skill_dir.name
            graph[skill_id] = [dep["id"] for dep in metadata.get("dependencies", [])]

        # Detect cycles via DFS
        cycles = []

        def dfs(node, visited, rec_stack, path):
            visited.add(node)
            rec_stack.add(node)
            path.append(node)

            for neighbor in graph.get(node, []):
                if neighbor not in visited:
                    dfs(neighbor, visited, rec_stack, path)
                elif neighbor in rec_stack:
                    # Found cycle
                    cycle_start = path.index(neighbor)
                    cycles.append(path[cycle_start:] + [neighbor])

            path.pop()
            rec_stack.remove(node)

        visited = set()
        for skill_id in graph:
            if skill_id not in visited:
                dfs(skill_id, visited, set(), [])

        return cycles


def validate_metadata_file(file_path: Path, metadata_type: str = "skill") -> ValidationResult:
    """Validate a metadata file (convenience function)."""
    if not file_path.exists():
        return ValidationResult(
            valid=False,
            errors=[ValidationError("file", f"File not found: {file_path}")],
            warnings=[]
        )

    try:
        with open(file_path) as f:
            metadata = json.load(f)
    except json.JSONDecodeError as e:
        return ValidationResult(
            valid=False,
            errors=[ValidationError("json", f"Invalid JSON: {str(e)}")],
            warnings=[]
        )

    schema = SKILL_METADATA_SCHEMA if metadata_type == "skill" else TOOL_METADATA_SCHEMA
    errors = []

    try:
        jsonschema.validate(metadata, schema)
    except jsonschema.ValidationError as e:
        errors.append(ValidationError(
            field=".".join(str(p) for p in e.absolute_path) or "root",
            error=e.message,
            value=e.instance
        ))

    return ValidationResult(valid=len(errors) == 0, errors=errors, warnings=[])
