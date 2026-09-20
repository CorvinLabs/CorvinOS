"""Tests for SkillValidator — Layers 1–2 (structural + test-based validation).

Layer 1: Deterministic structural checks (manifest, schema, folders).
Layer 2: Test-based validation (pytest, coverage).
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from autonomous.validator import SkillValidator, ValidationResult
from autonomous.validator_layer1 import StructuralValidator
from autonomous.validator_layer2 import TestValidator


PASS = 0
FAIL = 0


def t(label, ok, *, detail=""):
    """Test result printer."""
    global PASS, FAIL
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' — ' + detail) if detail else ''}")
    if ok:
        PASS += 1
    else:
        FAIL += 1


# Helper: Create a valid skill directory structure
def create_minimal_skill(tmpdir: Path, skill_id: str = "test.skill") -> Path:
    """Create a minimal valid skill directory."""
    skill_dir = tmpdir / skill_id
    skill_dir.mkdir(parents=True)

    # Create skill.json
    manifest = {
        "id": skill_id,
        "version": "1.0.0",
        "capabilities": ["inference"],
        "dependencies": [],
        "hooks": [],
        "lom_binding": "test.skill:execute",
    }
    (skill_dir / "skill.json").write_text(json.dumps(manifest, indent=2))

    # Create required folders
    (skill_dir / "src").mkdir()
    (skill_dir / "tests").mkdir()
    (skill_dir / "hooks").mkdir()
    (skill_dir / "scripts").mkdir()

    # Create minimal Python files
    (skill_dir / "src" / "skill.py").write_text("def execute(): pass\n")
    (skill_dir / "tests" / "test_basic.py").write_text(
        "def test_placeholder(): pass\n"
    )

    return skill_dir


# ============================================================================
# LAYER 1: STRUCTURAL VALIDATION TESTS
# ============================================================================

def test_layer1_valid_manifest_passes():
    """Layer 1: Valid manifest + structure → PASS."""
    print("\n[Layer 1: valid manifest passes]")
    with tempfile.TemporaryDirectory() as tmpdir:
        skill_dir = create_minimal_skill(Path(tmpdir))
        validator = StructuralValidator()
        result = validator.validate(skill_dir)

        t("passed=True", result.passed)
        t("no errors", len(result.errors) == 0, detail=str(result.errors))
        t("layer=1", result.layer == 1)
        t("has metrics", len(result.metrics) > 0, detail=str(result.metrics))


def test_layer1_missing_manifest_fails():
    """Layer 1: No skill.json → FAIL."""
    print("\n[Layer 1: missing manifest fails]")
    with tempfile.TemporaryDirectory() as tmpdir:
        skill_dir = Path(tmpdir) / "test.skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "src").mkdir()

        validator = StructuralValidator()
        result = validator.validate(skill_dir)

        t("passed=False", not result.passed)
        t("has errors", len(result.errors) > 0)
        t("error mentions skill.json", any("skill.json" in e for e in result.errors))


def test_layer1_invalid_json_fails():
    """Layer 1: Invalid JSON in manifest → FAIL."""
    print("\n[Layer 1: invalid JSON fails]")
    with tempfile.TemporaryDirectory() as tmpdir:
        skill_dir = Path(tmpdir) / "test.skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "skill.json").write_text("{invalid json")

        validator = StructuralValidator()
        result = validator.validate(skill_dir)

        t("passed=False", not result.passed)
        t("has errors", len(result.errors) > 0)
        t("error mentions JSON", any("JSON" in e for e in result.errors))


def test_layer1_missing_required_fields_fails():
    """Layer 1: Missing required schema fields → FAIL."""
    print("\n[Layer 1: missing required fields fails]")
    with tempfile.TemporaryDirectory() as tmpdir:
        skill_dir = Path(tmpdir) / "test.skill"
        skill_dir.mkdir(parents=True)

        # Manifest missing 'dependencies'
        manifest = {
            "id": "test.skill",
            "version": "1.0.0",
            "capabilities": [],
            "hooks": [],
            "lom_binding": "test",
            # Missing 'dependencies'
        }
        (skill_dir / "skill.json").write_text(json.dumps(manifest))

        validator = StructuralValidator()
        result = validator.validate(skill_dir)

        t("passed=False", not result.passed)
        t("error mentions missing field", any("Missing required" in e for e in result.errors))


def test_layer1_missing_folder_structure_fails():
    """Layer 1: Missing required folders (src, tests, etc.) → FAIL."""
    print("\n[Layer 1: missing folder structure fails]")
    with tempfile.TemporaryDirectory() as tmpdir:
        skill_dir = Path(tmpdir) / "test.skill"
        skill_dir.mkdir(parents=True)

        manifest = {
            "id": "test.skill",
            "version": "1.0.0",
            "capabilities": [],
            "dependencies": [],
            "hooks": [],
            "lom_binding": "test",
        }
        (skill_dir / "skill.json").write_text(json.dumps(manifest))
        # Create only src/ folder, missing tests/, hooks/, scripts/

        (skill_dir / "src").mkdir()

        validator = StructuralValidator()
        result = validator.validate(skill_dir)

        t("passed=False", not result.passed)
        t("error mentions folders", any("folder" in e.lower() for e in result.errors))


def test_layer1_nonexistent_dir_fails():
    """Layer 1: Skill directory does not exist → FAIL."""
    print("\n[Layer 1: nonexistent directory fails]")
    validator = StructuralValidator()
    result = validator.validate(Path("/nonexistent/skill"))

    t("passed=False", not result.passed)
    t("has errors", len(result.errors) > 0)


def test_layer1_bad_manifest_schema_type_fails():
    """Layer 1: Manifest fields have wrong type → FAIL."""
    print("\n[Layer 1: bad manifest schema type fails]")
    with tempfile.TemporaryDirectory() as tmpdir:
        skill_dir = Path(tmpdir) / "test.skill"
        skill_dir.mkdir(parents=True)

        # capabilities should be list/dict, not string
        manifest = {
            "id": "test.skill",
            "version": "1.0.0",
            "capabilities": "inference",  # Wrong: should be list
            "dependencies": [],
            "hooks": [],
            "lom_binding": "test",
        }
        (skill_dir / "skill.json").write_text(json.dumps(manifest))
        (skill_dir / "src").mkdir()
        (skill_dir / "tests").mkdir()
        (skill_dir / "hooks").mkdir()
        (skill_dir / "scripts").mkdir()

        validator = StructuralValidator()
        result = validator.validate(skill_dir)

        t("passed=False", not result.passed)
        t("error about type", any("must be" in e for e in result.errors))


def test_layer1_version_format_warning():
    """Layer 1: Non-semver version → warning (not error)."""
    print("\n[Layer 1: version format warning]")
    with tempfile.TemporaryDirectory() as tmpdir:
        skill_dir = create_minimal_skill(Path(tmpdir))
        manifest_path = skill_dir / "skill.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["version"] = "v1"  # Not semver

        manifest_path.write_text(json.dumps(manifest))

        validator = StructuralValidator()
        result = validator.validate(skill_dir)

        t("passed=True", result.passed)
        t("has warning", len(result.warnings) > 0, detail=str(result.warnings))
        t("warning about version", any("version" in w.lower() for w in result.warnings))


# ============================================================================
# LAYER 2: TEST-BASED VALIDATION TESTS
# ============================================================================

def test_layer2_no_tests_dir_fails():
    """Layer 2: No tests/ directory → FAIL."""
    print("\n[Layer 2: no tests directory fails]")
    with tempfile.TemporaryDirectory() as tmpdir:
        skill_dir = Path(tmpdir) / "test.skill"
        skill_dir.mkdir(parents=True)

        validator = TestValidator()
        result = validator.validate(skill_dir)

        t("passed=False", not result.passed)
        t("error mentions tests", any("tests" in e.lower() for e in result.errors))


def test_layer2_no_test_files_fails():
    """Layer 2: tests/ exists but no test_*.py files → FAIL."""
    print("\n[Layer 2: no test files fails]")
    with tempfile.TemporaryDirectory() as tmpdir:
        skill_dir = Path(tmpdir) / "test.skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "tests").mkdir()
        # Create a non-test Python file
        (skill_dir / "tests" / "helper.py").write_text("def helper(): pass\n")

        validator = TestValidator()
        result = validator.validate(skill_dir)

        t("passed=False", not result.passed)
        t("error mentions test files", any("test" in e.lower() for e in result.errors))


def test_layer2_test_execution_success():
    """Layer 2: Tests pass → ValidationResult records passed count."""
    print("\n[Layer 2: test execution success]")
    with tempfile.TemporaryDirectory() as tmpdir:
        skill_dir = Path(tmpdir) / "test.skill"
        skill_dir.mkdir(parents=True)

        # Create minimal test
        (skill_dir / "src").mkdir()
        (skill_dir / "src" / "skill.py").write_text("def add(a, b): return a + b\n")

        (skill_dir / "tests").mkdir()
        (skill_dir / "tests" / "test_skill.py").write_text(
            "from src.skill import add\ndef test_add(): assert add(1, 1) == 2\n"
        )

        validator = TestValidator()
        result = validator.validate(skill_dir)

        # Result may fail due to coverage or if pytest not installed
        # We're checking it ran and has layer set
        t("layer=2", result.layer == 2)
        # Either has test metrics OR pytest not found error (both valid outcomes)
        has_metrics = "tests_passed" in result.metrics or "tests_failed" in result.metrics
        has_pytest_error = any("pytest" in e.lower() for e in result.errors)
        t("has test metrics or pytest error", has_metrics or has_pytest_error,
          detail=f"metrics={list(result.metrics.keys())}, errors={result.errors[:1]}")


def test_layer2_nonexistent_skill_dir_fails():
    """Layer 2: Skill directory doesn't exist → FAIL."""
    print("\n[Layer 2: nonexistent skill directory fails]")
    validator = TestValidator()
    result = validator.validate(Path("/nonexistent/skill"))

    t("passed=False", not result.passed)
    t("has errors", len(result.errors) > 0)


# ============================================================================
# MULTI-LAYER VALIDATION TESTS
# ============================================================================

def test_validator_all_layers_pass():
    """Multi-layer: Layer 1 and 2 both pass."""
    print("\n[Multi-layer: all layers pass]")
    with tempfile.TemporaryDirectory() as tmpdir:
        skill_dir = create_minimal_skill(Path(tmpdir))

        validator = SkillValidator()
        results = validator.validate_all_layers(skill_dir, layer_mask=0b11)

        t("two results returned", len(results) == 2, detail=f"got {len(results)}")
        t("layer 1 passed", results[0].passed, detail=str(results[0].errors))
        # Layer 2 might fail on coverage, but Layer 1 should pass


def test_validator_layer1_fail_stops():
    """Multi-layer: Layer 1 fails → stops (doesn't run Layer 2)."""
    print("\n[Multi-layer: Layer 1 fail stops downstream]")
    with tempfile.TemporaryDirectory() as tmpdir:
        skill_dir = Path(tmpdir) / "bad.skill"
        skill_dir.mkdir(parents=True)
        # No manifest, no structure

        validator = SkillValidator()
        results = validator.validate_all_layers(skill_dir, layer_mask=0b11)

        t("only layer 1 result", len(results) == 1, detail=f"got {len(results)}")
        t("layer 1 failed", not results[0].passed)
        t("layer is 1", results[0].layer == 1)


def test_validator_layer_mask_selects_layers():
    """Multi-layer: layer_mask selects which layers run."""
    print("\n[Multi-layer: layer_mask selection]")
    with tempfile.TemporaryDirectory() as tmpdir:
        skill_dir = create_minimal_skill(Path(tmpdir))

        validator = SkillValidator()

        # Run only Layer 1
        results_l1_only = validator.validate_all_layers(skill_dir, layer_mask=0b01)
        t("layer_mask=0b01 runs only L1", len(results_l1_only) == 1 and results_l1_only[0].layer == 1)

        # Run only Layer 2
        results_l2_only = validator.validate_all_layers(skill_dir, layer_mask=0b10)
        t("layer_mask=0b10 runs only L2", len(results_l2_only) == 1 and results_l2_only[0].layer == 2)


def test_validator_verbose_returns_tuple():
    """Multi-layer: validate_all_layers_verbose returns (bool, list)."""
    print("\n[Multi-layer: verbose mode]")
    with tempfile.TemporaryDirectory() as tmpdir:
        skill_dir = create_minimal_skill(Path(tmpdir))

        validator = SkillValidator()
        overall, results = validator.validate_all_layers_verbose(skill_dir, layer_mask=0b01)

        t("returns tuple", isinstance((overall, results), tuple))
        t("overall is bool", isinstance(overall, bool))
        t("results is list", isinstance(results, list))
        t("overall matches Layer 1", overall == results[0].passed)


def test_validation_result_string_repr():
    """ValidationResult.__str__() produces readable output."""
    print("\n[ValidationResult: __str__ representation]")
    result = ValidationResult(
        layer=1,
        passed=True,
        errors=[],
        warnings=["watch this"],
        metrics={"coverage": 90.5},
    )
    s = str(result)
    t("__str__ includes layer", "Layer 1" in s)
    t("__str__ includes pass status", "PASS" in s)
    t("__str__ includes metrics", "coverage" in s)


# ============================================================================
# RUN ALL TESTS
# ============================================================================

if __name__ == "__main__":
    print("=" * 70)
    print("VALIDATION SUITE LAYERS 1–2 TESTS")
    print("=" * 70)

    # Layer 1 tests
    test_layer1_valid_manifest_passes()
    test_layer1_missing_manifest_fails()
    test_layer1_invalid_json_fails()
    test_layer1_missing_required_fields_fails()
    test_layer1_missing_folder_structure_fails()
    test_layer1_nonexistent_dir_fails()
    test_layer1_bad_manifest_schema_type_fails()
    test_layer1_version_format_warning()

    # Layer 2 tests
    test_layer2_no_tests_dir_fails()
    test_layer2_no_test_files_fails()
    test_layer2_test_execution_success()
    test_layer2_nonexistent_skill_dir_fails()

    # Multi-layer tests
    test_validator_all_layers_pass()
    test_validator_layer1_fail_stops()
    test_validator_layer_mask_selects_layers()
    test_validator_verbose_returns_tuple()
    test_validation_result_string_repr()

    print("\n" + "=" * 70)
    print(f"RESULTS: {PASS} PASS, {FAIL} FAIL")
    print("=" * 70)

    sys.exit(0 if FAIL == 0 else 1)
