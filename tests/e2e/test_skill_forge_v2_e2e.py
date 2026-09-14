"""E2E Test: Skill Forge v2.0 Generator end-to-end."""

import sys
from pathlib import Path
import json
import tempfile

# Setup paths
corvinOS = Path(__file__).parent.parent.parent
sys.path.insert(0, str(corvinOS / "core" / "skill_forge"))

from generators.manifest import SkillManifest, SkillType, SkillScope
from generators.skeleton import SkeletonGenerator, generate_folder_structure
from generators.validator import ManifestValidator


def test_e2e_generate_and_validate():
    """E2E: Generate skill skeleton → Validate → Save to disk."""

    # Step 1: Generate skeleton
    gen = SkeletonGenerator(SkillType.LEARNED_EXPERIENCE)
    manifest = gen.generate(
        name="e2e_test_skill",
        title="E2E Test Skill",
        description="This is an end-to-end test skill to verify the full pipeline works correctly.",
        scope=SkillScope.TASK
    )

    # Verify generated manifest
    assert manifest.name == "e2e_test_skill"
    assert manifest.generator_phase == "skeleton"
    assert len(manifest.body_md) > 200
    assert "Pattern" in manifest.body_md
    print(f"✅ Step 1: Generated skeleton manifest ({len(manifest.body_md)} chars)")

    # Step 2: Validate
    validator = ManifestValidator()
    is_valid, errors, warnings = validator.validate(manifest)

    assert is_valid, f"Validation failed: {errors}"
    print(f"✅ Step 2: Validated manifest (errors={len(errors)}, warnings={len(warnings)})")

    # Step 3: Serialize to JSON
    json_str = manifest.to_json()
    d = json.loads(json_str)

    assert d["name"] == "e2e_test_skill"
    assert d["skill_type"] == "learned-experience"
    print(f"✅ Step 3: Serialized to JSON ({len(json_str)} bytes)")

    # Step 4: Deserialize and re-validate
    manifest2 = SkillManifest.from_dict(d)
    is_valid2, errors2, _ = validator.validate(manifest2)

    assert is_valid2, f"Re-validation failed: {errors2}"
    assert manifest2.name == manifest.name
    print(f"✅ Step 4: Deserialized and re-validated")

    # Step 5: Write to disk
    with tempfile.TemporaryDirectory() as tmpdir:
        skill_root = Path(tmpdir)

        # Generate folder structure
        skill_dir = generate_folder_structure(skill_root, manifest.name)

        # Write manifest
        manifest_file = skill_dir / ".forge" / "manifest.json"
        manifest_file.write_text(json_str)

        # Write skill body
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text(manifest.body_md)

        # Verify files exist
        assert manifest_file.exists()
        assert skill_file.exists()
        assert (skill_dir / "hooks").exists()
        assert (skill_dir / "tests").exists()

        # Read back and verify
        saved_json = manifest_file.read_text()
        saved_manifest = SkillManifest.from_dict(json.loads(saved_json))

        assert saved_manifest.name == manifest.name
        print(f"✅ Step 5: Written to disk and read back successfully")
        print(f"   - Structure: .forge/, hooks/, scripts/, tests/")
        print(f"   - Manifest: {manifest_file}")
        print(f"   - Body: {skill_file}")


def test_e2e_all_skill_types():
    """E2E: Generate all skill types and validate."""

    skill_types = [
        SkillType.LEARNED_EXPERIENCE,
        SkillType.REASONING,
        SkillType.REFERENCE,
        SkillType.AUTOMATION,
    ]

    for skill_type in skill_types:
        gen = SkeletonGenerator(skill_type)
        manifest = gen.generate(
            name=f"test_{skill_type.value.replace('-', '_')}",
            title=f"Test {skill_type.value}",
            description=f"Test skill of type {skill_type.value}.",
            scope=SkillScope.SESSION
        )

        validator = ManifestValidator()
        is_valid, errors, warnings = validator.validate(manifest)

        assert is_valid, f"{skill_type.value} validation failed: {errors}"
        assert manifest.generator_phase == "skeleton"
        assert manifest.skill_type == skill_type

        print(f"✅ {skill_type.value}: Generated and validated")


def test_e2e_invalid_manifest_rejected():
    """E2E: Invalid manifest is rejected."""

    # Manifest missing required sections
    invalid_manifest = SkillManifest(
        name="invalid_skill",
        skill_type=SkillType.LEARNED_EXPERIENCE,
        title="Invalid",
        description="Too short.",
        scope=SkillScope.TASK,
        body_md="Just a body, no required sections."
    )

    validator = ManifestValidator()
    is_valid, errors, warnings = validator.validate(invalid_manifest)

    assert not is_valid
    assert any("required section" in e.lower() for e in errors)
    print(f"✅ Invalid manifest rejected with {len(errors)} errors")


if __name__ == "__main__":
    print("Running E2E Tests: Skill Forge v2.0 Generator\n")
    print("="*60)

    test_e2e_generate_and_validate()
    print()
    test_e2e_all_skill_types()
    print()
    test_e2e_invalid_manifest_rejected()

    print("="*60)
    print("\n✅ ALL E2E TESTS PASSED")
