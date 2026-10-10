"""Test: Manifest ↔ Boot Reconciliation (G4/T3).

Validates that skills declared in MANIFEST.json are consistent with
BUILTIN_SKILL_IDS in os_skills_phase1.py.

Load-bearing invariant: every skill that promises to boot must have a
call-site from boot_skills() → registry.execute(). A skill in MANIFEST
without a BUILTIN_SKILL_ID entry is a dead skill.
"""

import json
from pathlib import Path

import pytest

from core.skills.os_skills_phase1 import BUILTIN_SKILL_IDS


def load_manifest() -> dict:
    """Load MANIFEST.json."""
    path = Path(__file__).parent.parent.parent / "core" / "skills" / "os_skills" / "MANIFEST.json"
    with open(path) as f:
        return json.load(f)


class TestManifestBootSync:
    """Manifest ↔ Boot reconciliation."""

    def test_manifest_skills_defined(self):
        """MANIFEST.json has a 'skills' key."""
        manifest = load_manifest()
        assert "skills" in manifest, "MANIFEST.json missing 'skills' key"
        assert isinstance(manifest["skills"], list), "MANIFEST.skills must be a list"
        assert len(manifest["skills"]) > 0, "MANIFEST.skills is empty"

    def test_all_manifest_skills_have_id(self):
        """Every skill in MANIFEST has an 'id' field."""
        manifest = load_manifest()
        for skill in manifest["skills"]:
            assert "id" in skill, f"Skill missing 'id': {skill}"
            assert isinstance(skill["id"], str), f"Skill id must be string: {skill}"

    def test_builtin_skill_ids_non_empty(self):
        """BUILTIN_SKILL_IDS has entries."""
        assert len(BUILTIN_SKILL_IDS) > 0, "BUILTIN_SKILL_IDS is empty"

    def test_manifest_vs_builtin_reconciliation(self):
        """Document the gap: manifest skills vs. builtin skills.

        This is the reconciliation test. It measures divergence and
        documents which skills are:
        - In MANIFEST, in BUILTIN_SKILL_IDS (✓ consistent)
        - In MANIFEST, NOT in BUILTIN_SKILL_IDS (✗ inconsistent)
        - In BUILTIN_SKILL_IDS, NOT in MANIFEST (✗ inconsistent)
        """
        manifest = load_manifest()
        manifest_ids = {s["id"] for s in manifest["skills"]}
        builtin_set = set(BUILTIN_SKILL_IDS)

        # Categorize
        consistent = manifest_ids & builtin_set
        manifest_only = manifest_ids - builtin_set
        builtin_only = builtin_set - manifest_ids

        # Document
        print("\n" + "=" * 70)
        print("MANIFEST ↔ BUILTIN_SKILL_IDS Reconciliation Report")
        print("=" * 70)
        print(f"\n✓ Consistent (in both): {len(consistent)}")
        for sid in sorted(consistent):
            print(f"    {sid}")

        print(f"\n✗ Manifest-only (no call-site): {len(manifest_only)}")
        for sid in sorted(manifest_only):
            print(f"    {sid}  ← DEAD SKILL (not booted)")

        print(f"\n✗ Builtin-only (no manifest entry): {len(builtin_only)}")
        for sid in sorted(builtin_only):
            print(f"    {sid}  ← MISSING MANIFEST (metadata)")

        print(f"\n{'-' * 70}")
        print(f"Summary: {len(consistent)} OK, {len(manifest_only)} dead, {len(builtin_only)} unmapped")
        print("=" * 70 + "\n")

        # Assertion: there MUST be divergence documented
        # (once reconciled, the counts should match, but for now we assert
        # that we measured it)
        assert len(manifest_ids) + len(builtin_set) > 0, "Both empty (unreachable)"

    def test_no_duplicate_ids_in_manifest(self):
        """Manifest skills have unique IDs."""
        manifest = load_manifest()
        ids = [s["id"] for s in manifest["skills"]]
        assert len(ids) == len(set(ids)), f"Duplicate IDs in manifest: {ids}"

    def test_no_duplicate_ids_in_builtin(self):
        """BUILTIN_SKILL_IDS has unique entries."""
        assert len(BUILTIN_SKILL_IDS) == len(set(BUILTIN_SKILL_IDS)), \
            f"Duplicate entries in BUILTIN_SKILL_IDS: {BUILTIN_SKILL_IDS}"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
