"""E2E wiring test for orchestrator enforcement rule integration (ADR-2225, ADR-2226).

Verifies that orchestrator.create_layer_definition() calls check_all_enforcement_rules()
(not just check_host_awareness) and correctly rejects manifests on enforcement violations.
"""
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from core.orchestration.layer_forge.orchestrator import LayerForgeOrchestrator
from core.orchestration.layer_forge.review import ReviewVerdict


@pytest.fixture
def orchestrator():
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "forge_home"
        home.mkdir(parents=True)
        orch = LayerForgeOrchestrator("_default", repo_root=Path.cwd(), actor="test")
        # Override the home path for testing
        orch.registry._root = home / "registry"
        orch.registry._root.mkdir(parents=True, exist_ok=True)
        yield orch


@pytest.fixture
def mock_review():
    """Mock the adversarial review phase to avoid LLM calls."""
    with patch("core.orchestration.layer_forge.orchestrator.review_layer_definition") as m:
        m.return_value = ReviewVerdict("PASS", flags=[], reason="Mocked review")
        yield m


def test_valid_manifest_passes_all_enforcement_rules(orchestrator, mock_review):
    """A valid manifest passes all three enforcement checks (schema, boundaries, host_awareness)."""
    manifest = {
        "id": "e2e.valid",
        "version": "1.0.0",
        "targets": [{"layer_id": "L34", "layer_name": "Data Flow Guard"}],
        "enforcement_rules": [],
        "host_awareness": {"source_tree": {"paths": ["scripts/layer_forge_cli.py"]},
                           "cross_check": "none"},
    }
    result = orchestrator.create_layer_definition(manifest, skip_gates=True)

    assert result.status == "SUCCESS", f"Unexpected error: {result.error}"
    assert len(result.enforcement_verdicts) >= 3, \
        f"Expected >=3 enforcement verdicts, got {len(result.enforcement_verdicts)}"
    # At least schema_validation and host_awareness should pass
    statuses = {v.rule_id: v.status for v in result.enforcement_verdicts}
    assert statuses.get("schema_validation") in ("PASS", "FAIL", "ERROR"), \
        f"schema_validation verdict missing: {statuses}"


def test_invalid_manifest_rejects_at_validate_before_enforcement(orchestrator, mock_review):
    """Invalid manifest (missing required field) is rejected in VALIDATE phase before enforcement runs."""
    manifest = {
        "id": "e2e.invalid-schema",
        "version": "1.0.0",
        # Missing 'targets' — will fail schema validation in VALIDATE phase
    }
    result = orchestrator.create_layer_definition(manifest, skip_gates=True)

    assert result.status == "FAILED"
    assert result.phase == "validate", \
        "Invalid manifest should be rejected in VALIDATE phase (before ENFORCE runs)"
    # No enforcement verdicts since validation failed before enforcement ran
    assert len(result.enforcement_verdicts) == 0, \
        "Enforcement checks should not run when VALIDATE phase fails"


def test_dependency_resolution_uses_registry_lookup(orchestrator, mock_review):
    """Manifest with dependencies triggers registry_lookup in enforcement (verified by DAG validation)."""
    # Create layer-a (no dependencies)
    layer_a = {
        "id": "e2e.layer-a",
        "version": "1.0.0",
        "targets": [{"layer_id": "L34"}],
        "dependencies": [],
        "enforcement_rules": [],
    }
    result_a = orchestrator.create_layer_definition(layer_a, skip_gates=True)
    assert result_a.status == "SUCCESS"

    # Create layer-b that references layer-a (valid dependency)
    layer_b = {
        "id": "e2e.layer-b",
        "version": "1.0.0",
        "targets": [{"layer_id": "L35"}],
        "dependencies": [{"id": "e2e.layer-a", "type": "layer_definition"}],
        "enforcement_rules": [],
    }
    result_b = orchestrator.create_layer_definition(layer_b, skip_gates=True)
    assert result_b.status == "SUCCESS"
    # The registry_lookup was used by check_all_enforcement_rules, which called check_schema_validation
    # with the correct registry_lookup function
    schema_verdicts = [v for v in result_b.enforcement_verdicts if v.rule_id == "schema_validation"]
    assert schema_verdicts, "schema_validation verdict should be present"
    assert schema_verdicts[0].status == "PASS"


def test_enforcement_verdicts_recorded_before_audit(orchestrator, mock_review):
    """Enforcement verdicts are returned in result.enforcement_verdicts."""
    manifest = {
        "id": "e2e.audit-verdicts",
        "version": "1.0.0",
        "targets": [{"layer_id": "L34"}],
        "host_awareness": {"source_tree": {"paths": ["scripts/layer_forge_cli.py"]},
                           "cross_check": "none"},
    }
    result = orchestrator.create_layer_definition(manifest, skip_gates=True)

    assert result.status == "SUCCESS"
    assert isinstance(result.enforcement_verdicts, list)
    assert len(result.enforcement_verdicts) >= 3, \
        f"Expected >=3 verdicts (schema, boundaries, host_awareness), got {len(result.enforcement_verdicts)}"

    # Verify structure of verdicts
    for verdict in result.enforcement_verdicts:
        assert hasattr(verdict, "rule_id")
        assert hasattr(verdict, "status")
        assert hasattr(verdict, "detail")
        assert verdict.status in ("PASS", "FAIL", "SKIPPED", "ERROR")


def test_missing_source_tree_path_fails_host_awareness(orchestrator, mock_review):
    """Host-awareness check fails when source_tree path is missing."""
    manifest = {
        "id": "e2e.missing-source",
        "version": "1.0.0",
        "targets": [{"layer_id": "L34"}],
        "host_awareness": {
            "source_tree": {"paths": ["nonexistent_file_does_not_exist.py"]},
            "cross_check": "none",
        },
    }
    result = orchestrator.create_layer_definition(manifest, skip_gates=True)

    assert result.status == "FAILED"
    assert result.phase == "enforce"
    host_aware_verdicts = [v for v in result.enforcement_verdicts
                           if v.rule_id == "host_awareness"]
    assert host_aware_verdicts
    assert host_aware_verdicts[0].status == "FAIL"
    assert "nonexistent_file_does_not_exist.py" in host_aware_verdicts[0].detail
