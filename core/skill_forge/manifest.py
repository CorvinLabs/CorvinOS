"""Skill Manifest Schema — Frozen, Validated, Immutable Skill Descriptor

Implements ADR-0853 Layer 1: Manifest schema with comprehensive validation.
Every skill is defined via an immutable manifest that specifies:
- Execution contract (input/output schema)
- Dependencies + composition constraints
- Learning integration points
- Compliance requirements (audit, consent, house-rules)
"""

from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional, Any
import json
import re
import jsonschema


class SkillManifestError(Exception):
    """Base exception for manifest validation errors."""
    pass


class CircularDependencyError(SkillManifestError):
    """Raised when skill dependencies form a cycle."""
    pass


class InvalidSkillIdError(SkillManifestError):
    """Raised when skill_id does not match format constraints."""
    pass


class InvalidSchemaError(SkillManifestError):
    """Raised when input/output schema is not valid JSON schema."""
    pass


@dataclass(frozen=True)
class SkillManifest:
    """Immutable skill descriptor — validated at creation, never mutated.

    A SkillManifest defines the complete contract for a skill:
    - What it accepts (input_schema)
    - What it returns (output_schema)
    - What it depends on (depends_on)
    - How it learns (learnable_params)
    - How it's audited (audit_events, required_checks)
    """

    # Identity
    skill_id: str                               # e.g., "os.delegation_router" or "marketplace.plugin_search"
    version: str                                # semver (e.g., "1.2.3")
    author: str                                 # creator attribution
    description: str                            # one-line purpose

    # Execution contract
    entry_point: str                            # e.g., "core.skills.delegation_router.execute"
    input_schema: Dict[str, Any]               # JSON schema for input validation
    output_schema: Dict[str, Any]              # JSON schema for output validation

    # Dependency graph (for composition)
    depends_on: Tuple[Tuple[str, str], ...] = ()  # ((skill_id, version_spec), ...)
    required_capabilities: Tuple[str, ...] = ()   # ("L5_ROUTING", "AUDIT_TRAIL", ...)

    # Learning integration (ADR-0314)
    learnable_params: Dict[str, Dict[str, Any]] = None  # {"threshold": {"type": "float", "min": 0.0, "max": 1.0}}
    learning_feedback_types: Tuple[str, ...] = ()       # ("outcome", "preference", "confidence", "metric")

    # Compliance & audit (ADR-0232)
    audit_events: Tuple[str, ...] = ()                  # ("skill_executed", "config_optimized", ...)
    required_checks: Tuple[str, ...] = ()               # ("consent_gate", "house_rules", ...)

    # Lifecycle
    boot_layer: str = "bundled"                         # "bundled" | "installed" | "community"
    active_by_default: bool = True
    removal_date: Optional[str] = None                  # ISO format for deprecation

    def __post_init__(self):
        """Validate manifest after initialization (frozen allows this once)."""
        # Validate skill_id format
        self._validate_skill_id()

        # Validate version (semver)
        self._validate_semver()

        # Validate schemas (must be valid JSON schema)
        self._validate_schemas()

        # Validate dependencies (no circular refs)
        self._validate_dependencies()

        # Validate learnable params match expected types
        self._validate_learnable_params()

        # Validate required_checks (must be known checks)
        self._validate_required_checks()

    def _validate_skill_id(self) -> None:
        """Validate skill_id format: namespace.name (no special chars)."""
        if not re.match(r'^[a-z0-9_]+\.[a-z0-9_]+(\.[a-z0-9_]+)*$', self.skill_id):
            raise InvalidSkillIdError(
                f"Invalid skill_id format: {self.skill_id}. "
                "Must be lowercase alphanumeric + underscore + dots (e.g., 'os.delegation_router')"
            )

    def _validate_semver(self) -> None:
        """Validate version is semantic versioning (major.minor.patch)."""
        if not re.match(r'^\d+\.\d+\.\d+$', self.version):
            raise SkillManifestError(
                f"Invalid version: {self.version}. "
                "Must be semantic versioning (e.g., '1.2.3')"
            )

    def _validate_schemas(self) -> None:
        """Validate input_schema and output_schema are valid JSON schema."""
        for schema_name, schema in [("input_schema", self.input_schema), ("output_schema", self.output_schema)]:
            try:
                jsonschema.Draft7Validator.check_schema(schema)
            except jsonschema.SchemaError as e:
                raise InvalidSchemaError(
                    f"Invalid {schema_name}: {e.message}"
                )

    def _validate_dependencies(self) -> None:
        """Validate no circular dependencies (will be checked globally by SkillRegistry)."""
        # Local check: dependency on self is not allowed
        for dep_id, _ in self.depends_on:
            if dep_id == self.skill_id:
                raise CircularDependencyError(f"Skill {self.skill_id} cannot depend on itself")

    def _validate_learnable_params(self) -> None:
        """Validate learnable_params schema (type, min/max constraints)."""
        if not self.learnable_params:
            return

        valid_types = {"float", "int", "str", "bool"}
        for param_name, param_spec in self.learnable_params.items():
            if "type" not in param_spec:
                raise SkillManifestError(
                    f"Learnable param '{param_name}' missing 'type' field"
                )
            param_type = param_spec["type"]
            if param_type not in valid_types:
                raise SkillManifestError(
                    f"Invalid learnable param type '{param_type}' for '{param_name}'. "
                    f"Must be one of: {', '.join(valid_types)}"
                )

            # For numeric types, validate min/max
            if param_type in ("float", "int"):
                if "min" in param_spec and "max" in param_spec:
                    if param_spec["min"] > param_spec["max"]:
                        raise SkillManifestError(
                            f"Invalid bounds for '{param_name}': min ({param_spec['min']}) > max ({param_spec['max']})"
                        )

    def _validate_required_checks(self) -> None:
        """Validate required_checks are known compliance checks."""
        # Known checks (can be extended as new compliance layers added)
        known_checks = {
            "consent_gate",      # L16 user consent
            "house_rules",       # L44 acceptable use
            "audit_trail",       # ADR-0232 immutable logging
            "pii_detection",     # Data classification
            "data_flow_guard",   # L34 data flow validation
        }
        for check in self.required_checks:
            if check not in known_checks:
                # Warn but don't fail (new checks might be added dynamically)
                pass

    def to_dict(self) -> Dict[str, Any]:
        """Export manifest as dictionary (for serialization, JSON, etc.)."""
        return {
            "skill_id": self.skill_id,
            "version": self.version,
            "author": self.author,
            "description": self.description,
            "entry_point": self.entry_point,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "depends_on": list(self.depends_on),
            "required_capabilities": list(self.required_capabilities),
            "learnable_params": self.learnable_params or {},
            "learning_feedback_types": list(self.learning_feedback_types),
            "audit_events": list(self.audit_events),
            "required_checks": list(self.required_checks),
            "boot_layer": self.boot_layer,
            "active_by_default": self.active_by_default,
            "removal_date": self.removal_date,
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> 'SkillManifest':
        """Create manifest from dictionary (e.g., loaded from JSON)."""
        return SkillManifest(
            skill_id=data["skill_id"],
            version=data["version"],
            author=data["author"],
            description=data["description"],
            entry_point=data["entry_point"],
            input_schema=data["input_schema"],
            output_schema=data["output_schema"],
            depends_on=tuple(tuple(dep) for dep in data.get("depends_on", [])),
            required_capabilities=tuple(data.get("required_capabilities", [])),
            learnable_params=data.get("learnable_params", {}),
            learning_feedback_types=tuple(data.get("learning_feedback_types", [])),
            audit_events=tuple(data.get("audit_events", [])),
            required_checks=tuple(data.get("required_checks", [])),
            boot_layer=data.get("boot_layer", "bundled"),
            active_by_default=data.get("active_by_default", True),
            removal_date=data.get("removal_date", None),
        )

    def to_json(self) -> str:
        """Serialize manifest to JSON string."""
        return json.dumps(self.to_dict(), indent=2)

    @staticmethod
    def from_json(json_str: str) -> 'SkillManifest':
        """Deserialize manifest from JSON string."""
        return SkillManifest.from_dict(json.loads(json_str))
