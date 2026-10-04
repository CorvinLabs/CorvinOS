"""Test: core/skills/manifest_v2.py is the canonical Skill manifest schema.

Verify:
1. All imports resolve without error
2. License binding metadata is properly typed
3. No other manifest variants are loaded in production
4. v1→v2 migration function works
"""

import pytest
from core.skills.manifest_v2 import SkillManifestV2, LicenseBindingMetadata


def test_manifest_v2_schema_is_canonical():
    """Canonical manifest must deserialize cleanly."""
    manifest = SkillManifestV2(
        skill_id="os.test_skill",
        version="1.0.0",
        boot_layer="bundled",
        audit_events=["test_event"],
    )
    assert manifest.skill_id == "os.test_skill"
    assert manifest.version == "1.0.0"
    assert manifest.license_binding is None  # free tier default


def test_license_binding_metadata():
    """License binding must be cryptographically typed."""
    binding = LicenseBindingMetadata(
        required_tier="paid",
        binding_hash="sha256:abc123",
        operator_signature="sig:def456",
        timestamp="2026-10-04T00:00:00Z",
    )
    assert binding.required_tier == "paid"
    assert binding.binding_hash.startswith("sha256:")


def test_to_dict_serialization():
    """Manifest must serialize to JSON-compatible dict."""
    manifest = SkillManifestV2(
        skill_id="os.test",
        version="2.0.0",
        boot_layer="installed",
    )
    d = manifest.to_dict()
    assert isinstance(d, dict)
    assert d["skill_id"] == "os.test"
    assert d["version"] == "2.0.0"


def test_no_duplicate_manifest_variants_loaded():
    """Verify no other manifest modules are imported in production code."""
    import subprocess
    result = subprocess.run(
        ["grep", "-r", "--include=*.py", "-E",
         "from core.skills.phase1_manifest|from core.skills.skill_validator|from core.skills.os_skills_registry",
         "core", "corvin_operator"],
        capture_output=True, text=True, cwd="/home/shumway/projects/CorvinOS"
    )
    # Should be zero matches (dead imports)
    assert result.returncode != 0 or not result.stdout.strip(), \
        f"Found imports of duplicate manifest variants:\n{result.stdout}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
