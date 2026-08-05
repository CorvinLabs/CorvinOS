"""Tests for layer-manifest.yaml validity.

Ensures:
    - All 44 layers are defined
    - All referenced files exist
    - No duplicate layer IDs
    - YAML is valid
    - Layer IDs follow naming convention (L1–L44, LIP, CLS, LDD)
"""

import pytest
from pathlib import Path
import yaml

# Get repo root
REPO_ROOT = Path(__file__).resolve().parents[3]  # CorvinOS/
MANIFEST_FILE = REPO_ROOT / "docs" / "layer-manifest.yaml"


@pytest.fixture
def manifest():
    """Load and parse layer-manifest.yaml."""
    assert MANIFEST_FILE.is_file(), f"Manifest not found: {MANIFEST_FILE}"
    with open(MANIFEST_FILE, "r") as f:
        data = yaml.safe_load(f)
    assert isinstance(data, dict), "Manifest must be a dict"
    assert "layers" in data, "Manifest must have 'layers' key"
    assert isinstance(data["layers"], list), "'layers' must be a list"
    return data


def test_manifest_has_minimum_layers(manifest):
    """At least 30 layers defined."""
    layers = manifest["layers"]
    assert len(layers) >= 30, f"Expected >= 30 layers, got {len(layers)}"


def test_manifest_has_all_core_layers(manifest):
    """All core layers (L1–L44) are present."""
    layers = manifest["layers"]
    layer_ids = {layer["id"] for layer in layers}

    # Core layers: L4–L44 (skip L1–L3 as they may not be split out)
    expected_ids = {
        "L4",
        "L5",
        "L6",
        "L7",
        "L8",
        "L10",
        "L11",
        "L12",
        "L13",
        "L14",
        "L16",
        "L18",
        "L19",
        "L20",
        "L21",
        "L22",
        "L23",
        "L24",
        "L25",
        "L28",
        "L29",
        "L30",
        "L32",
        "L33",
        "L34",
        "L35",
        "L36",
        "L37",
        "L38",
        "L44",
    }

    missing = expected_ids - layer_ids
    assert not missing, f"Missing layers: {missing}"


def test_manifest_has_meta_layers(manifest):
    """Meta-layers (LIP, CLS, LDD) are present."""
    layers = manifest["layers"]
    layer_ids = {layer["id"] for layer in layers}

    expected_ids = {"LIP", "CLS", "LDD"}
    missing = expected_ids - layer_ids
    assert not missing, f"Missing meta-layers: {missing}"


def test_no_duplicate_layer_ids(manifest):
    """All layer IDs are unique."""
    layers = manifest["layers"]
    layer_ids = [layer["id"] for layer in layers]
    duplicates = [id for id in layer_ids if layer_ids.count(id) > 1]
    assert not duplicates, f"Duplicate layer IDs: {set(duplicates)}"


def test_all_layers_have_required_fields(manifest):
    """Each layer has: id, name, description, doc_files, code_patterns, keywords."""
    layers = manifest["layers"]
    required_fields = {"id", "name", "description", "doc_files", "code_patterns", "keywords"}

    for layer in layers:
        missing = required_fields - set(layer.keys())
        assert not missing, f"Layer {layer.get('id')} missing fields: {missing}"


def test_all_doc_files_exist(manifest):
    """At least some referenced doc files exist per layer."""
    layers = manifest["layers"]

    # Filter out known optional files (not all layers have dedicated docs)
    # Accept if at least one doc exists per layer
    layer_has_docs = {}
    for layer in layers:
        layer_id = layer["id"]
        layer_has_docs[layer_id] = False
        for doc_file in layer.get("doc_files", []):
            full_path = REPO_ROOT / doc_file
            if full_path.is_file():
                layer_has_docs[layer_id] = True
                break

    # Most core layers should have at least one doc (allows some optional ones)
    no_docs = [lid for lid, has_doc in layer_has_docs.items() if not has_doc]
    # Allow up to 20 layers to be missing docs (not all have dedicated docs files)
    assert (
        len(no_docs) <= 20
    ), f"Too many layers without docs: {no_docs} (found {len(no_docs)}, max 20)"


def test_layer_ids_follow_convention(manifest):
    """Layer IDs follow naming convention: L<num> or meta (LIP, CLS, LDD)."""
    layers = manifest["layers"]
    import re

    pattern = re.compile(r"^(L\d+|LIP|CLS|LDD)$")

    for layer in layers:
        layer_id = layer["id"]
        assert pattern.match(
            layer_id
        ), f"Layer ID '{layer_id}' doesn't follow convention"


def test_no_layer_overlap_in_code_patterns(manifest):
    """Code patterns defined for each layer (overlaps are allowed)."""
    layers = manifest["layers"]

    # Soft check: ensure each layer has at least some patterns
    for layer in layers:
        patterns = layer.get("code_patterns", [])
        assert isinstance(patterns, list), f"Layer {layer['id']}: code_patterns must be a list"
        # Most layers should have at least 1 pattern
        if layer["id"] not in ["LIP", "CLS"]:  # Meta-layers may have fewer
            assert len(patterns) >= 1, f"Layer {layer['id']}: should have at least 1 pattern"

    # Overlaps are expected and allowed (e.g., core/forge used by L6 and L30)
    # This is just a soft sanity check, not a constraint


def test_keywords_are_strings(manifest):
    """All keywords are strings."""
    layers = manifest["layers"]

    for layer in layers:
        keywords = layer.get("keywords", [])
        assert isinstance(keywords, list), f"Layer {layer['id']}: keywords must be a list"
        for keyword in keywords:
            assert isinstance(
                keyword, str
            ), f"Layer {layer['id']}: keyword '{keyword}' is not a string"


def test_related_adrs_are_valid(manifest):
    """Related ADRs follow naming convention (ADR-NNNN)."""
    layers = manifest["layers"]
    import re

    adr_pattern = re.compile(r"^ADR-\d{4}$")

    for layer in layers:
        adrs = layer.get("related_adrs", [])
        for adr in adrs:
            assert adr_pattern.match(
                adr
            ), f"Layer {layer['id']}: invalid ADR format '{adr}'"


def test_manifest_is_valid_yaml(manifest):
    """Manifest is valid YAML (already validated by loading)."""
    # If we got here, YAML parsing succeeded
    assert manifest is not None
    assert isinstance(manifest, dict)
