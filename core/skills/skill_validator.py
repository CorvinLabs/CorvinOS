"""
Skill Manifest Validator (ADR-0533)

Validates skill manifest.yaml files against the canonical JSON Schema.
Implements 13 validation checks before skill installation.
"""

import re
import yaml
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Any, Optional, Set, Tuple
import json
from jsonschema import Draft7Validator, ValidationError, FormatChecker


# Simple semver parsing (no external dependency required)
class SemanticVersion:
    """Simple semantic version parser."""

    def __init__(self, version_str: str):
        """Parse a semantic version string."""
        match = re.match(r'^(\d+)\.(\d+)\.(\d+)(?:-([a-z0-9]+))?$', version_str)
        if not match:
            raise ValueError(f"Invalid semver: {version_str}")

        self.major = int(match.group(1))
        self.minor = int(match.group(2))
        self.patch = int(match.group(3))
        self.prerelease = match.group(4)

    def __str__(self):
        s = f"{self.major}.{self.minor}.{self.patch}"
        if self.prerelease:
            s += f"-{self.prerelease}"
        return s

    def __lt__(self, other):
        if not isinstance(other, SemanticVersion):
            other = SemanticVersion(str(other))
        if self.major != other.major:
            return self.major < other.major
        if self.minor != other.minor:
            return self.minor < other.minor
        if self.patch != other.patch:
            return self.patch < other.patch
        # Prerelease versions are less than release versions
        if self.prerelease and not other.prerelease:
            return True
        if not self.prerelease and other.prerelease:
            return False
        if self.prerelease and other.prerelease:
            return self.prerelease < other.prerelease
        return False

    def __le__(self, other):
        return self < other or self == other

    def __gt__(self, other):
        if not isinstance(other, SemanticVersion):
            other = SemanticVersion(str(other))
        return other < self

    def __ge__(self, other):
        return self > other or self == other

    def __eq__(self, other):
        if not isinstance(other, SemanticVersion):
            other = SemanticVersion(str(other))
        return (self.major == other.major and
                self.minor == other.minor and
                self.patch == other.patch and
                self.prerelease == other.prerelease)


@dataclass
class SkillValidationReport:
    """Validation report for a skill manifest."""
    is_valid: bool
    skill_id: Optional[str] = None
    version: Optional[str] = None
    blockers: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    affected_paths: List[str] = field(default_factory=list)

    def __str__(self):
        status = "✅ VALID" if self.is_valid else "❌ INVALID"
        result = f"{status} {self.skill_id}@{self.version}\n"
        if self.blockers:
            result += f"\nBlockers ({len(self.blockers)}):\n"
            for blocker in self.blockers:
                result += f"  ❌ {blocker}\n"
        if self.warnings:
            result += f"\nWarnings ({len(self.warnings)}):\n"
            for warning in self.warnings:
                result += f"  ⚠️  {warning}\n"
        return result


# Allowed trigger event types and conditions
ALLOWED_TRIGGER_TYPES = {
    "decision_point",
    "system_event",
    "user_input",
    "timer",
    "webhook",
}

ALLOWED_TRIGGER_CONDITIONS = {
    "every_turn",
    "on_quota_exhausted",
    "on_latency_spike",
    "on_error_rate_high",
    "on_cost_spike",
    "on_user_feedback",
    "on_schedule",
    "on_webhook",
}

ALLOWED_TRIGGER_PHASES = {
    "pre_routing",
    "routing",
    "post_routing",
    "backpressure",
    "optimization",
    "post_execution",
    "user_feedback",
}

# Scoring rule patterns
SCORING_RULE_PATTERN = re.compile(
    r"^(mde|rmse|mae|accuracy|f1|auc)\s*([<>=]+)\s*([\d.]+)(%)?$",
    re.IGNORECASE
)

# PII patterns to disallow
REQUIRED_PII_PATTERNS = {
    "email",
    "phone",
    "credit_card",
    "social_security",
    "api_key",
    "password",
}

# Semver pattern
SEMVER_PATTERN = re.compile(r"^\d+\.\d+\.\d+(-[a-z0-9]+)?$")

# Skill name pattern
SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9._-]+$")


class SkillValidator:
    """Validates skill manifests against ADR-0533 schema."""

    def __init__(self, schema_path: Optional[Path] = None):
        """
        Initialize validator with JSON Schema.

        Args:
            schema_path: Path to manifest_schema.yaml (defaults to bundled schema)
        """
        if schema_path is None:
            schema_path = Path(__file__).parent / "manifest_schema.yaml"

        if not schema_path.exists():
            raise FileNotFoundError(f"Schema not found: {schema_path}")

        with open(schema_path) as f:
            self.schema = yaml.safe_load(f)

        # Initialize JSON Schema validator
        self.validator = Draft7Validator(
            self.schema,
            format_checker=FormatChecker()
        )

    def validate_manifest(
        self,
        manifest_path: Path,
    ) -> SkillValidationReport:
        """
        Validate a skill manifest.yaml file.

        Implements 13 validation checks (ADR-0533):
        1. manifest.yaml is valid YAML
        2. Frontmatter complete (name, version, goal, triggers, schemas, learning_signal)
        3. version matches regex ^[0-9]+\\.[0-9]+\\.[0-9]+(\\-[a-z0-9]+)?$
        4. input_schema is JSON Schema compliant
        5. output_schema is JSON Schema compliant
        6. triggers[].event_type in ALLOWED_TRIGGERS
        7. learning_signal.feedback_sources has required PII patterns
        8. depends_on: no cycles (DAG check)
        9. depends_on version constraints are valid semver ranges
        10. boot_layer in [core, compliance, bundled, installed]
        11. origin in [builtin, vetted, community]
        12. score_rule parseable (mde, rmse, mae, etc.)
        13. No unknown top-level keys

        Args:
            manifest_path: Path to manifest.yaml

        Returns:
            SkillValidationReport with validation results
        """
        report = SkillValidationReport(is_valid=True)

        # Check 1: manifest.yaml exists and is valid YAML
        if not manifest_path.exists():
            report.is_valid = False
            report.blockers.append(f"Manifest not found: {manifest_path}")
            return report

        try:
            with open(manifest_path) as f:
                manifest = yaml.safe_load(f)
        except yaml.YAMLError as e:
            report.is_valid = False
            report.blockers.append(f"Invalid YAML: {e}")
            return report

        if not isinstance(manifest, dict):
            report.is_valid = False
            report.blockers.append("Manifest must be a YAML object")
            return report

        self._check_manifest(manifest, report)
        report.affected_paths = [str(manifest_path)]
        return report

    def _check_manifest(self, manifest: Dict[str, Any], report: SkillValidationReport) -> None:
        """Checks 2–13 + full JSON Schema validation, shared by BOTH entry points.

        ``validate_manifest_dict`` used to run only the JSON Schema and the DAG
        check, so the same manifest could pass as a dict and fail as a file
        (e.g. an unparseable ``scoring_rule`` or unknown trigger phase), and the
        two paths reported the same defect with different messages.
        """
        report.skill_id = manifest.get("name")
        report.version = manifest.get("version")

        # Check 2: Frontmatter complete
        required_fields = [
            "name",
            "version",
            "goal",
            "description",
            "triggers",
            "input_schema",
            "output_schema",
            "learning_signal",
            "boot_layer",
            "origin",
            "scope",
        ]
        for field in required_fields:
            if field not in manifest:
                report.is_valid = False
                report.blockers.append(f"Missing required field: {field}")

        # No early return: a manifest missing ``scope`` must still be told its
        # version is malformed. Every check below tolerates absent/mistyped
        # fields (the JSON Schema pass reports the type errors).

        # Check 3: Version format (semver)
        if "version" in manifest and not SEMVER_PATTERN.match(str(manifest["version"])):
            report.is_valid = False
            report.blockers.append(
                f"Invalid version format: {manifest.get('version')} "
                "(must be Major.Minor.Patch, e.g., 1.2.3)"
            )

        # Check 4: input_schema is JSON Schema compliant
        if "input_schema" in manifest and not self._is_valid_json_schema(manifest["input_schema"]):
            report.is_valid = False
            report.blockers.append("input_schema is not valid JSON Schema")

        # Check 5: output_schema is JSON Schema compliant
        if "output_schema" in manifest and not self._is_valid_json_schema(manifest["output_schema"]):
            report.is_valid = False
            report.blockers.append("output_schema is not valid JSON Schema")

        # Check 6: Trigger event types
        triggers = manifest.get("triggers", [])
        for i, trigger in enumerate(triggers if isinstance(triggers, list) else []):
            if not isinstance(trigger, dict):
                report.is_valid = False
                report.blockers.append(f"triggers[{i}] must be an object")
                continue
            event_type = trigger.get("event_type")
            if event_type not in ALLOWED_TRIGGER_TYPES:
                report.blockers.append(
                    f"triggers[{i}].event_type '{event_type}' not in "
                    f"{sorted(ALLOWED_TRIGGER_TYPES)}"
                )
                report.is_valid = False

            # Check condition is valid
            condition = trigger.get("condition", "")
            if condition not in ALLOWED_TRIGGER_CONDITIONS:
                report.warnings.append(
                    f"triggers[{i}].condition '{condition}' is not a recognized condition"
                )

            # Check phase
            phase = trigger.get("phase")
            if phase not in ALLOWED_TRIGGER_PHASES:
                report.blockers.append(
                    f"triggers[{i}].phase '{phase}' not in {sorted(ALLOWED_TRIGGER_PHASES)}"
                )
                report.is_valid = False

        # Check 7: Learning signal has required PII patterns
        learning_signal = manifest.get("learning_signal", {})
        if not isinstance(learning_signal, dict):
            learning_signal = {}
        sanitization = learning_signal.get("sanitization", {})
        if not isinstance(sanitization, dict):
            sanitization = {}
        pii_patterns = set(p for p in sanitization.get("pii_patterns", []) or [] if isinstance(p, str))

        missing_pii = REQUIRED_PII_PATTERNS - pii_patterns
        if missing_pii:
            report.warnings.append(
                f"learning_signal.sanitization missing PII patterns: {sorted(missing_pii)}"
            )

        # Ensure fail_closed is true
        if not sanitization.get("fail_closed"):
            report.is_valid = False
            report.blockers.append(
                "learning_signal.sanitization.fail_closed must be true (fail-closed)"
            )

        # Check 8: Dependencies - no cycles (DAG check)
        deps_graph = self._build_dependency_graph(manifest)
        cycles = self._find_cycles(deps_graph)
        if cycles:
            report.is_valid = False
            for cycle in cycles:
                report.blockers.append(f"Dependency cycle detected: {' -> '.join(cycle)}")

        # Check 9: Dependency version constraints are valid semver
        deps = manifest.get("depends_on", [])
        for dep in deps if isinstance(deps, list) else []:
            if not isinstance(dep, dict):
                report.is_valid = False
                report.blockers.append("depends_on entries must be objects")
                continue
            version_constraint = dep.get("version", "")
            if not self._is_valid_semver_range(version_constraint):
                report.is_valid = False
                report.blockers.append(
                    f"Invalid semver range in depends_on: {dep.get('name')} "
                    f"version={version_constraint}"
                )

        # Check 10: boot_layer in allowed values
        boot_layer = manifest.get("boot_layer")
        if "boot_layer" in manifest and boot_layer not in ["compliance", "core", "bundled", "installed"]:
            report.is_valid = False
            report.blockers.append(
                f"boot_layer '{boot_layer}' not in "
                "[compliance, core, bundled, installed]"
            )

        # Check 11: origin in allowed values
        origin = manifest.get("origin")
        if "origin" in manifest and origin not in ["builtin", "vetted", "community"]:
            report.is_valid = False
            report.blockers.append(
                f"origin '{origin}' not in [builtin, vetted, community]"
            )

        # Check 12: Score rule is parseable
        score_rule = learning_signal.get("scoring_rule", "")
        if not SCORING_RULE_PATTERN.match(str(score_rule)):
            report.is_valid = False
            report.blockers.append(
                f"Invalid scoring_rule: '{score_rule}' "
                "(must match pattern: METRIC OPERATOR VALUE%, e.g., 'mde < 5%')"
            )

        # Check 13: No unknown top-level keys (schema drift detection)
        allowed_keys = set(self.schema.get("properties", {}).keys())
        unknown_keys = set(manifest.keys()) - allowed_keys
        if unknown_keys:
            report.warnings.append(
                f"Unknown top-level keys (schema drift): {sorted(unknown_keys)}"
            )

        # JSON Schema validation (comprehensive)
        validation_errors = list(self.validator.iter_errors(manifest))
        if validation_errors:
            report.is_valid = False
            for error in validation_errors:
                path = ".".join(str(p) for p in error.absolute_path) or "root"
                report.blockers.append(f"{path}: {error.message}")

    def validate_manifest_dict(self, manifest: Dict[str, Any]) -> SkillValidationReport:
        """
        Validate a manifest as a dictionary (already parsed).

        Args:
            manifest: Dictionary containing manifest data

        Returns:
            SkillValidationReport
        """
        report = SkillValidationReport(is_valid=True)
        if not isinstance(manifest, dict):
            report.is_valid = False
            report.blockers.append("Manifest must be a YAML object")
            return report
        self._check_manifest(manifest, report)
        return report

    @staticmethod
    def _is_valid_json_schema(schema: Any) -> bool:
        """Check if something is a valid JSON Schema."""
        if not isinstance(schema, dict):
            return False
        # At minimum, should have 'type' and 'properties'
        if "type" not in schema:
            return False
        return True

    @staticmethod
    def _is_valid_semver_range(version_spec: str) -> bool:
        """
        Validate a semver range (e.g., '>=1.0.0', '~1.2.3', '^1.0.0').

        Args:
            version_spec: Version specification string

        Returns:
            True if valid, False otherwise
        """
        if not version_spec:
            return False

        # Strip operator prefix
        spec = re.sub(r"^(>=?|<=?|~|\^)", "", version_spec).strip()

        # Check if what remains is valid semver
        try:
            SemanticVersion(spec)
            return True
        except (ValueError, AttributeError):
            return False

    @staticmethod
    def _build_dependency_graph(manifest: Dict[str, Any]) -> Dict[str, Set[str]]:
        """
        Build a dependency graph from manifest.

        Args:
            manifest: Skill manifest

        Returns:
            Dict mapping skill name to set of dependencies
        """
        graph = {}
        skill_name = manifest.get("name", "unknown")
        graph[skill_name] = set()

        deps = manifest.get("depends_on", [])
        for dep in deps if isinstance(deps, list) else []:
            if isinstance(dep, dict):
                graph[skill_name].add(dep.get("name", ""))

        return graph

    @staticmethod
    def _find_cycles(graph: Dict[str, Set[str]]) -> List[List[str]]:
        """
        Find cycles in dependency graph using DFS.

        Args:
            graph: Dependency graph

        Returns:
            List of cycles (each cycle is a path)
        """
        cycles = []
        visited = set()
        rec_stack = set()
        path = []

        def dfs(node):
            visited.add(node)
            rec_stack.add(node)
            path.append(node)

            for neighbor in graph.get(node, set()):
                if neighbor not in visited:
                    dfs(neighbor)
                elif neighbor in rec_stack:
                    # Found a cycle
                    cycle_start = path.index(neighbor)
                    cycle = path[cycle_start:] + [neighbor]
                    cycles.append(cycle)

            path.pop()
            rec_stack.remove(node)

        for node in graph:
            if node not in visited:
                dfs(node)

        return cycles


# Singleton validator instance
_validator: Optional[SkillValidator] = None


def get_validator() -> SkillValidator:
    """Get or create the singleton validator instance."""
    global _validator
    if _validator is None:
        _validator = SkillValidator()
    return _validator


def validate_skill_manifest(manifest_path: Path) -> SkillValidationReport:
    """
    Convenience function to validate a manifest file.

    Args:
        manifest_path: Path to manifest.yaml

    Returns:
        SkillValidationReport
    """
    return get_validator().validate_manifest(manifest_path)


def validate_skill_manifest_dict(manifest: Dict[str, Any]) -> SkillValidationReport:
    """
    Convenience function to validate a manifest dictionary.

    Args:
        manifest: Dictionary containing manifest data

    Returns:
        SkillValidationReport
    """
    return get_validator().validate_manifest_dict(manifest)
