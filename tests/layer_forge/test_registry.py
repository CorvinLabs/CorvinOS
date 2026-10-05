"""Unit tests for LayerRegistry — create/promote/get lifecycle."""
import pytest

from core.orchestration.layer_forge.registry import (
    LayerNotFoundError,
    LayerPromotionError,
    LayerRegistry,
)


def _manifest(**overrides):
    base = {"id": "test.rule", "version": "0.1.0", "targets": [{"layer_id": "L34"}]}
    base.update(overrides)
    return base


def test_create_then_get(tmp_path):
    reg = LayerRegistry(tmp_path)
    key = reg.create(_manifest())
    assert key == "test.rule@0.1.0"
    entry = reg.get("test.rule")
    assert entry["status"] == "proposed"


def test_create_duplicate_version_rejected(tmp_path):
    reg = LayerRegistry(tmp_path)
    reg.create(_manifest())
    with pytest.raises(LayerPromotionError):
        reg.create(_manifest())


def test_get_unknown_raises(tmp_path):
    reg = LayerRegistry(tmp_path)
    with pytest.raises(LayerNotFoundError):
        reg.get("ghost")


def test_promote_proposed_to_accepted(tmp_path):
    reg = LayerRegistry(tmp_path)
    reg.create(_manifest())
    record = reg.promote("test.rule", "0.1.0", "accepted")
    assert record["status"] == "accepted"


def test_promote_invalid_transition_rejected(tmp_path):
    reg = LayerRegistry(tmp_path)
    reg.create(_manifest())
    with pytest.raises(LayerPromotionError):
        reg.promote("test.rule", "0.1.0", "deployed")  # must go through accepted first


def test_promote_full_lifecycle(tmp_path):
    reg = LayerRegistry(tmp_path)
    reg.create(_manifest())
    reg.promote("test.rule", "0.1.0", "accepted")
    record = reg.promote("test.rule", "0.1.0", "deployed")
    assert record["status"] == "deployed"
    with pytest.raises(LayerPromotionError):
        reg.promote("test.rule", "0.1.0", "proposed")  # deployed is one-way


def test_dependency_on_deployed_entry_resolves(tmp_path):
    reg = LayerRegistry(tmp_path)
    reg.create(_manifest(id="base.rule"))
    reg.create(_manifest(id="dependent.rule", dependencies=[{"id": "base.rule", "type": "layer_definition"}]))
    assert reg.get("dependent.rule")["dependencies"][0]["id"] == "base.rule"


def test_dependency_on_unknown_entry_rejected(tmp_path):
    reg = LayerRegistry(tmp_path)
    from core.orchestration.layer_forge.schema import LayerDependencyDAGError
    with pytest.raises(LayerDependencyDAGError):
        reg.create(_manifest(dependencies=[{"id": "ghost.rule", "type": "layer_definition"}]))


def test_latest_version_is_semver_not_lexical(tmp_path):
    reg = LayerRegistry(tmp_path)
    reg.create(_manifest(version="0.9.0"))
    reg.create(_manifest(version="0.10.0"))
    assert reg.get("test.rule")["version"] == "0.10.0"


@pytest.mark.parametrize("entry_id", ["../x", "*", "a/b", "x\n"])
def test_unsafe_ids_are_not_found_never_path_resolved(tmp_path, entry_id):
    reg = LayerRegistry(tmp_path)
    with pytest.raises(LayerNotFoundError):
        reg.get(entry_id)
    with pytest.raises(LayerNotFoundError):
        reg.promote(entry_id, "0.1.0", "accepted")
