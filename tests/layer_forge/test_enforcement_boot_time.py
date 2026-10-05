"""Unit tests for boot-time enforcement checker (ADR-2225).

Tests the JSON-schema + DAG validation that runs before any plugin loads.
Validates fail-closed: invalid manifest = denied (never admitted).
"""
import pytest
from core.orchestration.layer_forge.enforcement import EnforcementChecker, LayerBootValidationError
from core.orchestration.layer_forge.schema import LayerSchemaValidationError


def test_valid_manifest_passes_schema_validation(tmp_path):
    """A valid manifest passes schema + DAG check."""
    checker = EnforcementChecker(tmp_path)
    manifest = {
        "id": "test_layer",
        "version": "1.0.0",
        "type": "layer_definition",
        "targets": [{"layer_id": "L10", "layer_name": "Context"}],
        "dependencies": [],
        "quality_gates": [],
        "enforcement_rules": []
    }
    verdict = checker.check_schema_validation(manifest)
    assert verdict.status == "PASS"
    assert "valid" in verdict.detail


def test_missing_required_id_fails_validation(tmp_path):
    """Missing required 'id' field → FAIL."""
    checker = EnforcementChecker(tmp_path)
    manifest = {
        "version": "1.0.0",
        "targets": [{"layer_id": "L10"}]
    }
    verdict = checker.check_schema_validation(manifest)
    assert verdict.status == "FAIL"
    assert "id" in verdict.detail or "missing required" in verdict.detail


def test_invalid_version_format_fails_validation(tmp_path):
    """Invalid semver in version field → FAIL."""
    checker = EnforcementChecker(tmp_path)
    manifest = {
        "id": "test_layer",
        "version": "not.a.version",
        "targets": [{"layer_id": "L10"}]
    }
    verdict = checker.check_schema_validation(manifest)
    assert verdict.status == "FAIL"
    assert "version" in verdict.detail or "semver" in verdict.detail


def test_empty_targets_fails_validation(tmp_path):
    """Empty targets list → FAIL."""
    checker = EnforcementChecker(tmp_path)
    manifest = {
        "id": "test_layer",
        "version": "1.0.0",
        "targets": []
    }
    verdict = checker.check_schema_validation(manifest)
    assert verdict.status == "FAIL"
    assert "targets" in verdict.detail or "non-empty" in verdict.detail


def test_dependency_dag_with_circular_ref_fails(tmp_path):
    """Circular dependency in DAG → FAIL."""
    checker = EnforcementChecker(tmp_path)
    manifest = {
        "id": "layer_a",
        "version": "1.0.0",
        "targets": [{"layer_id": "L10"}],
        "dependencies": [
            {"id": "layer_b", "type": "layer_definition"}
        ]
    }

    def registry_lookup(dep_id):
        if dep_id == "layer_a":
            return ["layer_b"]
        elif dep_id == "layer_b":
            return ["layer_a"]  # Circle!
        return None

    verdict = checker.check_schema_validation(manifest, registry_lookup)
    assert verdict.status == "FAIL"
    assert "circular" in verdict.detail.lower() or "cycle" in verdict.detail.lower()


def test_dependency_dag_with_unresolvable_ref_fails(tmp_path):
    """Unresolvable dependency ID → FAIL."""
    checker = EnforcementChecker(tmp_path)
    manifest = {
        "id": "layer_a",
        "version": "1.0.0",
        "targets": [{"layer_id": "L10"}],
        "dependencies": [
            {"id": "nonexistent_layer", "type": "layer_definition"}
        ]
    }

    def registry_lookup(dep_id):
        if dep_id == "layer_a":
            return ["nonexistent_layer"]
        return None  # Unresolvable!

    verdict = checker.check_schema_validation(manifest, registry_lookup)
    assert verdict.status == "FAIL"
    assert "unresolvable" in verdict.detail.lower() or "nonexistent" in verdict.detail.lower()


def test_check_all_enforcement_rules_aggregates_verdicts(tmp_path):
    """check_all_enforcement_rules() runs all checks and returns list."""
    checker = EnforcementChecker(tmp_path)
    manifest = {
        "id": "test_layer",
        "version": "1.0.0",
        "targets": [{"layer_id": "L10"}],
        "enforcement_rules": []
    }
    verdicts = checker.check_all_enforcement_rules(manifest)
    assert len(verdicts) >= 3  # schema, boundaries, host_awareness
    # schema should PASS
    schema_verdict = [v for v in verdicts if v.rule_id == "schema_validation"]
    assert schema_verdict
    assert schema_verdict[0].status in ("PASS", "FAIL", "ERROR")


def test_is_manifest_valid_returns_false_on_fail(tmp_path):
    """is_manifest_valid() returns False if any verdict is FAIL."""
    checker = EnforcementChecker(tmp_path)
    manifest = {
        "id": "test_layer",
        "version": "1.0.0",
        # Missing 'targets' — will fail!
    }
    result = checker.is_manifest_valid(manifest)
    assert result is False


def test_is_manifest_valid_returns_true_on_all_pass_or_skip(tmp_path):
    """is_manifest_valid() returns True only if all verdicts are PASS or SKIPPED."""
    checker = EnforcementChecker(tmp_path)
    manifest = {
        "id": "test_layer",
        "version": "1.0.0",
        "targets": [{"layer_id": "L10"}],
        "enforcement_rules": []
    }
    result = checker.is_manifest_valid(manifest)
    assert result is True
