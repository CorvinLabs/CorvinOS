"""L1: Skill Contract Schema (Eiffel-Style Contracts)."""

from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Optional
from enum import Enum
import json
from jsonschema import Draft202012Validator, validate, ValidationError


class SkillTier(str, Enum):
    """Skill composability tier."""
    PRIMITIVE = "primitive"  # Pure, no side effects
    STATEFUL = "stateful"    # Local state + side effects
    AUTONOMOUS = "autonomous"  # Learning enabled, multi-phase


@dataclass(frozen=True)
class Predicate:
    """Boolean condition (for preconditions/postconditions)."""
    name: str
    condition: Callable[[Any], bool]

    def evaluate(self, context: Any) -> bool:
        """Evaluate predicate against context."""
        try:
            return self.condition(context)
        except Exception:
            return False


@dataclass(frozen=True)
class SkillContract:
    """Eiffel-style contract for a skill."""

    # Identity
    skill_id: str
    version: str  # Semantic versioning
    tier: SkillTier

    # Input/Output schema (immutable)
    input_schema: dict  # JSONSchema
    output_schema: dict  # JSONSchema
    error_schema: dict = field(default_factory=lambda: {})  # Error output schema

    # Preconditions (what must be true BEFORE execution)
    preconditions: list[Predicate] = field(default_factory=list)

    # Postconditions (what's guaranteed AFTER execution)
    postconditions: list[Predicate] = field(default_factory=list)

    # Tier 2 only: required predecessors/successors
    required_predecessors: list[str] = field(default_factory=list)
    required_successors: list[str] = field(default_factory=list)

    # Side effects (Tier 2/3)
    side_effects: list[str] = field(default_factory=list)  # e.g., ["file_create", "cloud_charge"]
    reversible_side_effects: dict[str, str] = field(default_factory=dict)  # side_effect -> reversal_skill

    # Learning (Tier 3 only)
    learning_enabled: bool = False
    learning_config_schema: Optional[dict] = None

    def validate(self) -> tuple[bool, str]:
        """Validate contract integrity."""

        # All side effects must be reversible (CRITICAL)
        for side_effect in self.side_effects:
            if side_effect not in self.reversible_side_effects:
                return False, f"Side effect '{side_effect}' not reversible"

        # Preconditions/postconditions must be callable
        for pred in self.preconditions + self.postconditions:
            if not callable(pred.condition):
                return False, f"Predicate '{pred.name}' is not callable"

        # Tier 2 requires predecessors/successors if declared
        if self.tier == SkillTier.STATEFUL:
            if not self.required_predecessors and not self.required_successors:
                return False, f"Stateful skill {self.skill_id} must declare predecessors or successors"

        # Tier 3 can have learning
        if self.tier == SkillTier.AUTONOMOUS and self.learning_enabled:
            if not self.learning_config_schema:
                return False, f"Autonomous skill {self.skill_id} with learning must have config schema"

        # Validate JSONSchemas are valid
        for name, schema in [("input", self.input_schema), ("output", self.output_schema)]:
            if schema:
                try:
                    Draft202012Validator.check_schema(schema)
                except Exception as e:
                    return False, f"Invalid {name}_schema: {str(e)}"

        return True, "OK"

    def validate_input(self, data: Any) -> tuple[bool, str]:
        """Validate input against input_schema."""
        try:
            validate(instance=data, schema=self.input_schema)
            return True, "OK"
        except ValidationError as e:
            return False, f"Input validation failed: {e.message}"

    def validate_output(self, data: Any) -> tuple[bool, str]:
        """Validate output against output_schema."""
        try:
            validate(instance=data, schema=self.output_schema)
            return True, "OK"
        except ValidationError as e:
            return False, f"Output validation failed: {e.message}"


@dataclass(frozen=True)
class MajorVersionMarker:
    """Breaking change marker for major version upgrades."""

    skill_id: str
    old_version: str
    new_version: str
    breaking_changes: list[str]  # e.g., ["confidence_threshold: 0.7 -> 0.6"]
    compatibility_layer: bool = False  # Can v1.0 config auto-convert to v2.0?
    conversion_skill_id: Optional[str] = None  # Skill to handle conversion


class SkillRegistry:
    """Registry for skill contracts."""

    def __init__(self):
        self.contracts: dict[str, SkillContract] = {}
        self.versions: dict[str, list[str]] = {}  # skill_id -> [v1.0, v1.1, v2.0, ...]
        self.version_markers: dict[tuple[str, str, str], MajorVersionMarker] = {}  # (skill_id, old_v, new_v) -> marker

    def register(self, contract: SkillContract) -> tuple[bool, str]:
        """Register a skill contract."""

        # Validate contract
        valid, msg = contract.validate()
        if not valid:
            return False, f"Contract validation failed: {msg}"

        # Check duplicate
        key = f"{contract.skill_id}:{contract.version}"
        if key in self.contracts:
            return False, f"Skill {key} already registered"

        # Store
        self.contracts[key] = contract
        if contract.skill_id not in self.versions:
            self.versions[contract.skill_id] = []
        self.versions[contract.skill_id].append(contract.version)

        return True, f"Registered {key}"

    def register_version_marker(self, marker: MajorVersionMarker) -> tuple[bool, str]:
        """Register a major version breaking-change marker."""

        key = (marker.skill_id, marker.old_version, marker.new_version)
        self.version_markers[key] = marker

        return True, f"Registered version marker {key}"

    def get_contract(self, skill_id: str, version: str) -> Optional[SkillContract]:
        """Get contract for skill version."""
        return self.contracts.get(f"{skill_id}:{version}")

    def get_latest_contract(self, skill_id: str) -> Optional[SkillContract]:
        """Get latest contract for skill."""
        if skill_id not in self.versions:
            return None
        latest_version = sorted(self.versions[skill_id], reverse=True)[0]
        return self.get_contract(skill_id, latest_version)

    def get_version_marker(self, skill_id: str, old_version: str, new_version: str) -> Optional[MajorVersionMarker]:
        """Get version marker for upgrade."""
        return self.version_markers.get((skill_id, old_version, new_version))


# Global registry
SKILL_REGISTRY = SkillRegistry()
