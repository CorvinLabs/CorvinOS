"""
Skill Forge v2.0 Phase 1: Manifest V2 Schema

ADR-0533 Manifest compliance + validation. Immutable contract for all Skills 2.0.

Author: Claude Haiku 4.5
License: Apache-2.0
"""

import json
import re
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any
from enum import Enum
import jsonschema


class BootLayer(str, Enum):
    """Boot layer hierarchy (ADR-0243)."""
    INSTALLED = "installed"  # User/marketplace skills
    BUNDLED = "bundled"  # Shipped with CorvinOS
    CORE = "core"  # System-level (non-replaceable)
    COMPLIANCE = "compliance"  # Meta-Skills (audit, consent, house-rules)


class SkillDomain(str, Enum):
    """Skill domain categories."""
    ROUTING = "routing"
    LEARNING = "learning"
    OPTIMIZATION = "optimization"
    INTEGRATION = "integration"


@dataclass
class SkillParameter:
    """Tunable parameter (learned by optimizer, ADR-0314)."""
    name: str
    type: str  # float | int | bool | str
    default: Any
    bounds: Optional[List[float]] = None  # [min, max] for numeric
    description: Optional[str] = None


@dataclass
class SkillDependency:
    """Skill dependency (ADR-0535 DAG)."""
    skill_id: str
    version: str  # Semver range: ">=0.1.0", "^0.1.0", "0.1.0"
    optional: bool = False


@dataclass
class SkillManifestV2:
    """
    Complete Skill Manifest (ADR-0533).

    Immutable contract specifying Skill metadata, capabilities, dependencies,
    audit integration, and learning configuration.
    """

    # Core identification
    skill_id: str
    name: str
    version: str  # Semantic versioning: MAJOR.MINOR.PATCH
    description: str
    author: str = "generated-by-skill-forge-v2.0"
    license: str = "Apache-2.0"

    # Execution contract
    boot_layer: BootLayer = BootLayer.INSTALLED
    entry_point: str = ""  # module.path:ClassName.method
    domain: SkillDomain = SkillDomain.ROUTING

    # Capabilities & constraints
    capabilities: List[str] = field(default_factory=list)
    parameters: List[SkillParameter] = field(default_factory=list)
    dependencies: List[SkillDependency] = field(default_factory=list)

    # I/O contracts
    input_schema: Dict[str, Any] = field(default_factory=dict)
    output_schema: Dict[str, Any] = field(default_factory=dict)

    # Hooks & scripts (folder paths)
    hooks: Dict[str, str] = field(default_factory=lambda: {
        "on_load": "hooks/on_load.py",
        "on_execute": "hooks/on_execute.py",
        "on_feedback": "hooks/on_feedback.py",
        "on_unload": "hooks/on_unload.py",
    })
    scripts: Dict[str, str] = field(default_factory=lambda: {
        "install": "scripts/install.py",
        "test": "scripts/test_runner.py",
        "package": "scripts/packager.py",
        "integrate": "scripts/integrator.py",
    })

    # Audit & compliance
    audit_events: List[str] = field(default_factory=lambda: [
        "skill_executed",
        "skill_failed",
        "learning_event_emitted",
    ])
    learning: Dict[str, Any] = field(default_factory=lambda: {
        "enabled": True,
        "strategy": "gradient_descent",
        "feedback_sources": ["outcome_feedback"],
        "convergence_signal": "weight_stabilization",
    })
    compliance: Dict[str, Any] = field(default_factory=lambda: {
        "gdpr_ready": True,
        "audit_trail": True,
        "pii_handling": "redacted",
        "eu_ai_act_tier": "high_risk",
    })

    # Generation metadata (immutable after Phase 1)
    generation_metadata: Dict[str, Any] = field(default_factory=dict)

    # Validation schema (must be present)
    _MANIFEST_SCHEMA = {
        "type": "object",
        "required": [
            "skill_id",
            "name",
            "version",
            "description",
            "entry_point",
            "boot_layer",
            "input_schema",
            "output_schema",
        ],
        "properties": {
            "skill_id": {"type": "string", "pattern": "^[a-z0-9_]+$"},
            "name": {"type": "string", "minLength": 1},
            "version": {"type": "string", "pattern": r"^\d+\.\d+\.\d+$"},
            "description": {"type": "string", "minLength": 1},
            "boot_layer": {"type": "string", "enum": ["installed", "bundled", "core", "compliance"]},
            "entry_point": {"type": "string", "pattern": "^[a-z_.]+:[A-Za-z_][A-Za-z0-9_]*\\.execute$"},
            "input_schema": {"type": "object"},
            "output_schema": {"type": "object"},
            "audit_events": {"type": "array", "items": {"type": "string"}},
        },
    }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SkillManifestV2":
        """
        Create manifest from dict (e.g., loaded from JSON).

        Args:
            data: Manifest dict

        Returns:
            SkillManifestV2 instance

        Raises:
            jsonschema.ValidationError: Schema validation failed
            ValueError: Invalid enum value or constraint
        """
        # Validate schema first
        jsonschema.validate(data, cls._MANIFEST_SCHEMA)

        # Convert enums
        boot_layer = BootLayer(data.get("boot_layer", "installed"))
        domain = SkillDomain(data.get("domain", "routing"))

        # Convert parameters
        parameters = [
            SkillParameter(**p) if isinstance(p, dict) else p
            for p in data.get("parameters", [])
        ]

        # Convert dependencies
        dependencies = [
            SkillDependency(**d) if isinstance(d, dict) else d
            for d in data.get("dependencies", [])
        ]

        return cls(
            skill_id=data["skill_id"],
            name=data["name"],
            version=data["version"],
            description=data["description"],
            author=data.get("author", "generated-by-skill-forge-v2.0"),
            license=data.get("license", "Apache-2.0"),
            boot_layer=boot_layer,
            entry_point=data.get("entry_point", ""),
            domain=domain,
            capabilities=data.get("capabilities", []),
            parameters=parameters,
            dependencies=dependencies,
            input_schema=data.get("input_schema", {}),
            output_schema=data.get("output_schema", {}),
            hooks=data.get("hooks", {
                "on_load": "hooks/on_load.py",
                "on_execute": "hooks/on_execute.py",
                "on_feedback": "hooks/on_feedback.py",
                "on_unload": "hooks/on_unload.py",
            }),
            scripts=data.get("scripts", {
                "install": "scripts/install.py",
                "test": "scripts/test_runner.py",
                "package": "scripts/packager.py",
                "integrate": "scripts/integrator.py",
            }),
            audit_events=data.get("audit_events", []),
            learning=data.get("learning", {}),
            compliance=data.get("compliance", {}),
            generation_metadata=data.get("generation_metadata", {}),
        )

    @classmethod
    def from_json_file(cls, filepath: str) -> "SkillManifestV2":
        """Load manifest from skill.json file."""
        with open(filepath, "r") as f:
            data = json.load(f)
        return cls.from_dict(data)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict (for JSON serialization)."""
        data = asdict(self)
        # Convert enums to strings
        data["boot_layer"] = self.boot_layer.value
        data["domain"] = self.domain.value
        # Convert dataclass objects
        data["parameters"] = [asdict(p) for p in self.parameters]
        data["dependencies"] = [asdict(d) for d in self.dependencies]
        return data

    def to_json(self, indent: int = 2) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict(), indent=indent)

    def to_json_file(self, filepath: str) -> None:
        """Write manifest to skill.json file."""
        with open(filepath, "w") as f:
            f.write(self.to_json())

    def validate(self) -> List[str]:
        """
        Validate manifest constraints (beyond schema).

        Returns:
            List of validation errors (empty if valid)
        """
        errors = []

        # Validate skill_id format
        if not re.match(r"^[a-z0-9_]+$", self.skill_id):
            errors.append(f"skill_id '{self.skill_id}' must be lowercase alphanumeric + underscore")

        # Validate semantic versioning
        if not re.match(r"^\d+\.\d+\.\d+$", self.version):
            errors.append(f"version '{self.version}' must follow semantic versioning (MAJOR.MINOR.PATCH)")

        # Validate output schema has confidence
        if "confidence" not in self.output_schema:
            errors.append("output_schema must include 'confidence' field")

        # Validate entry_point
        if not self.entry_point or ":" not in self.entry_point:
            errors.append(f"entry_point '{self.entry_point}' must be 'module.path:ClassName.method'")

        # Validate dependencies (DAG check — basic)
        for dep in self.dependencies:
            if not re.match(r"^[a-z0-9_.]+$", dep.skill_id):
                errors.append(f"dependency skill_id '{dep.skill_id}' invalid")

        # Validate boot_layer is not claimed incorrectly
        if self.boot_layer in [BootLayer.CORE, BootLayer.COMPLIANCE]:
            errors.append(f"boot_layer '{self.boot_layer.value}' cannot be user-claimed (reserved)")

        # Validate audit_events are present
        if not self.audit_events:
            errors.append("audit_events must be non-empty (at least skill_executed)")

        # Validate learning config
        if self.learning.get("enabled") and not self.learning.get("strategy"):
            errors.append("learning.strategy required when learning.enabled=true")

        return errors

    def __repr__(self) -> str:
        return f"SkillManifestV2(id={self.skill_id}, v{self.version}, domain={self.domain.value})"


class SkillManifestValidator:
    """Validation utilities for Skill manifests."""

    @staticmethod
    def validate_manifest_file(filepath: str) -> tuple[bool, List[str]]:
        """
        Validate a skill.json file.

        Args:
            filepath: Path to skill.json

        Returns:
            (is_valid, errors)
        """
        try:
            manifest = SkillManifestV2.from_json_file(filepath)
            errors = manifest.validate()
            return len(errors) == 0, errors
        except Exception as e:
            return False, [str(e)]

    @staticmethod
    def validate_schema_only(data: Dict[str, Any]) -> bool:
        """Quick schema validation (without constraint checks)."""
        try:
            jsonschema.validate(data, SkillManifestV2._MANIFEST_SCHEMA)
            return True
        except jsonschema.ValidationError:
            return False


if __name__ == "__main__":
    # Example usage
    example_manifest = {
        "skill_id": "test_routing_skill",
        "name": "Test Routing Skill",
        "version": "0.1.0",
        "description": "Routes requests to best LLM",
        "entry_point": "src.skill:TestRoutingSkill.execute",
        "boot_layer": "installed",
        "domain": "routing",
        "input_schema": {"request": "string"},
        "output_schema": {"engine": "string", "confidence": "float"},
        "audit_events": ["skill_executed", "skill_failed"],
    }

    try:
        manifest = SkillManifestV2.from_dict(example_manifest)
        errors = manifest.validate()
        if errors:
            print(f"✗ Validation errors: {errors}")
        else:
            print(f"✓ Manifest valid: {manifest}")
    except Exception as e:
        print(f"✗ Error: {e}")
