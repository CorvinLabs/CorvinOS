"""Phase 2b: Manifest Validation — Schema + versioning + DAG resolution.

Manifest format (YAML):
  manifest:
    version: 1.0.0
    skill_id: os.workflow_optimizer
    boot_layer: bundled
    parameters:
      - name: priority_weight
        type: float
        default: 0.5
        bounds: [0.0, 1.0]
    dependencies:
      - skill_id: os.delegation_router
        version: ">=0.1.0"
    entry_point: os_workflow_optimizer:WorkflowOptimizer.execute
"""

from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple
import re
import logging

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ManifestParameter:
    """Skill parameter definition (tunable via learning optimizer)."""
    name: str
    param_type: str  # float, int, string, enum
    default: any
    bounds: Optional[Tuple] = None  # (min, max) for numeric types


@dataclass(frozen=True)
class ManifestDependency:
    """Skill dependency with version constraint."""
    skill_id: str
    version_constraint: str  # Semver: ">=0.1.0", "~1.2", etc.


@dataclass(frozen=True)
class SkillManifest:
    """Immutable Skill manifest (ADR-0533)."""
    skill_id: str
    version: str  # Semver: 1.0.0
    boot_layer: str  # bundled, installed, core, compliance
    parameters: List[ManifestParameter] = None
    dependencies: List[ManifestDependency] = None
    entry_point: str = None  # module:class.method
    audit_events: List[str] = None  # Events this Skill emits

    def to_dict(self) -> Dict:
        return {
            "skill_id": self.skill_id,
            "version": self.version,
            "boot_layer": self.boot_layer,
            "parameters": [
                {"name": p.name, "type": p.param_type, "default": p.default, "bounds": p.bounds}
                for p in (self.parameters or [])
            ],
            "dependencies": [
                {"skill_id": d.skill_id, "version": d.version_constraint}
                for d in (self.dependencies or [])
            ],
            "entry_point": self.entry_point,
            "audit_events": self.audit_events or [],
        }


class ManifestValidator:
    """Validates Skill manifests against schema + constraints."""

    VALID_BOOT_LAYERS = {"bundled", "installed", "core", "compliance"}
    VALID_PARAM_TYPES = {"float", "int", "string", "enum", "bool"}

    def validate(self, manifest: SkillManifest) -> Tuple[bool, Optional[str]]:
        """Validate manifest against schema.

        Args:
            manifest: SkillManifest to validate

        Returns:
            (is_valid, error_message)
        """
        # 1. Skill ID must be non-empty
        if not manifest.skill_id:
            return False, "skill_id required"

        # 2. Version must be valid semver
        if not self._is_valid_semver(manifest.version):
            return False, f"Invalid version: {manifest.version} (must be semver)"

        # 3. Boot layer must be valid
        if manifest.boot_layer not in self.VALID_BOOT_LAYERS:
            return False, f"Invalid boot_layer: {manifest.boot_layer}"

        # 4. Parameters must have valid types + bounds
        for param in (manifest.parameters or []):
            if param.param_type not in self.VALID_PARAM_TYPES:
                return False, f"Invalid param type: {param.param_type} (param: {param.name})"

            if param.bounds and len(param.bounds) != 2:
                return False, f"Bounds must be (min, max) (param: {param.name})"

        # 5. Dependencies must have valid versions (DAG validation)
        seen_skills = set()
        for dep in (manifest.dependencies or []):
            if dep.skill_id == manifest.skill_id:
                return False, f"Circular dependency: {manifest.skill_id} depends on itself"

            if dep.skill_id in seen_skills:
                return False, f"Duplicate dependency: {dep.skill_id}"

            seen_skills.add(dep.skill_id)

            if not self._is_valid_semver_constraint(dep.version_constraint):
                return False, f"Invalid version constraint: {dep.version_constraint}"

        # 6. Entry point must be valid Python path (if specified)
        if manifest.entry_point and not self._is_valid_entry_point(manifest.entry_point):
            return False, f"Invalid entry_point: {manifest.entry_point}"

        return True, None

    @staticmethod
    def _is_valid_semver(version: str) -> bool:
        """Validate semantic version (e.g., 1.0.0)."""
        pattern = r'^(\d+)\.(\d+)\.(\d+)(?:-[a-zA-Z0-9]+)?(?:\+[a-zA-Z0-9]+)?$'
        return re.match(pattern, version) is not None

    @staticmethod
    def _is_valid_semver_constraint(constraint: str) -> bool:
        """Validate semver constraint (e.g., >=0.1.0, ~1.2, ^2.0)."""
        # Simplified: allow >=, ~, ^, * operators
        valid_prefixes = {">=", "<=", "~", "^", "=", "*"}
        if any(constraint.startswith(p) for p in valid_prefixes):
            remainder = constraint.lstrip(">=~^=*")
            return ManifestValidator._is_valid_semver(remainder) or remainder == "*"
        return ManifestValidator._is_valid_semver(constraint)

    @staticmethod
    def _is_valid_entry_point(entry_point: str) -> bool:
        """Validate Python module:class.method path."""
        parts = entry_point.split(":")
        if len(parts) != 2:
            return False

        module, method = parts
        if not all(p.replace("_", "").isalnum() for p in module.split(".")):
            return False

        if not method or "." not in method:
            return False

        return True


class ManifestDAGResolver:
    """Resolves Skill dependencies into DAG; detects cycles."""

    def resolve(self, manifest: SkillManifest, registry: Dict[str, SkillManifest]) -> Tuple[List[str], Optional[str]]:
        """Resolve dependency DAG (topological sort).

        Args:
            manifest: Root Skill manifest
            registry: All available Skill manifests by ID

        Returns:
            (execution_order, error_message) — execution_order is topological sort
        """
        visited = set()
        order = []
        cycle_check = set()

        def visit(skill_id: str) -> Optional[str]:
            if skill_id in cycle_check:
                return f"Cycle detected: {skill_id}"

            if skill_id in visited:
                return None

            cycle_check.add(skill_id)
            current = registry.get(skill_id)

            if not current:
                return f"Dependency not found: {skill_id}"

            for dep in (current.dependencies or []):
                error = visit(dep.skill_id)
                if error:
                    return error

            cycle_check.remove(skill_id)
            visited.add(skill_id)
            order.append(skill_id)
            return None

        error = visit(manifest.skill_id)
        if error:
            return [], error

        return order, None


# ============================================================================
# Tests
# ============================================================================

def test_manifest_validator():
    """Test: Manifest validation."""

    # Valid manifest
    valid = SkillManifest(
        skill_id="os.workflow_optimizer",
        version="1.0.0",
        boot_layer="bundled",
        parameters=[
            ManifestParameter("priority_weight", "float", 0.5, (0.0, 1.0))
        ],
        dependencies=[
            ManifestDependency("os.delegation_router", ">=0.1.0")
        ]
    )

    validator = ManifestValidator()
    is_valid, error = validator.validate(valid)
    assert is_valid, f"Valid manifest should pass: {error}"
    print("✅ Valid manifest passes")

    # Invalid: bad semver
    invalid_version = SkillManifest(
        skill_id="test",
        version="1.0",  # Should be 1.0.0
        boot_layer="bundled"
    )
    is_valid, error = validator.validate(invalid_version)
    assert not is_valid, "Bad semver should fail"
    print("✅ Bad semver rejected")

    # Invalid: circular dependency
    circular = SkillManifest(
        skill_id="test",
        version="1.0.0",
        boot_layer="bundled",
        dependencies=[
            ManifestDependency("test", "1.0.0")  # Self-reference
        ]
    )
    is_valid, error = validator.validate(circular)
    assert not is_valid, "Circular dependency should fail"
    print("✅ Circular dependency rejected")

    print("\n✅ Manifest validator tests pass!")


def test_dag_resolver():
    """Test: Dependency DAG resolution."""

    registry = {
        "skill_a": SkillManifest("skill_a", "1.0.0", "bundled", dependencies=[
            ManifestDependency("skill_b", "1.0.0")
        ]),
        "skill_b": SkillManifest("skill_b", "1.0.0", "bundled", dependencies=[
            ManifestDependency("skill_c", "1.0.0")
        ]),
        "skill_c": SkillManifest("skill_c", "1.0.0", "bundled"),
    }

    resolver = ManifestDAGResolver()
    root = registry["skill_a"]

    order, error = resolver.resolve(root, registry)
    assert error is None, f"DAG resolution should succeed: {error}"
    assert order == ["skill_c", "skill_b", "skill_a"], "Should be topological order"
    print("✅ DAG resolution: correct topological sort")

    print("\n✅ DAG resolver tests pass!")


if __name__ == "__main__":
    print("Running Phase 2b Manifest Validation Tests...\n")
    test_manifest_validator()
    test_dag_resolver()
    print("\n🎉 Manifest validation ready!")
