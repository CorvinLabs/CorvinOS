"""Unit tests for core.orchestration.layer_forge.schema."""
import pytest

from core.orchestration.layer_forge.schema import (
    LayerDependencyDAGError,
    LayerSchemaValidationError,
    validate_dependency_dag,
    validate_manifest,
)


def _valid_manifest(**overrides):
    base = {
        "id": "test.layer-rule",
        "version": "0.1.0",
        "targets": [{"layer_id": "L34"}],
    }
    base.update(overrides)
    return base


def test_valid_manifest_passes():
    validate_manifest(_valid_manifest())


def test_missing_required_field_rejected():
    manifest = _valid_manifest()
    del manifest["version"]
    with pytest.raises(LayerSchemaValidationError):
        validate_manifest(manifest)


def test_empty_targets_rejected():
    with pytest.raises(LayerSchemaValidationError):
        validate_manifest(_valid_manifest(targets=[]))


def test_bad_id_format_rejected():
    with pytest.raises(LayerSchemaValidationError):
        validate_manifest(_valid_manifest(id="Not Valid!"))


def test_bad_version_format_rejected():
    with pytest.raises(LayerSchemaValidationError):
        validate_manifest(_valid_manifest(version="v1"))


def test_quality_gate_missing_test_path_rejected():
    manifest = _valid_manifest(quality_gates=[{"gate_id": "g1"}])
    with pytest.raises(LayerSchemaValidationError):
        validate_manifest(manifest)


def test_enforcement_rule_bad_type_rejected():
    manifest = _valid_manifest(
        enforcement_rules=[{"rule_id": "r1", "type": "whenever"}]
    )
    with pytest.raises(LayerSchemaValidationError):
        validate_manifest(manifest)


def test_dag_no_dependencies_ok():
    validate_dependency_dag("a", [], lambda x: None)


def test_dag_simple_chain_ok():
    graph = {"a": ["b"], "b": ["c"], "c": []}
    validate_dependency_dag("a", [{"id": "b"}], lambda x: graph.get(x))


def test_dag_cycle_detected():
    graph = {"a": ["b"], "b": ["a"]}
    with pytest.raises(LayerDependencyDAGError):
        validate_dependency_dag("a", [{"id": "b"}], lambda x: graph.get(x))


def test_dag_unresolvable_dependency_rejected():
    with pytest.raises(LayerDependencyDAGError):
        validate_dependency_dag("a", [{"id": "ghost"}], lambda x: None)
